"""Public tracking summaries and cautious cross-player comparison. No simulator imports."""
import argparse
import copy
import json
from pathlib import Path
from team_player.config.analysis import MIN_COMPARISON_POSSESSION_S
from team_player.utils.io import fingerprint

TIME_FIELDS=('observed_s','possession_s','opp_possession_s','deep_s','advanced_s','forward_offer_s','box_s','followed_s')
# Episode counts/values from the online tactical tracker (benefit AND cost per behaviour).
EVENT_FIELDS=('drop_n','drop_link_n','drop_link_xt','drop_xg_after','drop_box_absent_n','drop_delivery_n',
              'fb_n','fb_overlap_n','fb_ft_receptions','fb_turnover_n','fb_counter_xg','fb_counter_goal_n',
              'indiv_net','value_net','value_n',
              'def_rec_own','def_rec_team','def_rec_n','def_set_own','def_set_team','def_set_n',
              'run_n','decoy_n','decoy_drawn','decoy_space_pp','decoy_space_m2','decoy_used_n','run_xg_after')
FIELDS=TIME_FIELDS+EVENT_FIELDS
LABELS={'indiv_net':'His own value: position vs an average player in his role, minus the cost he caused (expected goals)',
        'value_net':'Team outcome while he did it, shared with team-mates (expected goals)','deep_s':'Deeper than role band','advanced_s':'Advanced from fullback','forward_offer_s':'Available forward passing option','box_s':'Central final-third presence','followed_s':'Off-ball following evidence',
        'drop_n':'Drop-deep episodes','drop_link_n':'Forward link passes from the pocket','drop_link_xt':'xT added by link passes','drop_xg_after':'Team xG after his drops',
        'drop_box_absent_n':'Deliveries with striker absent from box','fb_n':'Advances beyond midfield','fb_ft_receptions':'Final-third receptions',
        'fb_turnover_n':'Ball lost while he was advanced','fb_counter_xg':'Counter xG conceded after those losses',
        'run_n':'Off-ball runs','decoy_n':'Decoy runs (not found by the pass)','decoy_drawn':'Defenders drawn by decoys',
        'decoy_space_pp':'Team-mate space value created (pp)','decoy_space_m2':'Team-mate space created (m²)','decoy_used_n':'Decoy space used within 4 s'}
EVENT_METRICS={'striker':['indiv_net','value_net','drop_n','drop_link_n','drop_link_xt','drop_xg_after','drop_box_absent_n'],
               'fullback':['indiv_net','value_net','fb_n','fb_ft_receptions','fb_turnover_n','fb_counter_xg'],
               'winger':['indiv_net','value_net','run_n','decoy_n','decoy_drawn','decoy_space_m2','decoy_used_n']}

def add_episode(row,e):
    """Credit one completed tactical episode to the player's row."""
    k=e['kind']
    if e.get('value') and k in ('striker_drop','fullback_advance','winger_run'):
        row['value_net']+=e['value']['net'];row['value_n']+=1
        row['indiv_net']+=(e.get('individual') or {}).get('net',0.)
    if k in ('fullback_recovery','fullback_defence'):
        key='def_rec' if k=='fullback_recovery' else 'def_set'
        row[key+'_own']+=e['individual']['net'];row[key+'_team']+=e['value']['net'];row[key+'_n']+=1
    if k=='striker_drop':
        row['drop_n']+=1;lp=e.get('link_pass');po=e.get('possession_outcome') or {}
        if lp:row['drop_link_n']+=1;row['drop_link_xt']+=max(0.,lp['delta_xt'])
        row['drop_xg_after']+=po.get('xg',0.) or 0.
        if e.get('delivery'):
            row['drop_delivery_n']+=1;row['drop_box_absent_n']+=int(not e['delivery']['striker_in_box'])
    elif k=='fullback_advance':
        row['fb_n']+=1;row['fb_overlap_n']+=int(e.get('overlap_type')=='overlap');row['fb_ft_receptions']+=e.get('final_third_receptions',0)
        c=e.get('counter')
        if c:row['fb_turnover_n']+=1;row['fb_counter_xg']+=c['xg_against'];row['fb_counter_goal_n']+=int(c['goal_against'])
    elif k=='winger_run':
        row['run_n']+=1;row['run_xg_after']+=e.get('xg_within_4s',0.) or 0.
        if e['decoy']:
            row['decoy_n']+=1;row['decoy_drawn']+=len(e['drawn_defenders']);row['decoy_space_pp']+=100*max(0.,e.get('space_created_obso') or 0.);row['decoy_space_m2']+=max(0.,e.get('space_created_m2') or 0.)
            row['decoy_used_n']+=int(e.get('space_used',False))

