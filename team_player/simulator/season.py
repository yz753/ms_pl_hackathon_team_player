"""Premier-League-style seasons: fixtures, reproducible seeds and match generation.

    python -m team_player.simulator.season fixtures --season 2019-20
    python -m team_player.simulator.season generate --season 2019-20 --out data --private-out private \
        [--matchweeks 1-38] [--limit N] [--workers 8]

Files: <out>/<history|current>/<season>/mw<NN>_<HOME>-<AWAY>.jsonl.gz (positions only; the
metadata row carries season, matchweek, home, away and kickoff date). The private profile
map goes to <private-out>/<season>.jsonl and never into the public tree.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import date, timedelta
import gzip
import json
import os
from pathlib import Path
import numpy as np

from team_player.config.league import (TEAMS, LEAGUE_SEED, LEAGUE_FORMATION, MATCH_DURATION_S, SAMPLE_HZ,
                                       HISTORY_SEASONS, CURRENT_SEASON, SEASON_SEED, SEASON_START)

SEASONS = HISTORY_SEASONS + (CURRENT_SEASON,)


def kind_of(season):
    if season not in SEASONS:
        raise ValueError(f'Unknown season {season}; edit config/league.py')
    return 'current' if season == CURRENT_SEASON else 'history'


def fixtures(season):
    """Double round robin (circle method), 38 matchweeks, alternating home and away.

    The team order is shuffled per season with a seeded RNG, so each season has its own calendar."""
    index = SEASONS.index(season) if season in SEASONS else int(season[:4]) - 2019
    rng = np.random.default_rng(np.random.SeedSequence([SEASON_SEED, index, 0]))
    teams = [str(t) for t in rng.permutation(TEAMS)]
    n = len(teams);rounds = []
    order = teams[:]
    for r in range(n - 1):
        pairs = []
        for k in range(n // 2):
            a, b = order[k], order[n - 1 - k]
            pairs.append((a, b) if (r + k) % 2 == 0 else (b, a))
        rounds.append(pairs)
        order = [order[0]] + [order[-1]] + order[1:-1]
    rounds += [[(b, a) for a, b in pairs] for pairs in rounds]   # second half: reversed venues
    start = date.fromisoformat(SEASON_START.get(season, f'{season[:4]}-08-15'))
    out = []
    for mw, pairs in enumerate(rounds, 1):
        for k, (home, away) in enumerate(pairs):
            seed = int(np.random.SeedSequence([SEASON_SEED, index, mw, k]).generate_state(1)[0])
            out.append({'season': season, 'matchweek': mw, 'home': home, 'away': away, 'seed': seed,
                        'fixture_id': f'{season}-MW{mw:02d}-{home}-{away}',
                        'kickoff': (start + timedelta(days=7 * (mw - 1))).isoformat()})
    return out


def relative_path(fx):
    return Path(kind_of(fx['season'])) / fx['season'] / f"mw{fx['matchweek']:02d}_{fx['home']}-{fx['away']}.jsonl.gz"


def simulate_fixture(fx, duration=MATCH_DURATION_S, hz=SAMPLE_HZ):
    """Returns (metadata, frame iterator, private record). Same fixture -> identical match."""
    from team_player.simulator.engine import SimulationConfig
    from team_player.simulator.match import MatchSimulator
    rng = np.random.default_rng(fx['seed'])
    cfg = SimulationConfig(team_names=(fx['home'], fx['away']), league_seed=LEAGUE_SEED, formation_a=LEAGUE_FORMATION,
                           formation_b=LEAGUE_FORMATION, seed=int(rng.integers(0, 2 ** 32)), duration=duration, hz=hz,
                           scenario='free_play', start=0, end=duration, match_tempo='mixed')
    cfg.acquisition = 'goalkeeper' if rng.random() < 2 / 3 else 'turnover'
    sim = MatchSimulator(cfg)
    meta = sim.metadata()
    meta.update(input_type='positions_only', match_id=fx['fixture_id'], season=fx['season'], matchweek=fx['matchweek'],
                home=fx['home'], away=fx['away'], kickoff=fx['kickoff'])
    private = {'fixture_id': fx['fixture_id'], 'config': asdict(cfg),
               'player_profiles': {pid: asdict(s) for pid, s in zip(sim.ids, sim.styles)}}
    return meta, sim.position_frames(), private


def private_record(fx, duration=MATCH_DURATION_S):
    """Profiles/config of a fixture without simulating it (deterministic from the fixture seed)."""
    return simulate_fixture(fx, duration)[2]


def write_fixture(fx, out_root, duration=MATCH_DURATION_S):
    """Write one match (gzip JSONL). Skips existing files, so interrupted runs can resume."""
    target = Path(out_root) / relative_path(fx)
    if target.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    meta, frames, private = simulate_fixture(fx, duration)
    tmp = target.with_suffix('.tmp')
    with gzip.open(tmp, 'wt', encoding='utf-8', compresslevel=6) as handle:
        handle.write(json.dumps(meta) + '\n')
        for frame in frames:
            handle.write(json.dumps(frame, separators=(',', ':')) + '\n')
    os.replace(tmp, target)
    return private


def _job(args):
    fx, out, duration = args
    return write_fixture(fx, out, duration)


def parse_range(text, top):
    if not text:
        return range(1, top + 1)
    a, _, b = text.partition('-')
    return range(int(a), int(b or a) + 1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('fixtures');f.add_argument('--season', required=True)
    g = sub.add_parser('generate');g.add_argument('--season', required=True);g.add_argument('--out', required=True)
    g.add_argument('--private-out', required=True);g.add_argument('--matchweeks', default='')
    g.add_argument('--limit', type=int, default=0, help='Only the first N fixtures (examples)')
    g.add_argument('--workers', type=int, default=1);g.add_argument('--duration', type=float, default=MATCH_DURATION_S)
    a = p.parse_args()
    if a.cmd == 'fixtures':
        for fx in fixtures(a.season):
            print(json.dumps({k: v for k, v in fx.items() if k != 'seed'}))
        return
    out, private = Path(a.out).resolve(), Path(a.private_out).resolve()
    if out == private or out in private.parents or private in out.parents:
        p.error('Private output must be outside the public data tree')
    weeks = set(parse_range(a.matchweeks, 38))
    todo = [fx for fx in fixtures(a.season) if fx['matchweek'] in weeks]
    if a.limit:
        todo = todo[:a.limit]
    private.mkdir(parents=True, exist_ok=True)
    jobs = [(fx, str(out), a.duration) for fx in todo]
    if a.workers > 1:
        with ProcessPoolExecutor(a.workers) as pool:
            results = list(pool.map(_job, jobs))
    else:
        results = [_job(j) for j in jobs]
    # Private profile records: one per fixture, also for files left by an interrupted run.
    record_file = private / f'{a.season}.jsonl'
    have = {json.loads(l)['fixture_id'] for l in record_file.open()} if record_file.exists() else set()
    with record_file.open('a') as handle:
        for fx, r in zip(todo, results):
            if fx['fixture_id'] in have:
                continue
            if r is None and (out / relative_path(fx)).exists():
                r = private_record(fx, a.duration)
            if r:
                handle.write(json.dumps(r) + '\n')
    print(f'{sum(r is not None for r in results)} new matches written under {out / kind_of(a.season) / a.season}')


if __name__ == '__main__':
    main()
