"""FastAPI viewer: streams a current-season match as if live, analyses it online, explains it.

Pages:  /            match viewer (pitch on the left, followed player on the right)
        /compare     head-to-head page (opened from the viewer's button)
        /glossary    short explanations of the football terms marked with "?"
API:    /api/matches /api/roster /api/player/{id} /health
Stream: /ws?match=<file>   (one independent, paced replay per browser tab)

Data: see app/storage.py (local `data/` folder or Azure Blob). History seasons come from
history.db (built offline); the current season is only ever analysed online.
"""
import asyncio
from contextlib import asynccontextmanager, suppress
import html
import json
import os
import time
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from team_player.schema import validate_metadata
from team_player.analyser.comparison import History, Accumulator, compare, FIELDS
from team_player.analyser.live import LivePipeline
from team_player.analyser.presentation import Preferences, local_overlay, overlay_is_current, verdicts
from team_player.analyser.situations import LiveSimilarity
from team_player.agents.narration import local_summary, similar_story, balance_from_row, defence_from_row
from team_player.app.storage import DataSource
from team_player.config.analysis import MIN_POSSESSION_S, GLOSSARY, SUPPORTED_ROLES
from team_player.config.league import TEAMS, LEAGUE_ROLES, CURRENT_SEASON, LEAGUE_FORMATION, player_id
from team_player.utils.io import open_text

WEB = Path(__file__).with_name('web')


def league_roster():
    return [{'id': player_id(t, LEAGUE_FORMATION, i), 'team': t, 'role': r} for t in TEAMS for i, r in enumerate(LEAGUE_ROLES)]


@asynccontextmanager
async def lifespan(app):
    app.state.director = None;app.state.agent_slots = asyncio.Semaphore(2)
    backend = os.environ.get('TEAM_PLAYER_AI', 'local')
    if backend not in ('local', 'foundry'):
        raise ValueError('TEAM_PLAYER_AI must be local or foundry')
    if backend == 'foundry':
        from team_player.agents.foundry import FoundryDirector
        app.state.director = FoundryDirector()
    app.state.data = DataSource();app.state.store = None;app.state.index = None;app.state.history_rows = {}
    db = await asyncio.to_thread(app.state.data.history_db)
    if db:
        from team_player.analyser.history import HistoryStore
        app.state.store = HistoryStore(db)
        app.state.index = await asyncio.to_thread(app.state.store.index)
        app.state.history_rows = await asyncio.to_thread(app.state.store.all_player_totals)
    yield
    if app.state.director:
        await app.state.director.close()

app = FastAPI(title='Team Player', lifespan=lifespan)


@app.get('/')
async def index():
    return FileResponse(WEB / 'index.html')


@app.get('/compare')
async def compare_page():
    return FileResponse(WEB / 'compare.html')


@app.get('/glossary')
async def glossary():
    items = ''.join(f'<section id="{html.escape(k)}"><h2>{html.escape(k)}</h2><p>{html.escape(v[0].upper() + v[1:])}.</p></section>'
                    for k, v in GLOSSARY.items())
    return HTMLResponse('<!doctype html><meta charset="utf-8"><title>Team Player · Glossary</title>'
                        '<style>body{background:#101a22;color:#e7eff5;font:16px system-ui;max-width:760px;margin:30px auto;padding:0 16px}'
                        'h2{color:#f3cc80;font-size:18px;margin-bottom:4px}section{border-top:1px solid #2b4248;padding:8px 0}'
                        'section:target{background:#172733;border-radius:8px;padding:8px}</style>'
                        f'<h1>Glossary</h1><p>Short explanations of the terms used in the viewer.</p>{items}')


@app.get('/health')
async def health():
    return {'status': 'ok', 'ai_backend': os.environ.get('TEAM_PLAYER_AI', 'local'),
            'history': app.state.store.summary() if app.state.store else None}


@app.get('/api/matches')
async def matches():
    return {'season': app.state.data.season, 'matches': await asyncio.to_thread(app.state.data.list_current)}


@app.get('/api/roster')
async def roster():
    return {'players': league_roster(), 'supported_roles': sorted(SUPPORTED_ROLES)}


