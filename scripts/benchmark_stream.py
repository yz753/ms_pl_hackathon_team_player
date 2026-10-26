"""Run the entire stream without pacing; prove online prefix processing and measure cost."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere: python scripts/benchmark_stream.py
import argparse
from collections import Counter
import json
from pathlib import Path
import time
import numpy as np
from team_player.analyser.live import LivePipeline
from team_player.analyser.presentation import Preferences,local_overlay


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',default='matches/match_0000.jsonl')
    parser.add_argument('--out',default='stream_validation.json')
    parser.add_argument('--player',default=None,help='Player to follow (default: first striker in the match)')
    args=parser.parse_args();counts=Counter();times=[];windows={};card_types=Counter();team_seconds=Counter();frames=0;last_t=0
    started=time.perf_counter()
    with Path(args.input).open() as handle:
        meta=json.loads(next(handle));pipeline=LivePipeline(meta)
        player=args.player or next(p['id'] for p in meta['players'] if 'ST' in p['role'])
        prefs=Preferences(player);prefs.validate(meta)
        for line in handle:
            raw=json.loads(line);packet=pipeline.update(raw,prefs);frames+=1;times.append(packet['processing_ms'])
            gate=packet['possession'];counts[gate['status']]+=1
            if gate['team']:team_seconds[gate['team']]+=raw['t']-last_t
            last_t=raw['t']
            for event in packet['frame']['events']:counts[event['type']]+=1
            if packet['snapshot']:
                for c in packet['snapshot']['cards']:card_types[c['topic']]+=1
                if packet['snapshot']['cards']:
                    windows.setdefault(str(gate['episode_id']),{'first_evidence':raw['t'],'player':args.player,'first_card':packet['snapshot']['cards'][0]['title']})
    elapsed=time.perf_counter()-started
    result={'frames':frames,'simulated_seconds':last_t,'processing_seconds':round(elapsed,3),
            'realtime_factor_unpaced':round(last_t/elapsed,2),'latency_ms':{'median':float(np.median(times)),'p95':float(np.percentile(times,95)),'max':max(times)},
            'counts':counts,'qualified_card_topics':card_types,'possession_context_seconds':team_seconds,'example_windows':windows,
            'ai_validation':'Local rules only. No Azure endpoint configured; no model latency measured.'}
    Path(args.out).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':main()
