"""Online inference from x/y only. No simulator events, owners or styles are read."""
import math
import numpy as np
from team_player.schema import validate_frame, validate_metadata


class TrackingObserver:
    def __init__(self, metadata):
        validate_metadata(metadata)
        self.meta=metadata;self.players={p['id']:p for p in metadata['players']}
        self.ids=list(self.players);self.previous=None;self.owner=None
        self.candidate=None;self.candidate_since=0.;self.last_owner=None
        self.release=None;self.pressure_since=None;self.last_pressure=-100.
        self.directions={t['id']:t['attack_direction'] for t in metadata['teams']}

    def update(self, raw):
        # Explicit projection prevents even accidentally supplied engine labels leaking in.
        frame={'kind':'frame','t':raw['t'],
            'players':[{k:p[k] for k in ('id','x','y')} for p in raw['players']],
            'ball':{k:raw['ball'][k] for k in ('x','y')},'possession':None,'events':[]}
        validate_frame(frame,self.meta,self.previous['t'] if self.previous else None)
        t=frame['t'];lookup={p['id']:p for p in frame['players']}
        positions=np.array([[lookup[i]['x'],lookup[i]['y']] for i in self.ids])
        ball=np.array([frame['ball']['x'],frame['ball']['y']])
        velocity=np.zeros(2);pv=np.zeros_like(positions);dt=0.
        if self.previous:
            dt=t-self.previous['t'];velocity=(ball-self.previous['ball'])/dt
            pv=(positions-self.previous['positions'])/dt
        distances=np.linalg.norm(positions-ball,axis=1);order=np.argsort(distances)
        nearest=int(order[0]);pid=self.ids[nearest]
        stable=np.linalg.norm(velocity-pv[nearest])<5 and np.linalg.norm(velocity)<12
        candidate=pid if distances[nearest]<=1.05 and stable else None
        if candidate and distances[order[1]]-distances[nearest]<.15 and candidate!=self.owner:
            candidate=None  # Ambiguous simultaneous contact does not establish control.
        if candidate!=self.candidate:self.candidate=candidate;self.candidate_since=t
        old_owner=self.owner
        self.owner=candidate if candidate and (candidate==old_owner or t-self.candidate_since>=.2-1e-6) else None
        events=[]
        def emit(kind,**kw):events.append({'type':kind,'source':'tracking_inference',**kw})
        if old_owner and not self.owner and np.linalg.norm(velocity)>8:
            team=self.players[old_owner]['team'];direction=self.directions[team]
            goal_x=105 if direction==1 else 0
            arrival=(goal_x-ball[0])/velocity[0] if abs(velocity[0])>1e-6 else -1
            aim_y=ball[1]+arrival*velocity[1]
            is_shot=direction*velocity[0]>7 and 0<arrival<3 and abs(aim_y-34)<8.5 and (ball[0] if direction==1 else 105-ball[0])>62
            kind='possible_shot' if is_shot else 'pass_attempt'
            self.release={'t':t,'player':old_owner,'team':team,'kind':kind}
            emit(kind,player=old_owner,confidence='tentative' if is_shot else 'moderate')
        if self.owner and self.owner!=self.last_owner:
            team=self.players[self.owner]['team']
            if self.last_owner:
                old_team=self.players[self.last_owner]['team']
                emit('reception' if team==old_team else 'possession_change',player=self.owner,team=team,confidence='moderate')
            else:emit('control_established',player=self.owner,team=team,confidence='moderate')
            self.last_owner=self.owner;self.release=None
        # Goal inference: a fast ball reaching the goal line between the posts. x/y only;
        # height is unobservable, so this is labelled "possible".
        prev_speed=getattr(self,'prev_ball_speed',0.)
        if abs(ball[1]-34)<=3.66 and (ball[0]>=104.3 or ball[0]<=.7) and prev_speed>6 and t-getattr(self,'last_goal',-100.)>10:
            team=next(k for k,d in self.directions.items() if (d==1)==(ball[0]>52.5))
            scorer=self.release['player'] if self.release and self.release['team']==team else None
            emit('possible_goal',team=team,player=scorer,confidence='tentative');self.last_goal=t
        self.prev_ball_speed=float(np.linalg.norm(velocity))
        state='loose';possession=None
        if self.owner:
            state='controlled';possession=self.players[self.owner]['team']
            opponents=[i for i,p in enumerate(self.ids) if self.players[p]['team']!=possession]
            close=[self.ids[i] for i in opponents if np.linalg.norm(positions[i]-ball)<3]
            if close:
                if self.pressure_since is None:self.pressure_since=t
                if t-self.pressure_since>=.4 and t-self.last_pressure>=2:
                    emit('pressure',player=self.owner,opponents=close,confidence='geometric');self.last_pressure=t
            else:self.pressure_since=None
        elif self.release and t-self.release['t']<3 and np.linalg.norm(velocity)>6:
            state='shot' if self.release['kind']=='possible_shot' else 'pass';possession=self.release['team']
        else:self.pressure_since=None
        frame['ball'].update(owner=self.owner,state=state)
        frame['possession']=possession;frame['events']=events
        frame['observation']={'ball_speed_m_s':round(float(np.linalg.norm(velocity)),3),
                              'control':'inferred' if self.owner else 'unestablished'}
        self.previous={'t':t,'positions':positions,'ball':ball}
        return frame


class PossessionGate:
    """Causal qualification; never retroactively emits highlights for earlier frames."""
    def __init__(self, minimum_seconds=5., gap_tolerance=1.5):
        if not math.isfinite(minimum_seconds) or minimum_seconds<=0:raise ValueError('Positive minimum required')
        self.minimum=minimum_seconds;self.gap=gap_tolerance
        self.team=None;self.start=None;self.last_control=None;self.previous=None;self.controlled=0.;self.episode=0

    def update(self,frame):
        t=frame['t'];dt=0 if self.previous is None else t-self.previous
        if dt<0:raise ValueError('Nonmonotonic possession stream')
        self.previous=t
        team=frame['possession'] if frame['ball']['owner'] else None
        if team:
            if self.team!=team or self.last_control is None or t-self.last_control>self.gap:
                self.episode+=1;self.team=team;self.start=t;self.controlled=0.
            else:self.controlled+=dt
            self.last_control=t
        elif self.last_control is not None and t-self.last_control>self.gap:
            self.team=None;self.start=None;self.controlled=0.
        active=self.team is not None and frame['possession']==self.team
        status='sustained' if active and self.controlled>=self.minimum else 'developing' if active else 'unsettled'
        return {'episode_id':self.episode,'team':self.team,'started_at':self.start,
                'controlled_seconds':round(self.controlled,2),'status':status,'eligible':status=='sustained',
                'decision_at':t,'minimum_seconds':self.minimum}