@app.get('/api/player/{player}')
async def player_history(player: str):
    if not app.state.store:
        return JSONResponse({'error': 'No history store loaded'}, status_code=404)
    return await asyncio.to_thread(app.state.store.player_history, player)


async def bounded_agent_call(app, coroutine, timeout):
    """Run one agent request within the per-process slot limit and a deadline.
    Fails fast under overload; never queues unlimited model requests."""
    try:
        await asyncio.wait_for(app.state.agent_slots.acquire(), timeout=.1)
    except asyncio.TimeoutError:
        coroutine.close();raise RuntimeError('Agent capacity reached') from None
    try:
        return await asyncio.wait_for(coroutine, timeout=timeout)
    finally:
        app.state.agent_slots.release()


async def agent_job(app, snapshot):
    return await bounded_agent_call(app, app.state.director.explain(snapshot), 14)


def resolve_match(app, requested):
    if requested:
        return app.state.data.match_path(requested)
    if os.environ.get('TEAM_PLAYER_MATCH'):
        return Path(os.environ['TEAM_PLAYER_MATCH'])
    available = app.state.data.list_current()
    if available:
        return app.state.data.match_path(available[0]['file'])
    return Path('matches/match_0000.jsonl')


def comparison_rows(app, path, meta, accumulator):
    """History rows for the head-to-head figures: history.db when built, else the legacy
    comparison_history.json beside the recording (which already includes this match so far)."""
    if app.state.store:
        rows = {p['id']: {**p, **dict.fromkeys(FIELDS, 0.), 'matches': 0} for p in league_roster()}
        rows.update(app.state.history_rows)
        for p in meta['players']:
            rows.setdefault(p['id'], {'id': p['id'], 'team': p['team'], 'role': p['role'], **dict.fromkeys(FIELDS, 0.)})
        return lambda: rows
    legacy = History(path.parent, path)
    return lambda: legacy.rows(accumulator)


