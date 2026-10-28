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
import copy
from contextlib import asynccontextmanager, suppress
import html
import json
import os
import time
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from team_player.schema import validate_metadata
from team_player.analyser.comparison import History, Accumulator, compare as raw_compare, FIELDS
from team_player.analyser.live import LivePipeline
from team_player.analyser.presentation import Preferences, local_overlay, overlay_is_current, verdicts as raw_verdicts
from team_player.analyser.situations import LiveSimilarity
from team_player.agents.narration import local_summary, similar_story, balance_from_row as raw_balance, defence_from_row as raw_defence
from team_player.app.storage import DataSource
from team_player.app.glossary import VIEW_GLOSSARY, EQUATIONS
from team_player.config.analysis import MIN_POSSESSION_S, GLOSSARY, SUPPORTED_ROLES
from team_player.config.league import TEAMS, LEAGUE_ROLES, CURRENT_SEASON, LEAGUE_FORMATION, player_id
from team_player.utils.io import open_text

from team_player.agents.reporting import report_units, valid_comparison, INVALID_COMPARISON
from team_player.agents.impact import impact_candidate
from team_player.agents.option_watch import PassingOptionWatch

# Reporting adapters preserve every numeric field and keep batch modules untouched.
def compare(rows, left, right, t, *args, **kwargs):
    if not valid_comparison(left, right):
        return {'t':t, 'left':{'id':left}, 'right':{'id':right}, 'selection_error':INVALID_COMPARISON}
    return report_units(raw_compare(rows, left, right, t, *args, **kwargs))
def verdicts(*args, **kwargs):return report_units(raw_verdicts(*args, **kwargs))
def balance_from_row(*args, **kwargs):return report_units(raw_balance(*args, **kwargs))
def defence_from_row(*args, **kwargs):return report_units(raw_defence(*args, **kwargs))

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
    app.state.history_source = {'backend':'azure_blob' if app.state.data.account and os.environ.get('TEAM_PLAYER_HISTORY_DB') is None else 'local', 'account_url':app.state.data.account or None, 'container':app.state.data.analysis_container if app.state.data.account else None, 'database_path':str(db) if db else None}
    if db:
        from team_player.analyser.history import HistoryStore
        app.state.store = HistoryStore(db)
        app.state.index = await asyncio.to_thread(app.state.store.index)
        app.state.history_rows = await asyncio.to_thread(app.state.store.all_player_totals)
    yield
    if app.state.director:
        await app.state.director.close()

app = FastAPI(title='Team Player', lifespan=lifespan)
from team_player.app.generation import router as generation_router
app.include_router(generation_router)

@app.get('/generate')
async def generate_page():
    return FileResponse(WEB / 'generate.html', headers={'Cache-Control':'no-store'})


@app.get('/')
async def index(request: Request):
    page = 'index.html' if ('demo' in request.query_params or 'match' in request.query_params) else 'lobby.html'
    return FileResponse(WEB / page, headers={'Cache-Control': 'no-store'})

@app.get('/watch')
async def watch_page():
    return FileResponse(WEB / 'index.html', headers={'Cache-Control': 'no-store'})

@app.get('/api/examples')
async def examples():
    return {'examples': json.loads((WEB / 'examples.json').read_text())}


@app.get('/compare')
async def compare_page():
    return FileResponse(WEB / 'compare.html', headers={'Cache-Control': 'no-store'})


@app.get('/reports')
async def reports_page():
    return FileResponse(WEB / 'reports.html', headers={'Cache-Control': 'no-store'})


@app.get('/glossary')
async def glossary():
    items = ''.join(f'<section id="{html.escape(k)}"><h2>{html.escape(k)}</h2><p>{html.escape(v[0].upper() + v[1:])}.</p></section>'
                    for k, v in VIEW_GLOSSARY.items())
    return HTMLResponse('<!doctype html><meta charset="utf-8"><title>Team Player · Glossary</title>'
                        '<style>body{background:#101a22;color:#e7eff5;font:16px system-ui;max-width:760px;margin:30px auto;padding:0 16px}'
                        'h2{color:#f3cc80;font-size:18px;margin-bottom:4px}section{border-top:1px solid #2b4248;padding:8px 0}'
                        'section:target{background:#172733;border-radius:8px;padding:8px}</style>'
                        f'<h1>Glossary</h1><p>Definitions and the implemented benefit–cost model.</p>{items}{EQUATIONS}')


@app.get('/health')
async def health():
    return {'status': 'ok', 'ai_backend': os.environ.get('TEAM_PLAYER_AI', 'local'),
            'history': app.state.store.summary() if app.state.store else None, 'history_source':app.state.history_source}


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
    return await bounded_agent_call(app, app.state.director.explain_live(snapshot), 30)