def family(role):
    if 'ST' in role:return 'striker'
    if role in ('LW','RW','LM','RM'):return 'winger'
    if role in ('LB','RB'):return 'fullback'
    return 'unsupported'

class Accumulator:
    """Left-endpoint integration; only already-observed intervals are credited."""
    def __init__(self,meta):
        self.meta=meta;self.previous=None;self.t=0.
        self.rows={p['id']:{**p,**dict.fromkeys(FIELDS,0.)} for p in meta['players']}
    def update(self,frame,analysis,tactics=None):
        for e in (tactics or {}).get('completed',()):
            if e.get('player') in self.rows:add_episode(self.rows[e['player']],e)
        if analysis is None:return
        t=float(frame['t'])
        if self.previous and t<=self.t:raise ValueError('Non-increasing comparison sample')
        if self.previous:
            dt=min(2.,t-self.t)
            for pid,flags in self.previous.items():
                for key,value in flags.items():self.rows[pid][key]+=dt*value
        positions={p['id']:p for p in frame['players']}
        directions={t['id']:t['attack_direction'] for t in self.meta['teams']}
        followed={f['runner'] for f in analysis['following'] if f['interpretation']=='following_evidence'}
        self.previous={}
        for p in analysis['players']:
            pid=p['player'];pos=positions[pid];own=frame['possession']==p['team']
            x=pos['x'] if directions[p['team']]==1 else 105-pos['x'];option=p.get('pass_option') or {}
            self.previous[pid]={'observed_s':1,'possession_s':own,'opp_possession_s':bool(frame['possession']) and not own,
                'deep_s':own and p['excursion']=='striker_dropping_deep',
                'advanced_s':own and p['excursion']=='fullback_advanced',
                'forward_offer_s':own and option.get('accessible',False) and option.get('forward_m',0)>3,
                'box_s':own and x>80 and abs(pos['y']-34)<15,
                'followed_s':own and pid in followed}
        self.t=t

class History:
    def __init__(self,folder,active):
        self.active=fingerprint(active);self.matches=[];self.roster={}
        roster=Path(folder)/'roster.json';archive=Path(folder)/'comparison_history.json'
        if roster.exists():
            for p in json.loads(roster.read_text())['players']:
                self.roster[p['id']]={k:p[k] for k in ('id','team','role')}
        if archive.exists():
            data=json.loads(archive.read_text())
            if data.get('version') not in (1,2):raise ValueError('Unsupported comparison history')
            self.matches=data['matches']
        # Exclude this complete recording even when renamed. Its live prefix is added separately.
        self.matches=[m for m in self.matches if m['fingerprint']!=self.active]
        for m in self.matches:
            for p in m['players']:self.roster[p['id']]={k:p[k] for k in ('id','team','role')}
    def rows(self,current):
        rows={pid:{**p,**dict.fromkeys(FIELDS,0.),'recordings':0} for pid,p in self.roster.items()}
        for players in [*[m['players'] for m in self.matches],list(current.rows.values())]:
            for p in players:
                row=rows.setdefault(p['id'],{**{k:p[k] for k in ('id','team','role')},**dict.fromkeys(FIELDS,0.),'recordings':0})
                if row['role']!=p['role']:raise ValueError('Player role changed between recordings')
                for key in FIELDS:row[key]+=p.get(key,0.)
                row['recordings']+=int(p['observed_s']>0)
        return rows

