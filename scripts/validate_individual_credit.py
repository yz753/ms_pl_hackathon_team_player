"""Controlled test: does a player's own value respond to HIS personality only?

For each seed, two otherwise identical matches (same clubs, same random seed) are simulated;
only the focal player's tendency differs (low vs high). The production online pipeline
analyses both, and we report how his own value / team outcome change, and how much other
players' figures move (spill-over). Matches can be saved (and rendered as HTML) to inspect.

    python scripts/validate_individual_credit.py --role striker --seeds 10 --save-dir validation/striker --html

Uses the private simulator (QA only); never part of the deployed app.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere
import argparse
import collections
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
import gzip
import json
import numpy as np

GROUPS = {'striker': (10, 'drop', .08, .92), 'fullback': (1, 'overlap', .07, .90), 'winger': (9, 'decoy', .15, .90)}
FOCUS = ('ST', 'LB', 'RB', 'LW', 'RW')


def run(job):
    role, seed, level, duration, save_dir = job
    from team_player.simulator.match import MatchSimulator
    from team_player.simulator.engine import SimulationConfig
    from team_player.simulator.league import draw_teams
    from team_player.analyser.live import LivePipeline
    from team_player.analyser.presentation import Preferences
    i, attr, lo, hi = GROUPS[role];pair = draw_teams(np.random.default_rng(1000 + seed))
    cfg = SimulationConfig(team_names=pair, league_seed=2026, formation_b='4231', seed=5000 + seed, duration=duration,
                           hz=10, scenario='free_play', start=0, end=duration)
    sim = MatchSimulator(cfg);sim.styles[i] = replace(sim.styles[i], **{attr: hi if level == 'high' else lo})
    meta = sim.metadata();meta.update(input_type='positions_only', match_id=f'{role}_seed{seed}_{level}')
    pipe = LivePipeline(meta);pref = Preferences(player=sim.ids[i]);poss = collections.Counter();prev = None;eps = []
    handle = None
    if save_dir:
        path = Path(save_dir) / f'{role}_seed{seed:02d}_{level}.jsonl.gz';path.parent.mkdir(parents=True, exist_ok=True)
        handle = gzip.open(path, 'wt', encoding='utf-8');handle.write(json.dumps(meta) + '\n')
    for fr in sim.position_frames():
        if handle:
            handle.write(json.dumps(fr, separators=(',', ':')) + '\n')
        pk = pipe.update(fr, pref);eps += pk['tactics']['completed']
        if prev is not None and pk['frame']['possession']:
            poss[pk['frame']['possession']] += fr['t'] - prev
        prev = fr['t']
    if handle:
        handle.close()
    eps += pipe.finish()
    rows = collections.defaultdict(lambda: {'own_value': 0., 'team_outcome': 0., 'episodes': 0, 'def_own': 0., 'def_team': 0.})
    for e in eps:
        if e['kind'] in ('fullback_recovery', 'fullback_defence'):   # without the ball
            r = rows[e['player']];r['def_own'] += e['individual']['net'];r['def_team'] += e['value']['net']
        elif e.get('individual'):
            r = rows[e['player']];r['own_value'] += e['individual']['net'];r['team_outcome'] += e['value']['net'];r['episodes'] += 1
    total = sum(poss.values())
    # with the ball: per 10 min of his team's possession; without: per 10 min of the opponents'
    per10 = {p: {k: (v if k == 'episodes' else 600 * v / max(total - poss[p[0]] if k.startswith('def') else poss[p[0]], 1))
                 for k, v in r.items()} for p, r in rows.items()}
    roles = {p['id']: p['role'] for p in meta['players']}
    return {'seed': seed, 'level': level, 'focal': sim.ids[i], 'rows': per10, 'roles': roles}


def report(results, role):
    by = collections.defaultdict(dict)
    for r in results:
        by[r['seed']][r['level']] = r
    for metric in ('own_value', 'team_outcome') + (('def_own', 'def_team') if role == 'fullback' else ()):
        focal = [];others = collections.defaultdict(list)
        for v in by.values():
            hi, lo, foc = v['high']['rows'], v['low']['rows'], v['high']['focal']
            get = lambda d, p: d.get(p, {}).get(metric, 0.)
            focal.append(get(hi, foc) - get(lo, foc))
            for p, r in v['high']['roles'].items():
                if p != foc and r in FOCUS:
                    others[('team-mate ' if p[0] == foc[0] else 'opponent ') + r].append(get(hi, p) - get(lo, p))
        f = np.array(focal);t = f.mean() / (f.std(ddof=1) / np.sqrt(len(f)) + 1e-12)
        same = max((f > 0).sum(), (f < 0).sum())
        unit = "the opponents' possession" if metric.startswith('def') else 'team possession'
        print(f'\n{role} · {metric} (per 10 min of {unit}, high minus low tendency)')
        lo_m = np.mean([v['low']['rows'].get(v['low']['focal'], {}).get(metric, 0.) for v in by.values()])
        print(f'  focal low {lo_m:+.4f} -> high {lo_m + f.mean():+.4f}')
        print(f'  focal player: {f.mean():+.4f}  t={t:+.2f}  same direction in {same}/{len(f)} seeds')
        for k, x in sorted(others.items()):
            x = np.array(x);tx = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)) + 1e-12)
            print(f'  {k:13s} {x.mean():+.4f}  ({100 * abs(x.mean()) / (abs(f.mean()) + 1e-12):4.0f}% of focal)  t={tx:+.2f}')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--role', choices=GROUPS, default='striker');p.add_argument('--seeds', type=int, default=10)
    p.add_argument('--duration', type=float, default=300);p.add_argument('--workers', type=int, default=4)
    p.add_argument('--save-dir', help='save every simulated match (.jsonl.gz) here');p.add_argument('--html', action='store_true',
                   help='also render each saved match as an HTML replay (needs --save-dir; ~9 MB each)')
    a = p.parse_args()
    jobs = [(a.role, s, lvl, a.duration, a.save_dir) for s in range(a.seeds) for lvl in ('low', 'high')]
    with ProcessPoolExecutor(a.workers) as pool:
        results = list(pool.map(run, jobs))
    report(results, a.role)
    if a.save_dir:
        Path(a.save_dir, 'results.json').write_text(json.dumps(results, indent=1))
        if a.html:
            from team_player.app.match_replay import create_match_replay
            for r in results:
                src = Path(a.save_dir) / f"{a.role}_seed{r['seed']:02d}_{r['level']}.jsonl.gz"
                out = src.with_name(src.name.replace('.jsonl.gz', '.html'))
                if not out.exists():
                    create_match_replay(src, out, initial_player=r['focal'])
        print(f'\nSaved matches and results.json in {a.save_dir}')


if __name__ == '__main__':
    main()
