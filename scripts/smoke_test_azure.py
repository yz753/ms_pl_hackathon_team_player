"""Explicit live Foundry smoke test. Requires credentials and makes paid model calls."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere: python scripts/smoke_test_azure.py
import asyncio
import argparse
import math
import time
import json
import os
from pathlib import Path
from team_player.analyser.live import LivePipeline
from team_player.analyser.presentation import Preferences
from team_player.agents.foundry import FoundryDirector,bounded_explanation

def first_snapshot(path, player=None):
    with Path(path).open() as handle:
        meta=json.loads(next(handle))
        fallback=next((p['id'] for p in meta['players'] if 'ST' in p['role']),meta['players'][0]['id'])
        preferences=Preferences(player=player or fallback)
        preferences.validate(meta)
        pipeline=LivePipeline(meta)
        for line in handle:
            snapshot=pipeline.update(json.loads(line),preferences)['snapshot']
            if snapshot and snapshot['cards']:
                return snapshot
    raise RuntimeError(f'No eligible evidence for {preferences.player} in {path}')

async def main(timeout=5., mode="live"):
    if not math.isfinite(timeout) or timeout<=0:raise ValueError("Timeout must be positive and finite")
    root=Path(__file__).resolve().parents[1]
    path=Path(os.environ.get('TEAM_PLAYER_MATCH',str(root/'matches/match_0000.jsonl')))
    snapshot=first_snapshot(path,os.environ.get('TEAM_PLAYER_FAVOURITE'))
    print(f"Testing Foundry with player {snapshot['player']['player']} from {path}",flush=True)
    print(f"Allowing {timeout:g}s for {mode} workflow.",flush=True)
    started=time.perf_counter()
    director=FoundryDirector()
    try:
        result=await (asyncio.wait_for(director.explain_live(snapshot), timeout=timeout) if mode == "live"
                      else bounded_explanation(director,snapshot,timeout=timeout))
        print(json.dumps(result,indent=2))
        print(f"Completed in {time.perf_counter()-started:.1f}s. Check the trace and narrative method for any local fallback.")
        return 0
    except TimeoutError:
        print(f"Foundry workflow exceeded {timeout:g}s; the test was cancelled. This does not identify which service or stage caused the delay. "
              "A longer diagnostic timeout does not change the live app deadline. Use --mode live --timeout 30 to diagnose a slow live response.",file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Response rejected after {time.perf_counter()-started:.1f}s: {exc}", file=sys.stderr)
        return 1
    finally:
        await director.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout',type=float,default=5.,help='Total workflow timeout in seconds (default: 5)')
    parser.add_argument("--mode", choices=("live", "full"), default="live")
    args=parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout<=0:parser.error('--timeout must be positive and finite')
    sys.exit(asyncio.run(main(args.timeout, args.mode)))
