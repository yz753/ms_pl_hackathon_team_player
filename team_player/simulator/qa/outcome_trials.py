"""Private simulator QA: complete outcome sequences, never an analyser input."""
import json, argparse
from pathlib import Path
from collections import Counter,deque
from concurrent.futures import ProcessPoolExecutor
import numpy as np
from team_player.simulator.match import MatchSimulator
from team_player.simulator.engine import SimulationConfig
from team_player.utils.geometry import norm

def vacated_marker_space(before,after,opponents,markers,beneficiary):
 """Conservative positional prerequisite, not a causal effect estimate."""
 nearest=min(opponents,key=lambda j:norm(before[j]-before[beneficiary]))
 if nearest not in markers:return False
 # Evaluate the old location too, so a teammate's own movement is not credited.
 withdrawn=norm(after[nearest]-before[beneficiary])-norm(before[nearest]-before[beneficiary])
 gain=min(norm(after[j]-after[beneficiary]) for j in opponents)-norm(before[nearest]-before[beneficiary])
 return withdrawn>2 and gain>2 and norm(after[beneficiary]-before[beneficiary])<8

def run(job):
 seed,out,private=job
 sim=MatchSimulator(SimulationConfig(seed=seed,duration=600,hz=5,scenario='free_play',start=0,end=600,formation_b='4231',striker_style='drops_and_returns',winger_style='decoy_runner',fullback_style='often_overlaps'))
 root=Path(out);meta=sim.metadata();meta.update(input_type='positions_only',match_id=f'seed_{seed}')
 counts=Counter();examples=[];history=deque();deep={};follow={};counter=None;pass_info=None;shot_context=None;last_owner=None
 with (root/f'seed_{seed}.jsonl').open('w') as h:
  h.write(json.dumps(meta)+'\n')
  for f in sim.frames():
   t=f['t'];raw={'kind':'frame','t':t,'players':f['players'],'ball':{k:f['ball'][k] for k in ('x','y')}};h.write(json.dumps(raw,separators=(',',':'))+'\n')
   owner=sim.owner
   if owner is not None:last_owner=owner
   history.append((t,sim.pos.copy(),sim.ball.copy(),sim.possession,owner,dict(sim.marking)))
   while history and t-history[0][0]>3.1:history.popleft()
   # Two defenders move with an off-ball winger, after removing common line movement.
   if len(history)>10 and sim.possession is not None:
    _,old,oldball,oldteam,_,_=history[0];team=sim.possession
    if oldteam==team:
     opp=[i for i in range(22) if sim.teams[i]!=team and sim.roles[i]!='GK'];common=np.median(sim.pos[opp]-old[opp],axis=0)
     for i in range(22):
      if sim.teams[i]!=team or sim.roles[i] not in ('LW','RW') or i==owner:continue
      if any(h[4]==i or h[3]!=team for h in history):continue
      move=sim.pos[i]-old[i];length=norm(move)
      if length<5:continue
      markers=[]
      for j in opp:
       motion=sim.pos[j]-old[j]-common;ml=norm(motion)
       if ml>2 and motion@move/(ml*length)>.65 and norm(sim.pos[j]-sim.pos[i])<14 and norm(sim.pos[j]-sim.pos[i])<norm(old[j]-old[i])+2:markers.append(j)
      # Private QA checks actual marking targets as well as geometry.
      # This is never supplied to the position-only analyser or AI.
      recent=[h for h in history if t-h[0]<=1.0]
      markers=[j for j in markers if recent and all(h[5].get(j)==i for h in recent)]
      if len(markers)<2:continue
      for k in range(22):
       if sim.teams[k]!=team or k==i or sim.roles[k]=='GK' or sim.local(sim.pos[k],team)[0]<60:continue
       before=min(norm(old[j]-old[k]) for j in opp);after=min(norm(sim.pos[j]-sim.pos[k]) for j in opp)
       if vacated_marker_space(old,sim.pos,opp,markers,k):
        if team not in follow or t-follow[team]['t']>10:counts['two_follow_space']+=1
        follow[team]={'t':t,'runner':sim.ids[i],'beneficiary':sim.ids[k],'markers':[sim.ids[j] for j in markers]}
   for e in f['events']:
    kind=e['type'];counts[kind]+=1
    if kind=='possession_change':
     team=sim.cfg.team_names.index(e['team']);deep={};follow={};shot_context=None
     exposed=[i for i in range(22) if sim.teams[i]!=team and sim.roles[i] in ('LB','RB') and sim.local(sim.pos[i],1-team)[0]>65]
     counter={'t':t,'team':team,'x':sim.local(sim.ball,team)[0],'backs':exposed,'progress':False} if exposed and any(x['type'] in ('tackle','recovery') for x in f['events']) else None
    if kind=='pass':
     i=sim.ids.index(e['player']);team=int(sim.teams[i]);pass_info=(i,e['target'],t,float(e['distance_m']))
     if 'ST' in sim.roles[i] and sim.local(sim.pos[i],team)[0]<60 and e['target']:
      j=sim.ids.index(e['target'])
      if sim.roles[j] in ('LW','RW') and sim.directions[i]*(sim.pos[j,0]-sim.pos[i,0])>10:
       deep[team]={'t':t,'striker':sim.ids[i],'winger':sim.ids[j],'distance':e['distance_m'],'received':False};counts['deep_wing_attempt']+=1
    if kind=='reception':
     for d in deep.values():
      if e['player']==d['winger'] and t-d['t']<5 and not d['received']:d['received']=True;counts['deep_wing_received']+=1
    if kind=='shot':
     i=sim.ids.index(e['player']);team=int(sim.teams[i]);shot_context=[]
     if team in deep and deep[team]['received'] and e['player']==deep[team]['winger'] and t-deep[team]['t']<20:
      counts['deep_wing_shot']+=1;shot_context.append(('deep_wing',dict(deep[team])))
     if team in follow and e['player']==follow[team]['beneficiary'] and t-follow[team]['t']<12:
      counts['two_follow_shot']+=1;shot_context.append(('two_follow',dict(follow[team])))
     if counter and counter['team']==team and counter['progress'] and t-counter['t']<20:
      counts['exposed_counter_shot']+=1;shot_context.append(('exposed_counter',dict(counter)))
     if shot_context:examples.append({'t':t,'type':'shot','player':e['player'],'sequences':shot_context})
    if kind=='goal' and shot_context:
     for name,detail in shot_context:counts[name+'_goal']+=1
     examples.append({'t':t,'type':'goal','player':e['player'],'sequences':shot_context});shot_context=None
   if counter and sim.possession==counter['team'] and t-counter['t']<10 and not counter['progress']:
    advance=sim.local(sim.ball,counter['team'])[0]-counter['x']
    if advance>15 and any(abs(sim.ball[1]-sim.pos[i,1])<18 for i in counter['backs']):
     counter['progress']=True;counts['exposed_counter_progress']+=1;examples.append({'t':t,'type':'counter_progress','start':counter['t']})
 result={'seed':seed,'counts':dict(counts),'examples':examples};(Path(private)/f'seed_{seed}_qa.json').write_text(json.dumps(result,indent=2));return result
def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--out',required=True,help='New public tracking directory')
 p.add_argument('--private-out',required=True,help='Separate new private QA directory')
 p.add_argument('--num-files',type=int,default=10);p.add_argument('--seed',type=int,default=8100)
 p.add_argument('--workers',type=int,default=8);a=p.parse_args()
 public=Path(a.out).resolve();private=Path(a.private_out).resolve()
 if public==private or public in private.parents or private in public.parents:p.error('Keep public tracking and private QA in separate trees')
 if public.exists() or private.exists():p.error('Use new output directories')
 if a.num_files<1 or a.workers<1 or a.seed<0:p.error('Positive counts and nonnegative seed required')
 public.mkdir(parents=True);private.mkdir(parents=True,mode=0o700)
 with ProcessPoolExecutor(max_workers=a.workers) as pool:
  results=[]
  for r in pool.map(run,[(a.seed+i,str(public),str(private)) for i in range(a.num_files)]):
   results.append(r);print(r['seed'],r['counts'],flush=True)
 (private/'summary.json').write_text(json.dumps(results,indent=2))
if __name__=='__main__':main()
