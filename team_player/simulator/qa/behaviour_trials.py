"""Ten seeded ten-minute diagnostic trials per individual archetype.

Uniform league fixtures remain the normal generator. This controlled diagnostic
holds other players' profiles fixed and changes only the focal player's profile.
Private results/maps must stay outside the public tracking directory.
"""
import argparse,json,time
from contextlib import nullcontext
from dataclasses import replace
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from team_player.simulator.match import MatchSimulator
from team_player.simulator.engine import SimulationConfig
from team_player.config.styles import STRIKERS,FULLBACKS,WINGERS
from team_player.simulator.qa.behaviour_audit import BehaviourAudit

def trial(job):
 number,group,style,index,duration,tracking,result_dir=job
 cfg=SimulationConfig(seed=7100+index,duration=duration,hz=5,scenario='free_play',start=0,end=duration,formation_b='4231',striker_style='stays_up_front',fullback_style='sometimes_overlaps',winger_style='stays_wide')
 sim=MatchSimulator(cfg);i={'striker':10,'fullback':4,'winger':9}[group];profiles={'striker':STRIKERS,'fullback':FULLBACKS,'winger':WINGERS}[group]
 base={'striker':'stays_up_front','fullback':'sometimes_overlaps','winger':'stays_wide'}[group]
 jitter=sim.styles[i].top_speed-profiles[base].top_speed
 sim.styles[i]=replace(profiles[style],top_speed=profiles[style].top_speed+jitter)
 sim.speeds[i]=sim.styles[i].top_speed;sim.accelerations[i]=sim.styles[i].acceleration;sim.skill[i]=sim.styles[i].passing
 pid=sim.ids[i];meta=sim.metadata();meta.update(input_type='positions_only',match_id=f'trial_{number:04d}')
 audit=BehaviourAudit(meta,pid);engine={};filename=f'trial_{number:04d}.jsonl'
 with ((Path(tracking)/filename).open('x') if tracking else nullcontext(None)) as handle:
  if handle:handle.write(json.dumps(meta)+'\n')
  for rich in sim.frames():
   for e in rich['events']:engine[e['type']]=engine.get(e['type'],0)+1
   raw={'kind':'frame','t':rich['t'],'players':rich['players'],'ball':{k:rich['ball'][k] for k in ('x','y')}}
   if handle:handle.write(json.dumps(raw,separators=(',',':'))+'\n')
   audit.update(raw)
 result={'file':filename if tracking else None,'group':group,'style':style,'trial':index,'seed':cfg.seed,'duration':duration,'player':pid,**audit.result(),'engine_event_counts':engine}
 (Path(result_dir)/f'trial_{number:04d}.json').write_text(json.dumps(result,indent=2))
 return result

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True);p.add_argument('--tracking-out');p.add_argument('--duration',type=float,default=600);p.add_argument('--workers',type=int,default=8);p.add_argument('--only',default='');a=p.parse_args()
 out=Path(a.out).resolve();tracking=Path(a.tracking_out).resolve() if a.tracking_out else None
 if out.exists():p.error('Use a new result path')
 if a.workers<1:p.error('Positive worker count required')
 SimulationConfig(duration=a.duration).validate()
 if tracking:
  if tracking==out or tracking in out.parents:p.error('Private results must be outside public tracking')
  tracking.mkdir(parents=True,exist_ok=False)
 out.parent.mkdir(parents=True,exist_ok=True)
 result_dir=out.with_name(out.stem+'_checkpoints');result_dir.mkdir(exist_ok=False)
 conditions=[(g,s) for g,styles in [('striker',STRIKERS),('fullback',FULLBACKS),('winger',WINGERS)] for s in styles if not a.only or s==a.only]
 if not conditions:p.error('Unknown archetype')
 jobs=[(n,g,s,i,a.duration,str(tracking) if tracking else None,str(result_dir)) for n,(i,g,s) in enumerate((i,g,s) for i in range(10) for g,s in conditions)]
 results=[];start=time.monotonic()
 with ProcessPoolExecutor(max_workers=a.workers) as pool:
  for future in as_completed([pool.submit(trial,j) for j in jobs]):
   results.append(future.result());out.write_text(json.dumps(sorted(results,key=lambda r:(r['group'],r['style'],r['trial'])),indent=2));out.chmod(0o600)
   print(f'{len(results)}/{len(jobs)} trials completed in {time.monotonic()-start:.0f}s',flush=True)
if __name__=='__main__':main()
