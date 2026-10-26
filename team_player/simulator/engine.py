"""Private football engine: contextual intentions, separate ball physics, style profiles.

Not a calibrated match engine. No hidden intentions/styles reach the analyser.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
import math
import numpy as np
from team_player.schema import LENGTH, WIDTH, SCHEMA_VERSION
from team_player.config.league import player_id, FORMATIONS, ANCHORS, SCENARIOS, VARIANTS, TEAMS, TEMPOS, MATCH_DURATION_S
from team_player.config.styles import STRIKERS, FULLBACKS, WINGERS
from team_player.simulator.league import squad_styles, team_styles
from team_player.utils.geometry import norm, limit


@dataclass
class SimulationConfig:
    team_names: tuple[str, str] = ('A', 'B')
    league_seed: int | None = None
    seed: int = 1
    duration: float = 45.0
    hz: int = 10
    formation_a: str = '4231'
    formation_b: str = '433'
    scenario: str = 'drop_deep'
    variant: str = 'mixed'
    intervention: bool = True
    start: float = 2.0
    end: float = 28.0
    turnover_at: float | None = None
    striker_style: str = 'drops_and_returns'
    fullback_style: str = 'often_overlaps'
    winger_style: str = 'decoy_runner'
    build_up_route: str = 'wing_release'
    acquisition: str = 'goalkeeper'
    fullback_side: str = 'RB'
    match_tempo: str = 'mixed'

    def validate(self):
        if len(self.team_names)!=2 or len(set(self.team_names))!=2 or any(t not in TEAMS for t in self.team_names): raise ValueError('Choose two distinct teams A–T')
        if self.league_seed is not None and self.league_seed<0: raise ValueError('Nonnegative league seed required')
        if self.match_tempo not in TEMPOS: raise ValueError('Unknown match tempo')
        if self.fullback_side not in ('LB','RB'): raise ValueError('Unknown fullback side')
        if self.acquisition not in ('goalkeeper', 'turnover'): raise ValueError('Unknown acquisition')
        if not 5 <= self.hz <= 100: raise ValueError('hz must be between 5 and 100')
        if not math.isfinite(self.duration) or not 0 < self.duration <= MATCH_DURATION_S: raise ValueError(f'duration must be in (0,{MATCH_DURATION_S:g}]')
        if self.scenario not in SCENARIOS or self.variant not in VARIANTS: raise ValueError('Unknown scenario or variant')
        if self.formation_a not in FORMATIONS or self.formation_b not in FORMATIONS: raise ValueError('Unknown formation')
        if not (math.isfinite(self.start) and math.isfinite(self.end) and 0 <= self.start < self.end): raise ValueError('Require finite 0 <= start < end')
        if self.turnover_at is not None and not 0 < self.turnover_at < self.duration: raise ValueError('Invalid turnover time')
        if self.striker_style not in STRIKERS or self.fullback_style not in FULLBACKS or self.winger_style not in WINGERS: raise ValueError('Unknown style')
        if self.build_up_route not in ('wing_release','return_run'): raise ValueError('Unknown build-up route')


class Simulator:
    def __init__(self, config):
        config.validate()
        self.cfg = config
        self.dt = 1/config.hz
        self.substeps = math.ceil(50/config.hz)
        self.h = self.dt/self.substeps
        self.rng = np.random.default_rng(config.seed)
        self.roles = FORMATIONS[config.formation_a] + FORMATIONS[config.formation_b]
        self.ids = [player_id(t, f, i) for t, f in zip(config.team_names, (config.formation_a, config.formation_b)) for i in range(11)]
        self.teams = np.repeat([0,1],11)
        self.directions = np.where(self.teams == 0,1,-1)
        self.base = np.array([ANCHORS[r] for r in self.roles],float)
        self.styles = squad_styles(self.roles, config.striker_style, config.fullback_style, config.winger_style, self.rng)
        if config.league_seed is not None:
            self.styles = team_styles(config.team_names[0], self.roles[:11], config.league_seed) + team_styles(config.team_names[1], self.roles[11:], config.league_seed)
        self.speeds = np.array([p.top_speed for p in self.styles])
        self.accelerations = np.array([p.acceleration for p in self.styles])
        self.acceleration = float(self.accelerations.max())
        self.skill = np.array([p.passing for p in self.styles])
        self.pos = self.base.copy()
        # Build-up starts from the goalkeeper, with a compact but separated shape.
        self.pos[:11,0] -= np.array([0 if r=='GK' else 12 for r in self.roles[:11]])
        self.pos[11:,0] = 105 - (self.pos[11:,0] - 5)
        self.pos[11:,1] = 68 - self.pos[11:,1]
        self.pos += self.rng.normal(0,.35,(22,2))
        self.pos = np.clip(self.pos,[1,1],[104,67])
        self.vel = np.zeros((22,2))
        self.noise = np.zeros((22,2))
        n = round(config.duration*config.hz)+1
        self.random_motion = self.rng.normal(size=(n,22,2))
        self.random_events = self.rng.random((n,22,5))
        self.owner = 0 if config.acquisition == 'goalkeeper' else int(self.rng.choice([5,6,7,8,9]))
        self.ball = self.pos[self.owner].copy()
        self.ball_velocity = np.zeros(2)
        self.ball_state = 'controlled'
        self.possession = 0
        self.last_team = 0
        self.flight = None
        self.hold_time = 0.
        self.release_time = -100.
        self.last_kicker = None
        self.last_receiver = None
        self.score = [0,0]
        self.events = [{'type':'possession_start','team':config.team_names[0],'source':config.acquisition}]
        self.private = []
        self.finished = False
        self.termination = None
        self.last_contest_step = -1
        self.forced_turnover_done = False
        self.state = ['shape']*22
        self.state_since = np.zeros(22)
        self.cooldown = np.zeros(22)
        self.return_hold_until = np.zeros(22)
        self.pockets = self.pos.copy()
        self.run_destinations = self.pos.copy()
        self.target = self.pos.copy()
        self.presser = {0: None, 1: None}
        self.last_decision = -100.
        self.last_step = 0
        self.focus = next(i for i,r in enumerate(self.roles[:11]) if 'ST' in r)
        if config.scenario == 'overlap': self.focus=self.roles[:11].index(config.fullback_side)
        if config.scenario == 'decoy': self.focus=next(i for i,r in enumerate(self.roles[:11]) if r in ('RW','RM'))
        self.phase = np.r_[np.ones(11), np.zeros(11)]

    def local(self, point, team):
        return np.asarray(point).copy() if team==0 else np.array([105.,68.])-point

    def world(self, point, team):
        return self.local(point,team)

    def metadata(self):
        return {'kind':'metadata','schema_version':SCHEMA_VERSION,'pitch':{'length':LENGTH,'width':WIDTH},
                'sample_hz':self.cfg.hz,
                'teams':[{'id':self.cfg.team_names[0],'attack_direction':1,'formation':self.cfg.formation_a},
                         {'id':self.cfg.team_names[1],'attack_direction':-1,'formation':self.cfg.formation_b}],
                'players':[{'id':pid,'team':self.cfg.team_names[self.teams[i]],'role':self.roles[i]} for i,pid in enumerate(self.ids)]}

    def emit(self, kind, **kwargs):
        self.events.append({'type':kind,**kwargs})

    def set_state(self,i,state,time):
        if self.state[i]!=state:
            self.state[i]=state
            self.state_since[i]=time
            if state=='return' and self.styles[i].return_urgency<.4:
                urgency=self.styles[i].return_urgency
                # Sometimes retain a midfield support role before recovering the front line.
                delay=float(self.rng.uniform(6,14)) if self.rng.random()>urgency else 0.
                self.return_hold_until[i]=time+delay

    def allowed(self,i,time):
        if not self.cfg.intervention or self.cfg.scenario=='ordinary' or not self.cfg.start<=time<self.cfg.end:
            return False
        return self.teams[i]==0 and (self.cfg.scenario=='free_play' or i==self.focus)

    def choose_pocket(self,i):
        team=self.teams[i]
        ball=self.local(self.ball,team)
        # Offer in front of the build-up player, not on top of them. Half-space
        # candidates are scored for clearance and pass visibility, then latched.
        x=np.clip(ball[0]+12,24,43)
        candidates=np.array([self.world(np.array([x+dx,y]),team) for dx in (-3,3) for y in (20,29,39,48)])
        others=np.arange(22)!=i
        distance=norm(candidates[:,None,:]-self.pos[None,others,:]).min(axis=1)
        scores=1.5*np.minimum(distance,12)-.10*norm(candidates-self.pos[i])
        return candidates[int(np.argmax(scores))]

    def update_intentions(self,time,step):
        for i,role in enumerate(self.roles):
            team=self.teams[i]
            state=self.state[i]
            possession=self.possession==team or (self.possession is None and self.ball_state=='loose' and self.last_team==team)
            ball=self.local(self.ball,team)
            x=self.local(self.pos[i],team)[0]
            if state=='follow_through':
                if time-self.state_since[i]>.85: self.set_state(i,'shape',time)
                continue
            if not possession:
                if state not in ('shape','recover'): self.set_state(i,'recover',time)
                continue
            if state=='recover': self.set_state(i,'shape',time)
            if state=='drop':
                if self.owner==i: self.set_state(i,'link',time)
                elif ball[0]>58 or time-self.state_since[i]>16:
                    self.set_state(i,'return',time)
                continue
            if state=='link':
                if self.owner!=i and not (self.flight and self.flight.get('to')==i):
                    self.set_state(i,'return',time)
                continue
            if state=='return':
                if x>76 or time-self.state_since[i]>18:
                    self.set_state(i,'shape',time);self.cooldown[i]=time+8
                continue
            if state in ('overlap','decoy'):
                if time-self.state_since[i]>10 or (norm(self.pos[i]-self.run_destinations[i])<2 and (state!='decoy' or time-self.state_since[i]>=6)):
                    self.set_state(i,'shape',time);self.cooldown[i]=time+8
                continue
            if not self.allowed(i,time) or time<self.cooldown[i] or self.owner==i: continue
            # Rates per decision interval; duration does not force an outcome.
            if 'ST' in role and ball[0]<43 and self.owner is not None:
                owner_role=self.roles[self.owner]
                if owner_role=='GK' or 'CB' in owner_role or 'DM' in owner_role:
                    if self.random_events[step,i,0]<1-math.exp(-.35*self.styles[i].drop**2*.4):
                        self.pockets[i]=self.choose_pocket(i); self.set_state(i,'drop',time)
            elif role in ('LB','RB') and 30<ball[0]<82:
                if self.random_events[step,i,0]<1-math.exp(-.25*self.styles[i].overlap**2*.4):
                    side=9 if role=='LB' else 59
                    self.run_destinations[i]=self.world(np.array([88.,side]),team)
                    self.set_state(i,'overlap',time)
            elif (role in ('LW','RW','LM','RM') or 'ST' in role) and 43<ball[0]<79:
                if self.random_events[step,i,0]<1-math.exp(-.22*self.styles[i].decoy**2*.4):
                    self.run_destinations[i]=self.choose_run_destination(i)
                    self.set_state(i,'decoy',time)

    def choose_run_destination(self,i):
        team=int(self.teams[i]);local=self.local(self.pos[i],team)
        side=5 if local[1]<34 else 63
        return self.world(np.array([min(91,local[0]+15),side]),team)

    def target_positions(self,time,step):
        if time-self.last_decision>=.4-1e-6:
            self.update_intentions(time,step);self.last_decision=time
        controlling=self.possession if self.possession is not None else self.last_team
        desired=(self.teams==controlling).astype(float)
        self.phase+=(desired-self.phase)*min(1,self.dt/2)
        rho=math.exp(-self.dt/2)
        self.noise=rho*self.noise+math.sqrt(1-rho*rho)*self.random_motion[step]*.25
        target=np.zeros((22,2))
        for i,role in enumerate(self.roles):
            team=self.teams[i]; b=self.local(self.ball,team)
            local=self.base[i].copy()
            local[0]+=.48*(b[0]-52.5)
            local[0]-=8*(1-self.phase[i])
            local[1]=34+(local[1]-34)*(.78+.22*self.phase[i])+.12*(b[1]-34)
            if role=='GK': local=np.array([np.clip(5+.04*b[0],4,9),np.clip(34+.10*(b[1]-34),29,39)])
            # When a forward offers deep, midfielders/wingers keep separate lanes.
            drops=[j for j in range(22) if self.teams[j]==team and self.state[j] in ('drop','link')]
            if drops and ('CM' in role or 'DM' in role or role=='CAM'):
                local[0]+=6
                local[1]+= -4 if local[1]<34 else 4
            target[i]=self.world(local,team)+self.noise[i]
            if self.state[i]=='drop': target[i]=self.pockets[i]
            elif self.state[i]=='return':
                urgency=self.styles[i].return_urgency
                local=np.array([min(92,max(78,b[0]+16)),28 if self.local(self.pos[i],team)[1]<34 else 40])
                if time<self.return_hold_until[i]: local[0]=max(self.local(self.pos[i],team)[0],np.clip(b[0]-12,40,65))
                target[i]=self.world(local,team)
            elif self.state[i] in ('overlap','decoy'): target[i]=self.run_destinations[i]
            elif self.state[i]=='follow_through': target[i]=self.pos[i]+.15*self.vel[i]
        if self.owner is not None:
            owner=self.owner; team=self.teams[owner]; local=self.local(self.pos[owner],team);role=self.roles[owner]
            if role=='GK' or 'CB' in role or self.state[owner]=='link':
                target[owner]=self.pos[owner]+self.directions[owner]*np.array([.35,0])
            else:
                goalward=np.array([min(97,local[0]+8),34+(local[1]-34)*.65])
                # A carrier can angle around a nearby defender while cutting inside.
                rivals=np.flatnonzero((self.teams!=team)&(np.array(self.roles)!='GK'))
                nearest_carrier=min(rivals,key=lambda j:norm(self.pos[j]-self.pos[owner]))
                rival=self.local(self.pos[nearest_carrier],team)
                if 0<rival[0]-local[0]<7 and abs(rival[1]-local[1])<3:
                    side=-1 if local[1]>34 else 1
                    goalward[1]=np.clip(local[1]+side*5,3,65)
                target[owner]=self.world(goalward,team)
            opponents=[i for i in range(22) if self.teams[i]!=team and self.roles[i]!='GK']
            nearest=min(opponents,key=lambda i:norm(self.pos[i]-self.ball))
            old=self.presser[1-team]
            if old is None or norm(self.pos[old]-self.ball)>norm(self.pos[nearest]-self.ball)+4: old=nearest
            self.presser[1-team]=old
            # Mid block: do not send the striker sprinting 60 m to chase a keeper.
            if norm(self.pos[old]-self.ball)<28:
                target[old]=self.ball.copy()
            attackers=[i for i in range(22) if self.teams[i]==team and self.roles[i]!='GK' and i!=owner]
            used=set()
            weight={'track':.85,'hold':.08,'mixed':.43,'covered':.43,'exposed':.43}[self.cfg.variant]
            for defender in sorted(opponents,key=lambda i:norm(self.pos[i]-self.ball)):
                if defender==old: continue
                choices=[i for i in attackers if i not in used]
                if not choices: break
                threat=min(choices,key=lambda j:norm(self.pos[defender]-self.pos[j]))
                if norm(self.pos[defender]-self.pos[threat])<22:
                    mark=self.pos[threat]+np.array([self.directions[owner]*3,0])
                    target[defender]=(1-weight)*target[defender]+weight*mark
                    used.add(threat)
        # Cover is based on the actual full-back position, not a hidden success label.
        if self.cfg.variant=='covered' and self.pos[4,0]>58:
            target[6]=[max(30,self.pos[4,0]-26),54]
        # Receiver goes to the actual planned intercept location, rather than moving away.
        if self.flight and self.flight['type']=='pass' and self.flight.get('to') is not None:
            target[self.flight['to']]=self.flight.get('receive_target',self.flight['target'])
        if self.flight and self.flight['type']=='shot':
            keeper=11 if self.flight['team']==0 else 0
            # Intersect the actual flight line at the keeper's depth.
            fraction=np.clip((self.pos[keeper,0]-self.ball[0])/self.ball_velocity[0],0,3) if abs(self.ball_velocity[0])>1e-9 else 0
            interception=self.ball+self.ball_velocity*fraction
            interception[0]=self.pos[keeper,0]
            target[keeper]=interception
        if self.ball_state=='loose':
            for team in (0,1):
                squad=np.flatnonzero(self.teams==team)
                chaser=min(squad,key=lambda i:norm(self.pos[i]-self.ball)/self.speeds[i])
                target[chaser]=self.ball+.3*self.ball_velocity
        # Stay onside BEFORE release; receiver is free to run beyond the line after it.
        for team in (0,1):
            if controlling!=team: continue
            opp=self.pos[self.teams!=team,0]* (1 if team==0 else -1)
            line=np.sort(opp)[-2]
            bound=max(52.5*(1 if team==0 else -1),line-.8,self.ball[0]*(1 if team==0 else -1))
            for i in np.flatnonzero(self.teams==team):
                if i==self.owner or (self.flight and i==self.flight.get('to')): continue
                d=self.directions[i];target[i,0]=min(target[i,0]*d,bound)*d
        target=np.clip(target,[1,1],[104,67])
        self.target=target
        self.private.append({'t':round(time,6),'states':list(self.state),'targets':target.round(3).tolist()})
        return target

    def move_players(self,target,time):
        caps=np.array([s.cruise for s in self.styles])
        for i,state in enumerate(self.state):
            if state in ('overlap','decoy','recover'): caps[i]=self.speeds[i]*.92
            if state=='drop': caps[i]=min(6.6,self.speeds[i]*.77)
            if state=='return': caps[i]=self.speeds[i]*(.6+.4*self.styles[i].return_urgency)
            if state=='follow_through': caps[i]=1.2
            if self.owner==i: caps[i]=min(self.speeds[i]*.85,getattr(self,'carry_speed',5.7))
        if self.flight and self.flight.get('to') is not None: caps[self.flight['to']]=self.speeds[self.flight['to']]*.9
        if self.flight and self.flight['type']=='shot':
            keeper=11 if self.flight['team']==0 else 0
            caps[keeper]=self.speeds[keeper]*.9
        if self.ball_state=='loose':
            for team in (0,1):
                squad=np.flatnonzero(self.teams==team)
                i=min(squad,key=lambda j:norm(self.pos[j]-self.ball));caps[i]=self.speeds[i]*.9
        for presser in self.presser.values():
            if presser is not None and self.owner is not None and self.teams[presser]!=self.possession and norm(self.pos[presser]-self.ball)<18:
                caps[presser]=self.speeds[presser]*.85
        for defender in set(getattr(self,'marking',{})) | getattr(self,'covering',set()):
            if norm(target[defender]-self.pos[defender])>2:
                caps[defender]=max(caps[defender],self.speeds[defender]*.85)
        desired=limit((target-self.pos)*1.5,caps)
        # Predictive avoidance: bend trajectories before bodies meet, not a position teleport.
        delta=self.pos[:,None,:]-self.pos[None,:,:]
        distance=norm(delta);np.fill_diagonal(distance,np.inf)
        relative=self.vel[:,None,:]-self.vel[None,:,:]
        ttc=np.clip(-np.sum(delta*relative,axis=2)/np.maximum(np.sum(relative**2,axis=2),.01),0,2.5)
        predicted=delta+relative*ttc[...,None]
        pred_dist=norm(predicted)
        np.fill_diagonal(pred_dist,np.inf)
        away=delta/np.maximum(distance[...,None],.01)
        strength=np.maximum(0,.65-distance)*4.0
        anticipated=(pred_dist<.65)&(distance<5)&(ttc>.05)
        # Ball contestants may approach within tackling reach; avoid only bodies.
        if self.owner is not None:
            anticipated[self.owner,:]=False;anticipated[:,self.owner]=False
        # Stable handedness avoids symmetric deadlocks when paths are head-on.
        sideways=np.stack([-away[:,:,1],away[:,:,0]],axis=-1)
        avoid_normal=predicted/np.maximum(pred_dist[...,None],.01)
        avoid_normal=np.where((pred_dist<.2)[...,None],sideways,avoid_normal)
        danger=anticipated.any(axis=1)
        desired[danger]*=.85
        desired+=np.sum(away*strength[...,None]+(avoid_normal*1.2+away*.4)*(anticipated)[...,None],axis=1)
        # Brake before a close approach exceeds available stopping distance.
        # This leaves ~0.7 m for contests, rather than imposing a wide exclusion zone.
        closing=-np.sum(relative*away,axis=2)
        braking=(closing>0)&(distance < .9 + closing*.5 + closing**2/3.)
        for i in range(22):
            for j in np.flatnonzero(braking[i]):
                inward=float(desired[i]@away[i,j])
                if inward<0: desired[i]-=inward*away[i,j]
        desired=limit(desired,caps)
        acc=np.clip((desired-self.vel)/.35,-self.accelerations[:,None]/math.sqrt(2),self.accelerations[:,None]/math.sqrt(2))
        self.vel+=acc*self.h
        # Conservative boundary braking with reserved per-axis deceleration.
        axis=self.accelerations/math.sqrt(2)
        low=np.sqrt((axis[:,None]*self.h)**2+2*axis[:,None]*self.pos)-axis[:,None]*self.h
        high=np.sqrt((axis[:,None]*self.h)**2+2*axis[:,None]*(np.array([105,68])-self.pos))-axis[:,None]*self.h
        self.vel=np.clip(self.vel,-low,high)
        self.vel=limit(self.vel,self.speeds)
        self.pos=np.clip(self.pos+self.vel*self.h,[0,0],[105,68])

    def offside(self,recipient,origin):
        d=self.directions[recipient]
        line=np.sort(self.pos[self.teams!=self.teams[recipient],0]*d)[-2]
        x=self.pos[recipient,0]*d
        return x>52.5*d and x>max(line,origin[0]*d)

    def pass_candidates(self,owner,step):
        team=self.teams[owner];opponents=np.flatnonzero(self.teams!=team)
        out=[]
        for i in np.flatnonzero(self.teams==team):
            if i==owner or self.offside(i,self.ball): continue
            distance=norm(self.pos[i]-self.ball)
            if not 2<distance<48: continue
            # Limited lead; the receiver steers to this spot after release.
            target=self.pos[i].copy()
            for _ in range(3):
                travel_time=norm(target-self.ball)/20
                target=np.clip(self.pos[i]+self.vel[i]*min(2.5,travel_time),[2,2],[103,66])
            vector=target-self.ball; length=norm(vector)
            if length>48: continue
            u=np.clip((self.pos[opponents]-self.ball)@vector/max(length**2,.01),0,1)
            lane=np.min(norm(self.pos[opponents]-(self.ball+u[:,None]*vector)))
            clear=np.min(norm(self.pos[opponents]-target))
            forward=self.directions[owner]*(target[0]-self.ball[0])
            # Most decisions favour nearby support; occasional switches remain possible.
            long_option=self.random_events[step,owner,4]>.84
            preferred=32 if long_option else 11
            value=.055*forward+.30*min(clear,10)+.55*min(lane,7)-.24*abs(distance-preferred)
            value+=self.random_events[step,i,2]*.8
            if self.roles[owner]=='GK' and 'CB' in self.roles[i]: value+=6
            if self.state[i]=='drop' and norm(self.pos[i]-self.pockets[i])<7: value+=7
            if self.state[owner]=='link':
                preferred=self.roles[i] in ('LW','RW','LM','RM') if self.cfg.build_up_route=='wing_release' else ('DM' in self.roles[i] or 'CM' in self.roles[i] or self.roles[i]=='CAM')
                if preferred and forward>3: value+=6
            if self.state[i]=='return' and forward>3: value+=5
            if self.roles[i]=='GK' and self.roles[owner]!='GK': value-=4
            if lane<1.15 or clear<1.4: value-=12
            out.append((value,i,target,lane))
        return sorted(out,key=lambda x:x[0],reverse=True)

    def release(self,kind,target,time,receiver=None,speed=18.):
        owner=self.owner
        if owner is None: raise RuntimeError('Cannot kick without control')
        self.keeper_holding=False
        origin=self.ball.copy();direction=(target-origin)/max(norm(target-origin),1e-9)
        self.flight={'type':kind,'from':owner,'to':receiver,'team':int(self.teams[owner]),'target':target.copy(),
                     'speed':speed,'remaining':float(norm(target-origin))}
        if receiver is not None:
            receiver_motion=self.vel[receiver]
            continuation=receiver_motion/max(norm(receiver_motion),1e-9)*min(3.,norm(receiver_motion))
            self.flight['receive_target']=np.clip(target+continuation,[1,1],[104,67])
        self.ball_velocity=direction*speed
        self.owner=None
        self.ball_state='shot' if kind=='shot' else 'pass'
        self.last_kicker=owner;self.release_time=time
        self.hold_time=0
        if kind=='shot': self.set_state(owner,'follow_through',time)
        elif self.state[owner]=='link': self.set_state(owner,'return',time)
        self.emit(kind,player=self.ids[owner],target=self.ids[receiver] if receiver is not None else None,
                  distance_m=round(float(norm(target-origin)),3),
                  technique=('long_range' if norm(target-origin)>24 else 'angled' if abs(self.local(origin,int(self.teams[owner]))[1]-34)>12 else 'placed') if kind=='shot' else ('short' if norm(target-origin)<15 else 'medium' if norm(target-origin)<30 else 'long'))

    def make_ball_decision(self,time,step):
        if self.owner is None or self.finished: return
        owner=self.owner;role=self.roles[owner];team=self.teams[owner]
        local=self.local(self.ball,team)
        if self.cfg.turnover_at is not None and time>=self.cfg.turnover_at and not self.forced_turnover_done:
            self.forced_turnover_done=True
            opponents=np.flatnonzero(self.teams!=team)
            nearest=min(opponents,key=lambda i:norm(self.pos[i]-self.ball))
            self.release('pass',self.pos[nearest].copy(),time,speed=14)
            return
        goal=np.array([105.,34.]);distance=norm(local-goal)
        if local[0]>72 and distance<34 and abs(local[1]-34)<24 and self.hold_time>.65:
            if self.random_events[step,owner,1]<self.dt*(.25 if distance>24 else 1.5) or self.hold_time>4.5:
                # Aim away from keeper with skill-dependent lateral error. No guaranteed goal.
                keeper=11 if team==0 else 0
                keeper_y=self.local(self.pos[keeper],team)[1]
                aim=31.4 if keeper_y>=34 else 36.6
                aim+=(self.random_events[step,owner,4]-.5)*(1-self.styles[owner].finishing)*(20+distance*1.1)
                self.release('shot',self.world(np.array([105.,aim]),team),time,
                             speed=24+4*self.random_events[step,owner,3])
                return
        pressure=min(norm(self.pos[i]-self.ball) for i in np.flatnonzero(self.teams!=team))
        if role in ('LW','RW','LM','RM') and local[0]>55 and pressure>1.05 and self.hold_time<6:
            return
        dwell=1.3 if role=='GK' else (.7 if self.state[owner]=='link' else 1.1)
        if self.hold_time<dwell: return
        candidates=self.pass_candidates(owner,step)
        if not candidates: return
        value,recipient,target,lane=candidates[0]
        pressure=min(norm(self.pos[i]-self.ball) for i in np.flatnonzero(self.teams!=team))
        # Look for a supporting forward arriving in the pocket; a defender can
        # retain the ball briefly, but will recycle or release under pressure.
        dropping=[i for i in range(22) if self.teams[i]==team and self.state[i]=='drop']
        if ('CB' in role or role=='GK') and dropping and pressure>5 and self.hold_time<4.5:
            if all(norm(self.pos[i]-self.pockets[i])>7 for i in dropping): return
        if self.hold_time<2.8 and self.random_events[step,owner,1]>self.dt*1.5: return
        if value<0 and self.hold_time<4: return
        error=(1-self.skill[owner])*self.random_motion[step,owner]*2
        target=np.clip(target+error,[1,1],[104,67])
        self.release('pass',target,time,recipient,speed=17+min(4,norm(target-self.ball)/15))

    def take_possession(self,player,event,time=0):
        if norm(self.pos[player]-self.ball)>1.05: raise RuntimeError('Cannot award distant possession')
        previous=self.last_team
        local=self.local(self.pos[player],int(self.teams[player]))
        self.keeper_holding=self.roles[player]=='GK' and event in ('save','recovery') and local[0]<=16.5 and 13.84<=local[1]<=54.16
        self.owner=int(player);self.possession=int(self.teams[player]);self.last_team=self.possession
        self.flight=None;self.ball_state='controlled';self.ball_velocity=self.vel[player].copy()
        self.hold_time=0;self.last_receiver=int(player)
        if self.state[player]=='drop': self.set_state(player,'link',time)
        self.emit(event,player=self.ids[player])
        if previous!=self.possession:
            self.emit('possession_change',team=self.cfg.team_names[self.possession])
            self.finish('opponent_control' if event!='save' else 'saved_shot')

    def finish(self, reason):
        if not self.finished:
            self.finished=True;self.termination=reason
            self.emit('possession_end',team='A',reason=reason)

    def stop_if_out_of_play(self):
        if np.any(self.ball<0) or self.ball[0]>105 or self.ball[1]>68:
            self.ball=np.clip(self.ball,[0,0],[105,68])
            self.owner=None;self.flight=None;self.possession=None
            self.ball_state='dead';self.ball_velocity[:]=0
            self.emit('out_of_play');self.finish('out_of_play')
            return True
        return False

    def move_ball(self,time,step):
        if self.finished: return
        gathering=getattr(self,'keeper_gather',None)
        if gathering is not None:
            # Hands bring a secured ball towards the body; don't award distant ownership.
            self.ball+=limit(self.pos[gathering]-self.ball,6*self.h)
            self.ball_velocity[:]=0
            if norm(self.ball-self.pos[gathering])<=1.0:
                self.keeper_gather=None
                self.take_possession(gathering,'save',time)
            return
        if self.owner is not None:
            self.hold_time+=self.h
            # Controlled ball is kept within touching distance, never metres away.
            movement=self.vel[self.owner]
            foot=.35*movement/max(float(norm(movement)),1e-9)
            offset=self.pos[self.owner]+foot-self.ball
            self.ball+=limit(offset,12*self.h)
            self.ball_velocity=self.vel[self.owner].copy()
            if self.stop_if_out_of_play():return
            if getattr(self,'keeper_holding',False):return
            opponents=np.flatnonzero(self.teams!=self.possession)
            nearest=min(opponents,key=lambda i:norm(self.pos[i]-self.ball))
            if norm(self.pos[nearest]-self.ball)<1.0 and self.hold_time>.3 and self.last_contest_step!=step:
                self.last_contest_step=step
                contenders=[int(i) for i in opponents if norm(self.pos[i]-self.ball)<1.0]
                self.emit('contest',players=[self.ids[self.owner]]+[self.ids[i] for i in contenders])
                # A hazard per second, evaluated once per output tick (not per substep).
                if self.random_events[step,nearest,3]<1-math.exp(-1.3*len(contenders)*self.dt):
                    self.take_possession(nearest,'tackle',time)
            return
        if self.ball_state=='dead': return
        previous=self.ball.copy()
        if self.flight:
            flight=self.flight
            travel=min(flight['remaining'],norm(self.ball_velocity)*self.h)
            self.ball+=self.ball_velocity/max(norm(self.ball_velocity),1e-9)*travel
            flight['remaining']-=travel
        else:
            self.ball+=self.ball_velocity*self.h
            self.ball_velocity*=max(0,1-.85*self.h)
        # Swept segment contacts prevent fast balls tunnelling through players.
        segment=self.ball-previous
        u=np.clip((self.pos-previous)@segment/max(float(segment@segment),1e-12),0,1)
        contacts=norm(self.pos-(previous+u[:,None]*segment))
        candidates=[]
        for i in range(22):
            if i==self.last_kicker and time-self.release_time<.4: continue
            if self.flight and self.flight['type']=='pass' and self.teams[i]==self.flight['team'] and i!=self.flight.get('to'): continue
            if self.flight and self.flight.get('loft_peak'):
                uflight=1-self.flight['remaining']/max(self.flight['total_distance'],.01)
                height=4*self.flight['loft_peak']*uflight*(1-uflight)
                if height>2.2:continue
            radius=1.0  # Ordinary foot/control reach.
            if self.roles[i]=='GK' and self.flight and self.flight['type']=='shot':
                keeper_local=self.local(self.pos[i],int(self.teams[i]))
                if keeper_local[0]<=16.5 and 13.84<=keeper_local[1]<=54.16:radius=1.8
            if contacts[i]<radius: candidates.append(i)
        if candidates:
            player=min(candidates,key=lambda i:u[i])
            self.ball=previous+u[player]*segment
            if self.stop_if_out_of_play():return
            is_shot=self.flight is not None and self.flight['type']=='shot'
            if is_shot and self.roles[player]=='GK' and contacts[player]>1.0:
                # A stretched hand can parry, but cannot claim controlled possession.
                self.flight=None;self.ball_state='loose';self.possession=None
                # Cushion most saves for a second collection; hard spills remain possible.
                hard_spill=self.random_events[step,player,4]>.2+.65*self.styles[player].passing
                self.keeper_gather=None if hard_spill else player
                spill=3.5 if hard_spill else .8
                self.ball_velocity=np.array([self.directions[player]*spill,np.sign(self.ball[1]-self.pos[player,1])*spill*.5])
                self.last_kicker=player;self.release_time=time
                self.emit('parry',player=self.ids[player])
            elif is_shot and self.roles[player]!='GK':
                self.flight=None;self.ball_state='loose';self.possession=None
                self.ball_velocity*=.2;self.last_kicker=player;self.release_time=time
                self.emit('block',player=self.ids[player])
                self.finish('blocked_shot')
            else:
                event='save' if is_shot else ('reception' if self.flight and self.teams[player]==self.flight['team'] else 'recovery')
                self.take_possession(player,event,time)
            return
        if self.flight and self.flight['remaining']<=1e-8:
            flight=self.flight
            if flight['type']=='shot':
                goal=abs(self.ball[1]-34)<=3.66
                if goal:
                    self.score[flight['team']]+=1
                    self.emit('goal',team=self.cfg.team_names[flight['team']],player=self.ids[flight['from']])
                else: self.emit('shot_missed',player=self.ids[flight['from']])
                self.finish('goal' if goal else 'missed_shot');self.ball_state='dead';self.ball_velocity[:]=0;self.possession=None
                self.flight=None
                self.ball=np.clip(self.ball,[0,0],[105,68])
                return
            else:
                self.ball_state='loose';self.possession=None
                self.ball_velocity*=.25
            self.flight=None
        self.stop_if_out_of_play()

    def frame(self,time):
        return {'kind':'frame','t':round(time,6),
                'players':[{'id':pid,'x':round(float(p[0]),5),'y':round(float(p[1]),5)} for pid,p in zip(self.ids,self.pos)],
                'ball':{'x':round(float(self.ball[0]),5),'y':round(float(self.ball[1]),5),
                        'owner':self.ids[self.owner] if self.owner is not None else None,'state':self.ball_state},
                'possession':self.cfg.team_names[self.possession] if self.possession is not None else None,
                'score':list(self.score),'events':list(self.events)}

    def frames(self):
        yield self.frame(0)
        for step in range(1,round(self.cfg.duration*self.cfg.hz)+1):
            time=(step-1)*self.dt;self.events=[];self.last_step=step
            target=self.target_positions(time,step)
            self.make_ball_decision(time,step)
            # Releasing can change follow-through state; movement caps react immediately.
            for substep in range(self.substeps):
                self.move_players(target,time+(substep+1)*self.h)
                self.move_ball(time+(substep+1)*self.h,step)
            if step==round(self.cfg.duration*self.cfg.hz) and not self.finished:
                self.termination='time_limit'
                self.emit('clip_truncated',reason='time_limit')
            yield self.frame(step*self.dt)
            if self.finished: break

    def private_record(self):
        return {'config':asdict(self.cfg),'focus':self.ids[self.focus],
                'styles':[asdict(s) for s in self.styles],'movement_speed_limits':self.speeds.tolist(),
                'acceleration_limits':self.accelerations.tolist(),'intentions':self.private}