@app.websocket('/ws')
async def stream(ws: WebSocket):
    # Browser access is same-origin. Remote deployment should additionally enable App Service Authentication.
    origin = ws.headers.get('origin')
    if origin and origin.split('://', 1)[-1] != ws.headers.get('host'):
        await ws.close(code=1008);return
    await ws.accept()
    try:
        path = resolve_match(ws.app, ws.query_params.get('match') if hasattr(ws, 'query_params') else None)
    except Exception:
        path = None
    if not path or not path.is_file():
        await ws.send_json({'kind': 'error', 'message': 'No match available. Generate current-season matches or set TEAM_PLAYER_MATCH.'})
        await ws.close();return
    state = {'paused': False, 'speed': 2., 'revision': 0, 'closed': False}
    pending = reader_task = comparison_pending = similar_pending = None
    overlay = None;last_agent = -100.;last_status = 'Local evidence rules — no AI model'
    comparison_revision = -1;comparison_result = None
    try:
        with open_text(path) as handle:
            meta = json.loads(next(handle));validate_metadata(meta)
            ids = {p['id']: p for p in meta['players']}
            fallback = next(p['id'] for p in meta['players'] if 'ST' in p['role'])
            requested = ws.query_params.get('player') if hasattr(ws, 'query_params') else None
            requested = requested or os.environ.get('TEAM_PLAYER_FAVOURITE', fallback)
            preferences = Preferences(player=requested if requested in ids else fallback);preferences.validate(meta)
            accumulator = Accumulator(meta);rows_of = await asyncio.to_thread(comparison_rows, ws.app, path, meta, accumulator)
            rows = rows_of()
            similarity = LiveSimilarity(ws.app.state.index) if ws.app.state.index is not None and len(ws.app.state.index) else None
            similar_result = similar_review = story = None;similar_changed = False
            peer = next((p for p in rows if p != preferences.player and rows[p]['role'] == ids[preferences.player]['role']), None) or \
                next(p['id'] for p in meta['players'] if p['id'] != preferences.player)
            state.update(left=preferences.player, right=peer, comparison_revision=0, review=False)
            comparison_snapshot = compare(rows, state['left'], state['right'], 0, accumulator.rows)
            await ws.send_json({'kind': 'metadata', 'metadata': meta, 'preferences': preferences.__dict__, 'file': path.name,
                                'season': meta.get('season', CURRENT_SEASON), 'roster': [{k: p[k] for k in ('id', 'team', 'role')} for p in rows.values()],
                                'comparison': comparison_snapshot, 'ai_backend': 'foundry' if ws.app.state.director else 'local',
                                'history': ws.app.state.store.summary() if ws.app.state.store else None,
                                'glossary_terms': list(GLOSSARY)})

            async def receive_controls():
                nonlocal preferences
                try:
                    while True:
                        message = await ws.receive_json()
                        if message.get('type') == 'preferences':
                            candidate = Preferences(player=message.get('player', preferences.player), audience=message.get('audience', preferences.audience),
                                                    focus=message.get('focus', preferences.focus))
                            candidate.validate(meta);preferences = candidate;state['revision'] += 1;state['restory'] = True
                        elif message.get('type') == 'comparison':
                            left = message.get('left', state['left']);right = message.get('right', state['right'])
                            if left not in rows or right not in rows:
                                raise ValueError('Unknown comparison player')
                            state.update(left=left, right=right, review=bool(message.get('review', False)))
                            state['comparison_revision'] += 1
                        elif message.get('type') == 'similar_review':
                            state['similar_review'] = True
                        elif message.get('type') == 'playback':
                            speed = float(message.get('speed', state['speed']))
                            if speed not in (1, 2, 4, 8):
                                raise ValueError('Unsupported playback speed')
                            state['speed'] = speed;state['paused'] = bool(message.get('paused', False))
                except (WebSocketDisconnect, ValueError, TypeError):
                    state['closed'] = True
            reader_task = asyncio.create_task(receive_controls())
            pipeline = LivePipeline(meta, float(os.environ.get('TEAM_PLAYER_MIN_POSSESSION', MIN_POSSESSION_S)))
            previous = None;agent_revision = -1;deadline = time.monotonic();overlay_revision = -1;last_rows = -10.
            for line in handle:
                if state['closed']:
                    break
                while state['paused'] and not state['closed']:
                    await asyncio.sleep(.05);deadline = time.monotonic()
                if state['closed']:
                    break
                raw = json.loads(line)
                if previous is not None:
                    deadline += (raw['t'] - previous) / state['speed']
                    await asyncio.sleep(max(0, deadline - time.monotonic()))
                previous = raw['t'];t = raw['t']
                packet = await asyncio.to_thread(pipeline.update, raw, preferences)
                gate = packet['possession']
                accumulator.update(packet['frame'], packet['analysis'], packet.get('tactics'))
                focus = ids[preferences.player]
                # Similar history moments: query on each new decision window, then tell the story
                # from the followed player's point of view.
                if similarity:
                    found = similarity.update(packet['tactics'])
                    if found:
                        similar_result = found;similar_changed = True
                        similar_review = {'method': 'local_rules', 'advice': found.get('advice', []), 'warnings': found.get('warnings', []),
                                          'summary': local_summary('similar', found, preferences.audience)}
                    if found or state.pop('restory', False):
                        new = similar_story(similar_result, focus['id'], focus['role'], focus['team'])
                        if new or found:
                            story = new;similar_changed = True
                if similar_pending and similar_pending.done():
                    try:
                        similar_review = similar_pending.result()
                    except Exception:
                        similar_review = {'method': 'local_rules', 'advice': [], 'warnings': ['AI consultant unavailable; showing measured neighbours only.']}
                    similar_pending = None;similar_changed = True
                if state.pop('similar_review', False) and similar_result and similar_result.get('available') and not similar_pending:
                    if ws.app.state.director:
                        similar_pending = asyncio.create_task(bounded_agent_call(ws.app, ws.app.state.director.similar(similar_result, preferences.audience), 12))
                    else:
                        similar_review = {'method': 'local_rules', 'advice': similar_result['advice'], 'warnings': similar_result['warnings'],
                                          'summary': local_summary('similar', similar_result, preferences.audience)};similar_changed = True
                # Head-to-head: history figures plus this match so far.
                comparison_snapshot = compare(rows_of(), state['left'], state['right'], t, accumulator.rows)
                if comparison_pending and comparison_pending.done():
                    try:
                        result = comparison_pending.result()
                        if comparison_revision == state['comparison_revision']:
                            comparison_result = result
                    except Exception:
                        comparison_result = {'method': 'local_rules', 't': t, 'advice': [], 'warnings': ['AI consultant unavailable; local suitability checks remain active.']}
                    comparison_pending = None
                if comparison_revision != state['comparison_revision']:
                    comparison_result = None
                if state['review'] and not comparison_pending:
                    state['review'] = False;comparison_revision = state['comparison_revision']
                    if ws.app.state.director:
                        comparison_pending = asyncio.create_task(bounded_agent_call(ws.app, ws.app.state.director.compare(comparison_snapshot, preferences.audience), 12))
                    else:
                        comparison_result = {'method': 'local_rules', 't': t, 'advice': comparison_snapshot['advice'], 'warnings': comparison_snapshot['warnings'],
                                             'summary': local_summary('comparison', comparison_snapshot, preferences.audience)}
                # Overlay: local evidence immediately, Foundry review asynchronously.
                if pending and pending.done():
                    try:
                        result = pending.result()
                        if agent_revision == state['revision'] and gate['eligible'] and overlay_is_current(result, t, gate['episode_id'], preferences):
                            overlay = result;overlay_revision = state['revision'];last_status = 'Foundry agents reviewed the evidence'
                        else:
                            last_status = 'Late or outdated AI result discarded'
                    except Exception as exc:
                        last_status = 'AI unavailable (' + type(exc).__name__ + '); showing local evidence rules'
                    pending = None
                snapshot = packet.pop('snapshot')
                if snapshot:
                    snapshot['similar_story'] = story
                    snapshot['balance'] = balance_from_row(accumulator.rows.get(preferences.player)) if focus['role'] in SUPPORTED_ROLES else None
                    snapshot['defence'] = defence_from_row(accumulator.rows.get(preferences.player))
                    if overlay_revision != state['revision'] or not overlay_is_current(overlay, t, gate['episode_id'], preferences) or overlay['selection_method'] == 'local_rules':
                        overlay = local_overlay(snapshot);overlay_revision = state['revision']
                    if ws.app.state.director and snapshot['cards'] and snapshot['eligible'] and not pending and t - last_agent >= 8:
                        pending = asyncio.create_task(agent_job(ws.app, snapshot));last_agent = t;agent_revision = state['revision']
                        last_status = 'Foundry agents reviewing; local evidence shown meanwhile'
                if overlay_revision != state['revision'] or not gate['eligible'] or not overlay_is_current(overlay, t, gate['episode_id'], preferences):
                    overlay = None
                if agent_revision != state['revision'] and overlay and overlay['selection_method'] == 'microsoft_foundry_agents':
                    overlay = None
                extra = {'verdicts': verdicts(preferences.player, pipeline.episodes, t), 'unsupported': focus['role'] not in SUPPORTED_ROLES,
                         'player_live': accumulator.rows.get(preferences.player),
                         'balance': balance_from_row(accumulator.rows.get(preferences.player)) if focus['role'] in SUPPORTED_ROLES else None,
                         'defence': defence_from_row(accumulator.rows.get(preferences.player))}
                if similar_changed:
                    extra.update(similar=similar_result, similar_review=similar_review, story=story);similar_changed = False
                if t - last_rows >= 1:   # this-match rows for the head-to-head page, once per second
                    extra['match_rows'] = list(accumulator.rows.values());last_rows = t
                await ws.send_json({'kind': 'tick', **extra, 'comparison': comparison_snapshot, 'consultant': comparison_result,
                                    'comparison_pending': bool(comparison_pending), **packet, 'overlay': overlay, 'agent_status': last_status,
                                    'pending_ai': bool(pending), 'preferences': preferences.__dict__,
                                    'ingest_lag_ms': round(max(0, time.monotonic() - deadline) * 1000, 2)})
            if not state['closed']:
                await ws.send_json({'kind': 'end', 'message': 'Full time. Reload or pick another match to replay.'})
    except WebSocketDisconnect:
        pass
    except (ValueError, KeyError, StopIteration) as exc:
        with suppress(Exception):
            await ws.send_json({'kind': 'error', 'message': 'Invalid tracking stream or settings: ' + type(exc).__name__})
    finally:
        for task in (pending, comparison_pending, reader_task, similar_pending):
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        with suppress(Exception):
            await ws.close()