def remember_ai_moment(history, result):
    """Keep validated AI captions as historical, never relabel their evidence time."""
    narrative = result.get('narrative') or {}
    if narrative.get('method') != 'microsoft_foundry_agent':
        return
    item = {k: result[k] for k in ('player', 'audience', 'episode_id', 'evidence_at')}
    item.update(narrative=narrative, latency_ms=result.get('latency_ms'))
    key = (item['player'], item['audience'], item['episode_id'], item['evidence_at'])
    if not any((m['player'], m['audience'], m['episode_id'], m['evidence_at']) == key for m in history):
        history.insert(0, item)
        del history[300:]


def resolve_match(app, requested, demo=None):
    if demo:
        # Public tracking only: never accept arbitrary paths or private profile names.
        directory = Path(__file__).resolve().parents[2] / 'matches'
        allowed = {p.name: p for p in directory.glob('match_*.jsonl') if p.is_file() and not p.is_symlink()}
        if demo not in allowed:
            raise ValueError('Unknown demo recording')
        return allowed[demo]
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
    # Keep historical totals separate from the already-observed live prefix.
    empty = Accumulator(meta)
    return lambda: legacy.rows(empty)


@app.websocket('/ws')
async def stream(ws: WebSocket):
    # Browser access is same-origin. Remote deployment should additionally enable App Service Authentication.
    origin = ws.headers.get('origin')
    if origin and origin.split('://', 1)[-1] != ws.headers.get('host'):
        await ws.close(code=1008);return
    await ws.accept()
    try:
        path = resolve_match(ws.app, ws.query_params.get('match') if hasattr(ws, 'query_params') else None, ws.query_params.get('demo') if hasattr(ws, 'query_params') else None)
    except Exception:
        path = None
    if not path or not path.is_file():
        await ws.send_json({'kind': 'error', 'message': 'No match available. Generate current-season matches or set TEAM_PLAYER_MATCH.'})
        await ws.close();return
    state = {'paused': False, 'speed': 2., 'revision': 0, 'closed': False}
    pending = reader_task = comparison_pending = similar_pending = None
    similar_revision = -1;last_similar_agent = -100.
    recent_ai = []
    overlay = None;last_agent = -100.;last_status = 'AI not connected; measurements only'
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
            history_anchor = None;seen_impacts=set();history_contexts=[]
            peer = next((p for p in rows if p != preferences.player and rows[p]['role'] == ids[preferences.player]['role']), None) or \
                next(p['id'] for p in meta['players'] if p['id'] != preferences.player)
            state.update(left=preferences.player, right=peer, comparison_revision=0, review=False)
            comparison_snapshot = compare(rows, state['left'], state['right'], 0, accumulator.rows)
            await ws.send_json({'kind': 'metadata', 'metadata': meta, 'preferences': preferences.__dict__, 'file': path.name,
                                'season': meta.get('season', CURRENT_SEASON), 'roster': [{k: p[k] for k in ('id', 'team', 'role')} for p in rows.values()],
                                'comparison': comparison_snapshot, 'ai_backend': 'foundry' if ws.app.state.director else 'local',
                                'history': ws.app.state.store.summary() if ws.app.state.store else None,
                                'glossary_terms': list(VIEW_GLOSSARY)})

            async def review_comparison(snapshot, revision, request_id, audience):
                try:
                    if not ws.app.state.director:
                        raise RuntimeError('Connect Foundry to ask the AI consultant')
                    result = await bounded_agent_call(ws.app, ws.app.state.director.compare(snapshot, audience), 30)
                except Exception as exc:
                    result = {'method':'unavailable', 'error':f'AI consultant unavailable ({type(exc).__name__}). Please retry.'}
                if revision == state['comparison_revision'] and not state['closed']:
                    await ws.send_json({'kind':'comparison', 'request_id':request_id,
                                        'comparison':snapshot, 'pending':False, 'consultant':result})

            async def review_live(snapshot, revision):
                result=await agent_job(ws.app,snapshot)
                remember_ai_moment(recent_ai,result)
                if revision==state['revision'] and not state['closed']:
                    await ws.send_json({'kind':'live_report','player':preferences.player,
                                        'recent_ai':[m for m in recent_ai if m['player']==preferences.player and m['audience']==preferences.audience]})
                return result

            async def review_history(view, revision, player, audience):
                try:
                    if not ws.app.state.director:
                        raise RuntimeError('Foundry is not connected')
                    reviewed = await bounded_agent_call(ws.app, ws.app.state.director.similar(view, audience), 30)
                except Exception as exc:
                    reviewed = {'method':'unavailable', 'warnings':[f'AI history report unavailable ({type(exc).__name__}). Please retry.']}
                if revision == state['revision'] and not state['closed']:
                    await ws.send_json({'kind':'history_review','player':player,'pending':False,'review':reviewed,'similar':view})

            async def start_history_review():
                nonlocal similar_pending, last_similar_agent, similar_revision
                from team_player.agents.narration import CHOICE_FOR_ROLE
                if similar_pending and not similar_pending.done():
                    similar_pending.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await similar_pending
                pid=preferences.player
                if not history_anchor or not history_anchor.get('query'):
                    await ws.send_json({'kind':'history_review','player':pid,'pending':False,'review':{'method':'withheld','publish':False,'warnings':[]}})
                    return
                anchored_result = history_anchor['query']
                if not anchored_result.get('available'):
                    await ws.send_json({'kind':'history_review','player':pid,'pending':False,
                                        'review':{'warnings':['No comparable historical situations available yet.']}})
                    return
                view={**anchored_result, 'current_impact':history_anchor['impact'], 'query_t':history_anchor['impact']['observed_at'], 'focus_choice':CHOICE_FOR_ROLE.get(ids[pid]['role']), 'focus_role':ids[pid]['role']}
                if not view['focus_choice']:
                    await ws.send_json({'kind':'history_review','player':pid,'pending':False,
                                        'review':{'warnings':['Historical movement comparisons are not supported for this position.']}})
                    return
                last_similar_agent=time.monotonic();similar_revision=state['revision']
                await ws.send_json({'kind':'history_review','player':pid,'pending':True,'similar':view})
                similar_pending=asyncio.create_task(review_history(view,state['revision'],pid,preferences.audience))

            async def receive_controls():
                nonlocal preferences, comparison_pending, history_anchor
                try:
                    while True:
                        message = await ws.receive_json()
                        if message.get('type') == 'preferences':
                            candidate = Preferences(player=message.get('player', preferences.player), audience=message.get('audience', preferences.audience),
                                                    focus=message.get('focus', preferences.focus))
                            candidate.validate(meta);preferences = candidate;state['revision'] += 1;state['restory'] = True
                            history_anchor=None
                        elif message.get('type') == 'comparison':
                            left = message.get('left', state['left']);right = message.get('right', state['right'])
                            if left not in rows or right not in rows:
                                raise ValueError('Unknown comparison player')
                            state.update(left=left, right=right)
                            state['comparison_revision'] += 1
                            revision = state['comparison_revision']
                            request_id = message.get('request_id')
                            if comparison_pending:
                                comparison_pending.cancel()
                                with suppress(asyncio.CancelledError, Exception):
                                    await comparison_pending
                            snapshot = compare(rows_of(), left, right, accumulator.t, accumulator.rows)
                            review = bool(message.get('review')) and not snapshot.get('selection_error')
                            await ws.send_json({'kind':'comparison', 'request_id':request_id,
                                                'comparison':snapshot, 'pending':review, 'consultant':None})
                            if review:
                                comparison_pending = asyncio.create_task(review_comparison(snapshot, revision, request_id, preferences.audience))
                        elif message.get('type') == 'similar_review':
                            await start_history_review()
                        elif message.get('type') == 'playback':
                            speed = float(message.get('speed', state['speed']))
                            if speed not in (1, 2, 4, 8):
                                raise ValueError('Unsupported playback speed')
                            state['speed'] = speed;state['paused'] = bool(message.get('paused', False))
                except (WebSocketDisconnect, ValueError, TypeError):
                    state['closed'] = True
            reader_task = asyncio.create_task(receive_controls())
            pipeline = LivePipeline(meta, float(os.environ.get('TEAM_PLAYER_MIN_POSSESSION', MIN_POSSESSION_S)))
            option_watch = PassingOptionWatch(pipeline.analyzer)
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
                option_watch.update(packet, pipeline.episodes)
                gate = packet['possession']
                accumulator.update(packet['frame'], packet['analysis'], packet.get('tactics'))
                focus = ids[preferences.player]
                # Similar history moments: query on each new decision window, then tell the story
                # from the followed player's point of view.
                context_now=packet['tactics'].get('possession')
                if context_now and context_now.get('window'):
                    w=context_now['window']
                    if not history_contexts or history_contexts[-1]['t']!=w['t']:
                        history_contexts.append({'t':w['t'],'team':context_now['team'],'opponent':context_now['opponent'],'context':copy.deepcopy(w['context'])})
                        history_contexts=history_contexts[-256:]
                if similarity:
                    found = similarity.update(packet['tactics'])
                    if found:
                        similar_result = found;similar_changed = True
                        # Tables update immediately; no template prose substitutes for AI.
                    if found or state.pop('restory', False):
                        new = similar_story(similar_result, focus['id'], focus['role'], focus['team'])
                        if new or found:
                            story = new;similar_changed = True
                # Head-to-head: history figures plus this match so far.
                comparison_snapshot = compare(rows_of(), state['left'], state['right'], t, accumulator.rows)
                # Overlay: local evidence immediately, Foundry review asynchronously.
                if pending and pending.done():
                    try:
                        result = pending.result()
                        remember_ai_moment(recent_ai, result)
                        if result.get('latency_ms', float('inf')) <= 5000 and agent_revision == state['revision'] and gate['eligible'] and overlay_is_current(result, t, gate['episode_id'], preferences):
                            overlay = result;overlay_revision = state['revision'];last_status = 'Foundry agents reviewed the evidence'
                        else:
                            last_status = 'AI explanation saved in Recent moments; live measurements continue'
                    except Exception as exc:
                        last_status = 'AI explanation unavailable (' + type(exc).__name__ + '); measurements remain available'
                    pending = None
                snapshot = packet.pop('snapshot')
                if snapshot:
                    snapshot['similar_story'] = story
                    snapshot['balance'] = balance_from_row(accumulator.rows.get(preferences.player)) if focus['role'] in SUPPORTED_ROLES else None
                    snapshot['defence'] = defence_from_row(accumulator.rows.get(preferences.player))
                    snapshot['observed_row'] = dict(accumulator.rows.get(preferences.player, {}))
                    snapshot['recent_episodes'] = [copy.deepcopy(e) for e in pipeline.episodes if e.get('player')==preferences.player]
                    snapshot['recent_shots'] = [{**shot,'team':meta['teams'][int(shot['team'])]['id']} for shot in pipeline.tactics.shot_log if t-shot['t']<=60]
                    position=next(p for p in packet['frame']['players'] if p['id']==preferences.player)
                    direction=next(tm['attack_direction'] for tm in meta['teams'] if tm['id']==focus['team'])
                    snapshot['focus_position']={'local_x':position['x'] if direction==1 else 105-position['x'],'y':position['y']}
                    snapshot['passing_option_loss']=option_watch.for_player(preferences.player)
                    snapshot['forward_stall']=option_watch.forward_stall(preferences.player, pipeline.episodes)
                    snapshot['impact']=impact_candidate(snapshot)
                    if overlay_revision != state['revision'] or not overlay_is_current(overlay, t, gate['episode_id'], preferences) or overlay['selection_method'] == 'local_rules':
                        overlay = local_overlay(snapshot);overlay['narrative'] = None;overlay_revision = state['revision']
                    if ws.app.state.director and snapshot['impact'] and snapshot['impact']['key'] not in seen_impacts and not pending:
                        seen_impacts.add(snapshot['impact']['key'])
                        frozen=copy.deepcopy(snapshot)
                        pending = asyncio.create_task(review_live(frozen, state['revision']));last_agent = t;agent_revision = state['revision']
                        # Match on the latest observed pre-action window for this team.
                        contexts=[c for c in history_contexts if c['team']==focus['team'] and c['t']<=float(snapshot['impact']['t_start'] or t)]
                        history_anchor=None
                        if contexts and ws.app.state.index is not None:
                            c=contexts[-1]
                            query=await asyncio.to_thread(ws.app.state.index.query,c['context'],team=c['team'],opponent=c['opponent'],exclude_team=focus['team'],same_team_bonus=0)
                            history_anchor={'impact':copy.deepcopy(snapshot['impact']),'query':query}
                            await start_history_review()
                        last_status = 'AI is writing an explanation; measurements remain available'
                if overlay_revision != state['revision'] or not gate['eligible'] or not overlay_is_current(overlay, t, gate['episode_id'], preferences):
                    overlay = None
                if agent_revision != state['revision'] and overlay and overlay['selection_method'] == 'microsoft_foundry_agents':
                    overlay = None
                extra = {'recent_ai': [m for m in recent_ai if m['player'] == preferences.player and m['audience'] == preferences.audience], 'verdicts': verdicts(preferences.player, pipeline.episodes, t, window=float('inf')), 'unsupported': focus['role'] not in SUPPORTED_ROLES,
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
                await reader_task  # Keep comparison controls available after the recording ends.
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
