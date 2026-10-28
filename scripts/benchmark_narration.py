"""Benchmark simplified live/history AI narration using real tracking and local history. Paid calls."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,asyncio,json,time,statistics
from team_player.analyser.live import LivePipeline
from team_player.analyser.presentation import Preferences
from team_player.analyser.history import HistoryStore
from team_player.analyser.situations import LiveSimilarity
from team_player.agents.narration import CHOICE_FOR_ROLE
from team_player.agents.foundry import FoundryDirector
from smoke_test_azure import first_snapshot

async def main(args):
    path=Path(args.match)
    with path.open() as f:meta=json.loads(next(f))
    store=HistoryStore(args.history)
    similarity=LiveSimilarity(store.index());pipe=LivePipeline(meta)
    prefs=Preferences(player=next(p['id'] for p in meta['players'] if p['role']=='ST'))
    historical=None
    with path.open() as f:
        next(f)
        for line in f:
            historical=similarity.update(pipe.update(json.loads(line),prefs)['tactics'])
            if historical and historical.get('available'):break
    if not historical:raise RuntimeError('No historical match available')
    cases=[]
    for role in ('ST','LW','LB'):
        player=next(p['id'] for p in meta['players'] if p['role']==role)
        cases.append(('live',role,first_snapshot(path,player)))
        cases.append(('history',role,{**historical,'focus_choice':CHOICE_FOR_ROLE[role]}))
    store.con.close()
    director=FoundryDirector();rows=[]
    try:
        for repeat in range(args.repeats):
            for kind,role,evidence in cases:
                if kind not in args.kinds or role not in args.roles:continue
                row={'kind':kind,'role':role,'repeat':repeat};start=time.perf_counter()
                try:
                    call=director.explain_live(evidence,reasoning=args.reasoning,diagnostics=row) if kind=='live' else director.similar(evidence,reasoning=args.reasoning,diagnostics=row)
                    result=await asyncio.wait_for(call,args.timeout)
                    row.update(status='accepted',narrative=result.get('narrative',result.get('summary')))
                except Exception as exc:row.update(status='rejected',error=type(exc).__name__+': '+str(exc))
                row['elapsed_s']=round(time.perf_counter()-start,3);rows.append(row)
                with open(args.output,'a') as f:f.write(json.dumps(row,default=str)+'\n')
                print(f"{kind}/{role}: {row['status']} {row['elapsed_s']}s",flush=True)
        for kind in ('live','history'):
            subset=[r for r in rows if r['kind']==kind]
            if not subset:continue
            print(json.dumps({'kind':kind,'n':len(subset),'median_s':statistics.median(r['elapsed_s'] for r in subset),
                'max_s':max(r['elapsed_s'] for r in subset),'accepted':sum(r['status']=='accepted' for r in subset),
                'accepted_within_5s':sum(r['status']=='accepted' and r['elapsed_s']<=5 for r in subset)}))
    finally:await director.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--match',default='matches/match_0000.jsonl');p.add_argument('--history',default='data/history.db')
    p.add_argument('--kinds',nargs='+',choices=['live','history'],default=['live','history'])
    p.add_argument('--roles',nargs='+',choices=['ST','LW','LB'],default=['ST','LW','LB'])
    p.add_argument('--repeats',type=int,default=2);p.add_argument('--timeout',type=float,default=30)
    p.add_argument('--reasoning',choices=['default','low','minimal'],default='low')
    p.add_argument('--output',default='narration_benchmark.jsonl');a=p.parse_args()
    if a.repeats<1 or not 0<a.timeout<=300:p.error('Invalid repeats or timeout')
    asyncio.run(main(a))
