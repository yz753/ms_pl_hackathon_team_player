"""Observational behaviour checks consuming only public positions, never intentions.

Thresholds are demonstration diagnostics, not calibrated causal football metrics.
"""
from collections import Counter,deque
import numpy as np
from team_player.analyser.observation import TrackingObserver

class BehaviourAudit:
    def __init__(self,metadata,player):
        self.observer=TrackingObserver(metadata);self.ids=[p['id'] for p in metadata['players']]
        self.i=self.ids.index(player);self.player=player;self.meta=metadata
        self.team=metadata['players'][self.i]['team'];self.role=metadata['players'][self.i]['role']
        self.direction=next(t['attack_direction'] for t in metadata['teams'] if t['id']==self.team)
        self.own=[j for j,p in enumerate(metadata['players']) if p['team']==self.team]
        self.opp=[j for j in range(22) if j not in self.own]
        self.outfield=[j for j in self.opp if metadata['players'][j]['role']!='GK']
        self.count=Counter();self.examples={};self.history=deque();self.last_sample=-1;self.last={}
        self.pending=None;self.deep_since=None;self.deep_received=False;self.deep_return=None
        self.space_windows=[];self.exposure=None;self.last_own=False;self.last_deep_position=-100.
    def add(self,key,t,cooldown=8):
        if t-self.last.get(key,-100)<cooldown:return
        self.count[key]+=1;self.last[key]=t
        self.examples.setdefault(key,round(t,1))
    def localx(self,x):return x if self.direction==1 else 105-x
    def update(self,raw):
        f=self.observer.update(raw);t=f['t'];positions=np.array([[p['x'],p['y']] for p in f['players']]);ball=np.array([f['ball']['x'],f['ball']['y']]);own=f['possession']==self.team
        x=self.localx(positions[self.i,0]);bx=self.localx(ball[0]);owner=f['ball']['owner']
        if 'ST' in self.role:
            if own:
                if x<58:self.last_deep_position=t
                if t-self.last_deep_position<20 and bx>80 and x<75:
                    self.add('front_absence_after_deep_position',t)
            elif f['possession'] is not None:self.last_deep_position=-100.
        for e in f['events']:
            if e['type']=='pass_attempt' and e['player']==self.player:
                self.pending=(t,positions[self.i].copy(),x)
                if x<58:self.add('deep_pass_attempt',t,1)
            if e['type'] in ('reception','control_established','possession_change'):
                recipient=e['player'];j=self.ids.index(recipient)
                if recipient==self.player and own:
                    if x<58:self.deep_received=True;self.add('deep_reception',t,1)
                    if self.role in ('LB','RB') and x>70:self.add('advanced_reception',t,1)
                if self.pending:
                    start,point,depth=self.pending
                    if t-start<5 and depth<58:
                        if j in self.own and j!=self.i:
                            advance=self.direction*(positions[j,0]-point[0]);length=np.linalg.norm(positions[j]-point)
                            if advance>5:
                                self.add('deep_completed_forward_link',t,1);self.deep_return=t
                                if self.meta['players'][j]['role'] in ('LW','RW','LM','RM'):
                                    self.add('deep_forward_winger_link',t,1)
                                    if length>30:self.add('deep_long_winger_link',t,1)
                            elif advance< -3:self.add('deep_backward_recycle',t,1)
                            if self.meta['players'][j]['role']=='GK':self.add('deep_back_to_keeper',t,1)
                        elif j in self.opp:self.add('deep_pass_lost',t,1)
                    self.pending=None
                for w in self.space_windows:
                    if not w['used'] and j==w['teammate'] and t<=w['until'] and np.linalg.norm(positions[j]-w['point'])<8:
                        self.add('opened_space_received_in',t);w['used']=True
                        if w['forward'] and w['same_possession'] and not w['forward_used']:
                            self.add('forward_space_used',t);w['forward_used']=True
        if own and x<55 and 'ST' in self.role:
            if self.deep_since is None:self.deep_since=t;self.deep_received=owner==self.player
        elif self.deep_since is not None:
            if t-self.deep_since>=3:
                self.add('deep_excursion',t,1)
                if not self.deep_received:self.add('deep_no_reception',t,1)
            self.deep_since=None
        if self.deep_return is not None:
            if own and bx>80 and x<75 and t-self.deep_return>3:self.add('striker_absent_from_front',t);self.deep_return=None
            elif t-self.deep_return>20 or not own:self.deep_return=None
        if self.role in ('LB','RB'):
            if own and x>70 and owner!=self.player:
                vector=positions[self.i]-ball;length=np.linalg.norm(vector)
                if length>3:
                    u=np.clip((positions[self.opp]-ball)@vector/(length*length),0,1)
                    lane=np.min(np.linalg.norm(positions[self.opp]-(ball+u[:,None]*vector),axis=1))
                    if lane>2:
                        self.add('advanced_open_outlet',t)
                        second_last=sorted(self.localx(positions[j,0]) for j in self.opp)[-2]
                        if x<=max(bx,second_last):self.add('advanced_onside_outlet',t)
            if self.last_own and not own and x>65:
                self.exposure={'until':t+10,'ballx':bx,'y':positions[self.i,1]}
            if self.exposure and f['possession'] is not None and not own:
                w=self.exposure
                if t<=w['until'] and bx<x-5 and abs(ball[1]-w['y'])<18:
                    self.add('turnover_space_behind_fullback',t)
                    if bx<w['ballx']-8:self.add('opponent_progress_behind_fullback',t);self.exposure=None
            if self.exposure and (t>self.exposure['until'] or own):self.exposure=None
        self.last_own=own
        for w in list(self.space_windows):
            if f['possession'] is not None and not own:w['same_possession']=False
            j=w['teammate']
            if (w['forward'] and w['same_possession'] and not w['forward_used'] and t<=w['until']
                    and owner==self.ids[j] and self.direction*(positions[j,0]-w['point'][0])>5
                    and np.linalg.norm(positions[j]-w['point'])<12):
                self.add('forward_space_used',t);w['forward_used']=True
            if t>w['until']:
                if w['forward'] and not w['forward_used']:self.add('forward_space_not_used',t)
                if not w['used']:self.add('opened_space_not_used',t)
                self.space_windows.remove(w)
        if t-self.last_sample<.5-1e-6:return
        self.last_sample=t
        self.history.append((t,positions.copy(),ball.copy(),owner,own))
        while self.history and self.history[0][0]<t-3.1:self.history.popleft()
        if len(self.history)<6 or not all(h[4] and h[3]!=self.player for h in self.history):return
        old=self.history[0];before=old[1];run=positions[self.i]-before[self.i];length=np.linalg.norm(run)
        if length<5:return
        self.add('off_ball_run_window',t)
        shared=np.median(positions[self.outfield]-before[self.outfield],axis=0)
        following=[]
        for j in self.outfield:
            movement=positions[j]-before[j]-shared;m=np.linalg.norm(movement)
            align=run@movement/max(length*m,1e-9)
            d0=np.linalg.norm(before[j]-before[self.i]);d1=np.linalg.norm(positions[j]-positions[self.i])
            bm=ball-old[2];ball_align=movement@bm/max(m*np.linalg.norm(bm),1e-9)
            if m>2 and align>.65 and d1<14 and d1<d0+3 and not (np.linalg.norm(bm)>3 and ball_align>.85):following.append(j)
        if not following:self.add('run_without_following_evidence',t);return
        self.add('following_evidence',t)
        opened=False
        for j in self.own:
            if j==self.i or self.meta['players'][j]['role']=='GK':continue
            point=before[j];d0=np.linalg.norm(before[self.opp]-point,axis=1);d1=np.linalg.norm(positions[self.opp]-point,axis=1)
            nearest=self.opp[int(np.argmin(d0))]
            if nearest in following and 1<min(d0)<10 and min(d1)-min(d0)>2 and np.linalg.norm(positions[j]-point)<8:
                opened=True;self.add('following_with_teammate_space_gain',t)
                vector=positions[j]-ball;distance=np.linalg.norm(vector)
                u=np.clip((positions[self.opp]-ball)@vector/max(distance*distance,.01),0,1)
                lane=np.min(np.linalg.norm(positions[self.opp]-(ball+u[:,None]*vector),axis=1))
                forward=self.localx(point[0])>max(50,bx-5) and (owner==self.ids[j] or (distance>3 and lane>1.3))
                if forward:self.add('forward_following_space_gain',t)
                if not any(w['teammate']==j for w in self.space_windows):
                    self.space_windows.append({'teammate':j,'point':point,'until':t+8,'used':False,'forward':bool(forward),'same_possession':True,'forward_used':False})
        if not opened:self.add('following_without_space_gain',t)
    def result(self):return {'counts':dict(self.count),'example_seconds':self.examples}
