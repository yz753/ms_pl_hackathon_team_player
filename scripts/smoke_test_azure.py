"""Explicit live Foundry smoke test. Requires credentials and makes paid model calls."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere: python scripts/smoke_test_azure.py
import asyncio
import json
from pathlib import Path
from team_player.analyser.live import LivePipeline
from team_player.analyser.presentation import Preferences
from team_player.agents.foundry import FoundryDirector,bounded_explanation

async def main():
    director=FoundryDirector()
    try:
        with Path('matches/match_0000.jsonl').open() as handle:
            meta=json.loads(next(handle));pipeline=LivePipeline(meta)
            for line in handle:
                snapshot=pipeline.update(json.loads(line),Preferences())['snapshot']
                if snapshot and snapshot['cards']:
                    result=await bounded_explanation(director,snapshot,timeout=30)
                    print(json.dumps(result,indent=2));return
        raise RuntimeError('No eligible evidence in input')
    finally:await director.close()

if __name__=='__main__':asyncio.run(main())
