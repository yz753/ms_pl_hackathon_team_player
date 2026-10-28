"""Paid Azure benchmark: same evidence, schema and deployment across request modes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
import json
import os
import statistics
import time
from smoke_test_azure import first_snapshot
from team_player.agents.foundry import FoundryDirector

async def main(args):
    path=Path(args.match)
    with path.open() as f: meta=json.loads(next(f))
    cases=[]
    for family, roles in [('striker',{'ST','LST','RST'}),('winger',{'LW','RW','LM','RM'}),('fullback',{'LB','RB'})]:
        player=next((p['id'] for p in meta['players'] if p['role'] in roles),None)
        if player:
            try: cases.append((family,first_snapshot(path,player)))
            except RuntimeError as exc: print(f'Skipping {family}: {exc}',flush=True)
    if not cases: raise RuntimeError('No eligible evidence cases')
    director=FoundryDirector();rows=[]
    try:
        # Explicitly measure cold credential acquisition, then reuse this credential/client.
        auth_start=time.perf_counter()
        await director.credential.get_token('https://ai.azure.com/.default')
        print(f'Credential acquisition: {time.perf_counter()-auth_start:.3f}s (token never logged)',flush=True)
        for repeat in range(args.repeats):
            for family,snapshot in cases:
                configs=[(t,e) for t in args.transports for e in args.efforts]
                if repeat%2: configs.reverse()
                for transport,effort in configs:
                    row={'repeat':repeat,'family':family,'player':snapshot['player']['player'],
                         'evidence_at':snapshot['t'],'transport':transport,'reasoning':effort}
                    start=time.perf_counter()
                    try:
                        result=await asyncio.wait_for(director.explain_live(snapshot,transport=transport,
                            reasoning=effort,diagnostics=row),args.timeout)
                        row['status']='caption' if result.get('narrative') else 'veto'
                        row['narrative']=result.get('narrative')
                    except Exception as exc:
                        row.update(status='timeout' if isinstance(exc,TimeoutError) else 'error',
                                   error_type=type(exc).__name__,error=str(exc)[:1000])
                    row['elapsed_s']=round(time.perf_counter()-start,3)
                    row['valid_within_5s']=row['status']=='caption' and row['elapsed_s']<=5
                    rows.append(row)
                    with Path(args.output).open('a') as f: f.write(json.dumps(row,default=str)+'\n')
                    print(f"{family} {transport}/{effort}: {row['status']} in {row['elapsed_s']}s",flush=True)
        for transport in args.transports:
            for effort in args.efforts:
                subset=[r for r in rows if r['transport']==transport and r['reasoning']==effort]
                times=sorted(r['elapsed_s'] for r in subset)
                print(json.dumps({'transport':transport,'reasoning':effort,'requests':len(subset),
                    'valid_within_5s':sum(r['valid_within_5s'] for r in subset),
                    'valid_captions':sum(r['status']=='caption' for r in subset),
                    'median_observed_s':statistics.median(times),'max_observed_s':max(times),
                    'note':'Timeouts are censored; small samples do not establish a service level.'}))
    finally:
        await director.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--match',default=os.environ.get('TEAM_PLAYER_MATCH',str(Path(__file__).resolve().parents[1]/'matches/match_0000.jsonl')))
    p.add_argument('--repeats',type=int,default=1)
    p.add_argument('--timeout',type=float,default=30)
    p.add_argument('--transports',nargs='+',choices=['agent','direct'],default=['agent','direct'])
    p.add_argument('--efforts',nargs='+',choices=['default','low'],default=['default','low'])
    p.add_argument('--output',default='azure_latency.jsonl')
    a=p.parse_args()
    if a.repeats<1 or not 0<a.timeout<=300:p.error('Use positive repeats and timeout up to 300 seconds')
    asyncio.run(main(a))