def compare(rows,left,right,t,current=None):
    """rows: history totals per player. current: this match so far (player -> row), if live."""
    if left not in rows or right not in rows:raise ValueError('Unknown comparison player')
    a,b=copy.deepcopy(rows[left]),copy.deepcopy(rows[right]);fa,fb=family(a['role']),family(b['role'])
    compatible=fa==fb and fa!='unsupported' and left!=right
    warnings=[]
    if left==right:warnings.append('Choose two different players.')
    elif not compatible:warnings.append('These roles do not share the supported contribution measures. Choose two strikers, two wingers or two fullbacks; a goalkeeper comparison is unsuitable here.')
    if min(a['possession_s'],b['possession_s'])<MIN_COMPARISON_POSSESSION_S:warnings.append('Limited evidence: at least one player has under two minutes of observed team possession. Do not rank them.')
    if not a['observed_s'] or not b['observed_s']:warnings.append('No observed minutes for at least one selection. Generate and analyse more uniformly sampled matches.')
    metrics=['forward_offer_s','box_s']
    if compatible:metrics=({'striker':['deep_s'],'fullback':['advanced_s'],'winger':['followed_s']}[fa])+metrics
    events=EVENT_METRICS.get(fa,[]) if compatible else []
    for p in (a,b):
        p['percentages']={k:round(100*p[k]/p['possession_s'],1) if p['possession_s'] else None for k in metrics}
        # Episode measures per 10 minutes of observed team possession.
        p['rates']={k:round(600*p.get(k,0.)/p['possession_s'],3) if p['possession_s'] else None for k in events}
    def ratio(p,num,den):
        return round(p.get(num,0)/p[den],2) if p.get(den) else None
    for p in (a,b):
        c=(current or {}).get(p['id'])
        p['current']=None if c is None else {'possession_s':c['possession_s'],'observed_s':c['observed_s'],
            'percentages':{k:round(100*c[k]/c['possession_s'],1) if c['possession_s'] else None for k in metrics},
            'rates':{k:round(600*c.get(k,0.)/c['possession_s'],3) if c['possession_s'] else None for k in events},
            'own_value':round(c.get('indiv_net',0.),4),'team_outcome':round(c.get('value_net',0.),4),'episodes':c.get('value_n',0),
            **{k:c.get(k,0.) for k in ('opp_possession_s','def_rec_own','def_set_own','def_rec_team','def_set_team')}}
    if compatible and fa=='striker':
        for p in (a,b):p['tradeoff']={'own_value_per_episode':ratio(p,'indiv_net','value_n'),'team_value_per_episode':ratio(p,'value_net','value_n'),'link_rate':ratio(p,'drop_link_n','drop_n'),'xt_per_drop':ratio(p,'drop_link_xt','drop_n'),
                                       'xg_after_per_drop':ratio(p,'drop_xg_after','drop_n'),'box_absent_rate':ratio(p,'drop_box_absent_n','drop_delivery_n')}
    elif compatible and fa=='fullback':
        for p in (a,b):p['tradeoff']={'own_value_per_episode':ratio(p,'indiv_net','value_n'),'team_value_per_episode':ratio(p,'value_net','value_n'),'ft_receptions_per_advance':ratio(p,'fb_ft_receptions','fb_n'),'loss_rate_when_advanced':ratio(p,'fb_turnover_n','fb_n'),
                                       'counter_xg_per_loss':ratio(p,'fb_counter_xg','fb_turnover_n')}
    elif compatible and fa=='winger':
        for p in (a,b):p['tradeoff']={'own_value_per_episode':ratio(p,'indiv_net','value_n'),'team_value_per_episode':ratio(p,'value_net','value_n'),'decoy_share':ratio(p,'decoy_n','run_n'),'drawn_per_decoy':ratio(p,'decoy_drawn','decoy_n'),
                                       'space_m2_per_decoy':ratio(p,'decoy_space_m2','decoy_n'),'used_rate':ratio(p,'decoy_used_n','decoy_n')}
    from team_player.agents.narration import balance_from_row, defence_from_row
    for p in (a,b):
        p['balance']=balance_from_row(p,'across his history matches') if compatible else None
        p['current_balance']=balance_from_row({**(p['current'] or {}),'id':p['id'],'role':p['role'],'indiv_net':(p['current'] or {}).get('own_value',0),
                                               'value_net':(p['current'] or {}).get('team_outcome',0)},'in this match') if compatible and p.get('current') else None
        p['defence']=defence_from_row(p,'across his history matches') if compatible else None
        p['current_defence']=defence_from_row({**p['current'],'id':p['id'],'role':p['role']},'in this match') if compatible and p.get('current') else None
    candidates=[{'id':'context','text':'Different opponents, possession phases and sample sizes limit this comparison. These observations do not establish which player caused better results.'}]
    if compatible:
        candidates.append({'id':'tradeoff','text':{'striker':'Read deeper support alongside central final-third presence: coming back can create an outlet while leaving less presence near goal. More deep time alone is not a benefit.', 'winger':'Following evidence can accompany a useful off-ball run, but does not establish that defenders were deliberately attracted or that the resulting space was used.', 'fullback':'Read advanced time alongside passing availability. Advancing can add an outlet, but these figures alone do not measure cover behind the player.'}[fa]})
        if a['possession_s'] and b['possession_s']:
            k=metrics[0];candidates.append({'id':'difference','text':f"{LABELS[k]}: {left} {a['percentages'][k]}% versus {right} {b['percentages'][k]}% of observed team possession. This is a descriptive difference, not a performance ranking."})
    peers=[p['id'] for p in rows.values() if family(p['role'])==fa and fa!='unsupported' and p['id']!=left and p['observed_s']>0]
    if compatible and a.get('tradeoff') and b.get('tradeoff'):
        ta,tb=a['tradeoff'],b['tradeoff']
        text={'striker':lambda:f"Per drop: {left} adds xT {ta['xt_per_drop']} and is absent from the box at {ta['box_absent_rate']} of later deliveries; {right}: xT {tb['xt_per_drop']}, absent {tb['box_absent_rate']}.",
              'fullback':lambda:f"When advanced, {left} loses the ball in {ta['loss_rate_when_advanced']} of advances (counter xG {ta['counter_xg_per_loss']} per loss); {right}: {tb['loss_rate_when_advanced']} ({tb['counter_xg_per_loss']}).",
              'winger':lambda:f"{left}: {ta['decoy_share']} of runs are decoys, {ta['drawn_per_decoy']} defenders drawn and {ta['space_m2_per_decoy']} m² space each; {right}: {tb['decoy_share']}, {tb['drawn_per_decoy']}, {tb['space_m2_per_decoy']} m²."}[fa]()
        candidates.append({'id':'benefit_cost','text':text+' None-values mean no qualifying episodes yet.'})
    for p in (a,b):
        for key in ('current_balance','balance','current_defence','defence'):
            if p.get(key):candidates.append({'id':f"{p['id']}_{key}",'text':p[key]['text']})
    return {'t':t,'left':a,'right':b,'compatible':compatible,'warnings':warnings,'metrics':[{ 'key':k,'label':LABELS[k]} for k in metrics],
            'event_metrics':[{'key':k,'label':LABELS.get(k,k),'unit':'per 10 min possession'} for k in events],
            'suggested_peers':peers[:8],'advice':candidates,'source':'Completed public recordings plus this live prefix; active recording future excluded.'}

def build_history(folder):
    from team_player.schema import validate_metadata
    from team_player.analyser.live import LivePipeline
    from team_player.analyser.presentation import Preferences
    folder=Path(folder);matches=[];seen=set()
    for path in sorted(folder.glob('match_*.jsonl')):
        key=fingerprint(path)
        if key in seen:continue
        seen.add(key)
        with path.open() as f:
            meta=json.loads(next(f));validate_metadata(meta)
            pipeline=LivePipeline(meta);acc=Accumulator(meta)
            pref=Preferences(player=next(p['id'] for p in meta['players'] if 'ST' in p['role']))
            for line in f:
                packet=pipeline.update(json.loads(line),pref);acc.update(packet['frame'],packet['analysis'],packet['tactics'])
            acc.update(packet['frame'],None,{'completed':pipeline.finish()})
        matches.append({'fingerprint':key,'file':path.name,'players':list(acc.rows.values())})
    target=folder/'comparison_history.json'
    target.write_text(json.dumps({'version':2,'matches':matches},indent=2));return target

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Summarise completed public recordings for comparison. No private maps needed.')
    parser.add_argument('--input',required=True)
    print(build_history(parser.parse_args().input))
