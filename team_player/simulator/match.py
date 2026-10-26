"""Continuous synthetic play. Public output contains positions and metadata only."""
from dataclasses import asdict
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import math
import os
from pathlib import Path
import numpy as np
from team_player.simulator.engine import Simulator, SimulationConfig
from team_player.config.league import LEAGUE_SEED, MATCH_DURATION_S, SAMPLE_HZ, TEMPOS
from team_player.config.styles import STRIKERS, WINGERS, FULLBACKS
from team_player.utils.geometry import norm, limit


class MatchSimulator(Simulator):
    def __init__(self, config):
        super().__init__(config)
        self.restart = None
        self.clock = 0.
        self.patience=np.full(2,3.0 if config.match_tempo=='patient' else .65) if config.match_tempo!='mixed' else self.rng.uniform(2.2,3.8,2)

        self.marking={}
        self.carry_speed=3.2 if config.match_tempo!='direct' else 5.7
        self.covering=set()
        self.supporting={}
        self.screening={}
        self.mark_assignments={}
        self.mark_cooldown={}
        self.last_mark_choice=-10.
        self.transition_team=None
        self.transition_until=-1.
        self.initial_shape()

    def initial_shape(self):
        if self.cfg.acquisition=='goalkeeper':
            self.ball=np.array([7.,34.])
            self.pos=self.phase_shape(0,self.ball)
            self.phase[:11]=1.;self.phase[11:]=0.
        else:
            midfield=[i for i in range(11) if 'DM' in self.roles[i] or 'CM' in self.roles[i] or self.roles[i]=='CAM']
            self.owner=int(self.rng.choice(midfield))
            self.pos[self.owner,0]=48.
        self.ball=self.pos[self.owner].copy()
        self.pockets=self.pos.copy();self.run_destinations=self.pos.copy();self.target=self.pos.copy()

    def take_possession(self,player,event,time=0):
        previous=self.last_team
        super().take_possession(player,event,time)
        if previous!=self.last_team and event not in ('restart','save'):
            self.transition_team=self.last_team
            self.transition_until=time+9.

    def counter_window(self,team):
        return self.transition_team==team and self.clock<self.transition_until

    def choose_run_destination(self,i):
        # Threaten a channel or move across a marker; never prescribe their response.
        # Runs stay onside before the pass: destinations are capped at the defensive line,
        # so a run along/across the line (diagonal into the CB-FB channel) is a real sprint
        # instead of being clamped to a standstill.
        team=int(self.teams[i]);local=self.local(self.pos[i],team)
        others=[j for j in range(22) if self.teams[j]==team and j!=i]
        opponents=self.pos[self.teams!=team]
        opp_local=np.array([self.local(p,team) for p in opponents])
        line=float(np.sort(opp_local[:,0])[-2])-.8
        bound=max(52.5,line,float(self.local(self.ball,team)[0]))
        if local[0]>=bound:bound=94.  # already beyond the line: the usual forward runs apply
        inward=1 if local[1]<34 else -1
        candidates=[];values=[]
        for dx,dy in ((9,-10),(9,10),(15,-5),(15,5),(5,-12),(5,12),(4,14*inward),(4,20*inward),(2,-10*inward)):
            dest=np.array([min(94,local[0]+dx,max(bound,local[0]+1)),np.clip(local[1]+dy,8,60)])
            point=self.world(dest,team)
            if norm(point-self.pos[i])<6:continue
            spacing=min(norm(self.pos[j]-point) for j in others)
            clearance=float(min(norm(opponents-point)))
            progress=dest[0]-local[0]
            # Opponents near the run's path are the ones it can pull out of position.
            mid=(self.pos[i]+point)/2
            drag=min(3,int(np.sum(norm(opponents-mid)<9)))
            # Space and progress, with a cost for crowding a teammate's channel.
            value=.35*min(clearance,10)+.12*progress-.8*max(0,7-spacing)+.45*drag*self.styles[i].decoy
            candidates.append(point);values.append(value)
        if not candidates:return self.pos[i].copy()
        weights=np.exp((np.array(values)-max(values))/1.8);weights/=weights.sum()
        return candidates[int(self.rng.choice(len(candidates),p=weights))]

    def choose_pocket(self,i):
        # Offer near the midfield support line, never chase a keeper into his box.
        team=int(self.teams[i]);ball=self.local(self.ball,team)
        mids=[self.local(self.pos[j],team)[0] for j in range(22)
              if self.teams[j]==team and ('DM' in self.roles[j] or 'CM' in self.roles[j])]
        x=float(np.clip(max(ball[0]+10,np.median(mids)+6),36,55))
        candidates=[self.world(np.array([x+dx,y]),team) for dx in (-2,3) for y in (19,27,41,49)]
        opponents=np.flatnonzero(self.teams!=team)
        teammates=[j for j in range(22) if self.teams[j]==team and j!=i]
        def score(point):
            spacing=min(float(norm(point-self.pos[j])) for j in teammates)
            clearance=float(min(norm(self.pos[opponents]-point)))
            vector=point-self.ball;u=np.clip((self.pos[opponents]-self.ball)@vector/max(float(vector@vector),.01),0,1)
            incoming=float(min(norm(self.pos[opponents]-(self.ball+u[:,None]*vector))))
            outlets=0
            for j in teammates:
                v=self.pos[j]-point;length=float(norm(v))
                if self.directions[i]*v[0]<5 or not 5<length<50:continue
                u=np.clip((self.pos[opponents]-point)@v/(length*length),0,1)
                if min(norm(self.pos[opponents]-(point+u[:,None]*v)))>2:outlets+=1
            return .7*min(spacing,10)+.5*min(clearance,8)+.4*min(incoming,6)+1.2*outlets-.04*norm(point-self.pos[i])
        return max(candidates,key=score)

    def loft_option(self,owner,target):
        # Long balls may clear midfield feet but remain contestable at either end.
        length=float(norm(target-self.ball))
        if length<28 or self.skill[owner]<.90:return False
        opponents=self.pos[self.teams!=self.teams[owner]]
        return float(min(norm(opponents-target)))>4.5

    def pass_candidates(self,owner,step):
        # General utility for every carrier: progress, room and feasible ball travel.
        # No state, archetype or scripted route tells a carrier whom to pass to.
        team=int(self.teams[owner]);opponents=np.flatnonzero(self.teams!=team);out=[]
        for i in np.flatnonzero(self.teams==team):
            if i==owner or self.offside(i,self.ball):continue
            distance=float(norm(self.pos[i]-self.ball))
            if not 2<distance<60:continue
            target=self.pos[i].copy()
            for _ in range(3):
                target=np.clip(self.pos[i]+self.vel[i]*min(2.5,norm(target-self.ball)/20),[2,2],[103,66])
            vector=target-self.ball;length=float(norm(vector))
            if length>60:continue
            u=np.clip((self.pos[opponents]-self.ball)@vector/max(length**2,.01),0,1)
            lane=float(min(norm(self.pos[opponents]-(self.ball+u[:,None]*vector))))
            clear=float(min(norm(self.pos[opponents]-target)))
            forward=self.directions[owner]*vector[0]
            preferred=22 if self.counter_window(team) else 16
            value=.13*forward+.30*min(clear,10)+.55*min(lane,7)-.065*abs(distance-preferred)
            value-=max(0,length-30)*(1-self.skill[owner])*.6
            # Reward a receiver's usable attacking position, not a named route.
            value+=4*self.shooting_option(i,target)[0]
            if forward>8 and clear>4 and lane>2:
                value+=.09*min(forward,35)
            if self.counter_window(team):value+=.10*max(0,forward)
            value+=self.random_events[step,i,2]*.8
            if self.roles[owner]=='GK' and 'CB' in self.roles[i]:value+=4
            if self.roles[i]=='GK':value-=3
            if (lane<1.15 and not self.loft_option(owner,target)) or clear<1.4:value-=12
            out.append((value,int(i),target,lane))
        return sorted(out,key=lambda row:row[0],reverse=True)

    def shooting_option(self,player,origin=None):
        team=int(self.teams[player]);origin=self.pos[player] if origin is None else origin
        local=self.local(origin,team);distance=float(norm(local-[105.,34.]))
        opponents=[i for i in range(22) if self.teams[i]!=team and self.roles[i]!='GK']
        opened=0
        for y in np.linspace(31,37,5):
            goal=self.world(np.array([105.,y]),team);vector=goal-origin
            u=(self.pos[opponents]-origin)@vector/max(float(vector@vector),1e-9)
            separation=norm(self.pos[opponents]-(origin+u[:,None]*vector))
            if not np.any((u>.02)&(u<.98)&(separation<1.3)):opened+=1
        lane=opened/5
        front=[i for i in opponents if self.directions[player]*(self.pos[i,0]-origin[0])>-.5]
        pressure=min((float(norm(self.pos[i]-origin)) for i in front),default=30.)
        all_pressure=min((float(norm(self.pos[i]-origin)) for i in opponents),default=30.)
        quality=math.exp(-distance/25)*max(0,1-abs(local[1]-34)/30)*lane*min(1,pressure/3)
        clear=distance<28 and abs(local[1]-34)<12 and lane>=.6 and pressure>3 and all_pressure>1.2
        return quality,clear

    def allowed(self, i, time):
        return self.cfg.intervention and self.cfg.start <= time < self.cfg.end and self.restart is None

    def finish(self, reason):
        # A shot/block or turnover ends an analysis episode, not the match.
        self.termination=reason
        if reason in ('goal','missed_shot','out_of_play'):
            team=1-self.last_team
            if reason in ('goal','missed_shot'):
                # User-requested demo convention: conceding/defending keeper restarts.
                player=0 if team==0 else 11
                target=self.world(np.array([7.,34.]),team);delay=4.
            else:
                squad=np.flatnonzero((self.teams==team)&(np.array(self.roles)!='GK'))
                player=int(min(squad,key=lambda i:norm(self.pos[i]-self.ball)))
                target=np.clip(self.ball,[.5,.5],[104.5,67.5]);delay=3.
            self.restart={'reason':reason,'player':player,'team':team,'target':target,'at':self.clock+delay}
            self.state=['shape']*22
            self.state_since[:]=self.clock
        # Opponent control, saves and blocked-shot rebounds continue immediately.

    def phase_shape(self,attacking_team,ball):
        """Continuous team shape; also used to initialise a goalkeeper build-up."""
        progress=float(self.local(ball,attacking_team)[0])
        shape=np.zeros_like(self.pos)
        for i,role in enumerate(self.roles):
            team=int(self.teams[i]);attacking=team==attacking_team
            side=-1 if self.base[i,1]<34 else 1
            central=self.base[i,1]==34
            if attacking:
                if role=='GK':x,y=7.,34.
                elif 'CB' in role:x,y=min(46,18+.28*max(0,progress-10)),34+side*13
                elif role in ('LB','RB'):x,y=min(76,27+.45*max(0,progress-10)),34+side*26
                elif 'ST' in role:x,y=min(93,59+.43*max(0,progress-10)),34+(0 if role=='ST' else side*7)
                elif role in ('LW','RW','LM','RM'):x,y=min(91,48+.48*max(0,progress-10)),34+side*25
                elif role=='CAM':x,y=min(84,43+.46*max(0,progress-10)),34
                else:x,y=min(72,31+.43*max(0,progress-10)),34+(0 if central else side*10)
            else:
                line=float(np.clip(105-progress-20,12,42))
                ball_y=self.local(ball,team)[1]
                if role=='GK':x,y=6.,34+.12*(ball_y-34)
                elif 'CB' in role:x,y=line,34+side*7+.15*(ball_y-34)
                elif role in ('LB','RB'):x,y=line+2,34+side*18+.2*(ball_y-34)
                elif 'ST' in role:x,y=min(72,line+27),34+.3*(ball_y-34)
                elif role in ('LW','RW','LM','RM'):x,y=line+18,34+side*15+.25*(ball_y-34)
                else:x,y=line+12,34+(0 if central else side*8)+.2*(ball_y-34)
            shape[i]=self.world(np.array([x,y]),team)
        return np.clip(shape,[1,1],[104,67])

    def rotate_into_vacated_space(self,team,target):
        """When the striker drops to link play, the attacking midfielder (or, without one, the
        nearer winger) rotates into the striker's position, as a coached false-9 rotation does."""
        droppers=[j for j in range(22) if self.teams[j]==team and 'ST' in self.roles[j] and self.state[j] in ('drop','link')]
        if not droppers:return
        striker=droppers[0];home=self.local(target[striker],team)
        mates=[j for j in range(22) if self.teams[j]==team and self.state[j] not in ('drop','link','overlap','decoy') and j!=self.owner]
        cam=[j for j in mates if self.roles[j]=='CAM']
        wide=[j for j in mates if self.roles[j] in ('LW','RW','LM','RM')]
        if cam:mover=cam[0]
        elif wide:mover=min(wide,key=lambda j:abs(self.local(self.pos[j],team)[1]-34))
        else:return
        ball=self.local(self.ball,team)
        target[mover]=self.world(np.array([min(93.,max(home[0],ball[0]+6)),34.]),team)

    def target_positions(self,time,step):
        original=super().target_positions(time,step)
        team=self.possession if self.possession is not None else self.last_team
        target=self.phase_shape(team,self.ball)
        self.rotate_into_vacated_space(team,target)
        for i in range(22):
            # Keep personality-driven excursions and ball reception targets.
            if self.teams[i]==team and self.state[i] in ('drop','link','return','overlap','decoy','follow_through'):
                target[i]=original[i]
        if self.flight and self.flight['type']=='pass':target[self.flight['to']]=original[self.flight['to']]
        if self.flight and self.flight['type']=='shot':
            keeper=11 if self.flight['team']==0 else 0;target[keeper]=original[keeper]
        if self.ball_state=='loose':target=original
        if self.restart:
            target=self.phase_shape(self.restart['team'],self.restart['target'])
            target[self.restart['player']]=self.restart['target']
            self.marking={};self.covering=set();self.supporting={};self.screening={}
        else:
            if self.owner is not None:
                owner=self.owner;local=self.local(self.pos[owner],team)
                if self.roles[owner]=='GK' or 'CB' in self.roles[owner] or self.state[owner]=='link':target[owner]=original[owner]
                else:
                    advance=6. if self.counter_window(team) else (1.2 if self.cfg.match_tempo!='direct' else 6.)
                    target[owner]=self.world(np.array([min(96,local[0]+advance),34+.95*(local[1]-34)]),team)
                    if self.roles[owner] in ('LW','RW','LM','RM') and local[0]>62:
                        # Choose a dribbling corridor from current defensive spacing.
                        choices=[self.world(np.array([min(96,local[0]+3),np.clip(local[1]+dy,4,64)]),team) for dy in (-3,0,3)]
                        defenders=self.pos[self.teams!=team]
                        def carry_value(point):
                            space=float(min(norm(defenders-point)))
                            return .5*min(space,5)-.32*abs(self.local(point,team)[1]-34)
                        target[owner]=max(choices,key=carry_value)
            if self.ball_state!='dead':self.defensive_targets(target)
        # New phase targets must obey the same pre-release offside constraint.
        opp=self.pos[self.teams!=team,0]*(1 if team==0 else -1)
        direction=1 if team==0 else -1
        bound=max(52.5*direction,np.sort(opp)[-2]-.8,self.ball[0]*direction)
        for i in np.flatnonzero(self.teams==team):
            if i!=self.owner and not (self.flight and self.flight.get('to')==i):target[i,0]=min(target[i,0]*direction,bound)*direction
        # Open-field carriers can accelerate; contested dribbles remain slower.
        self.carry_speed=3.2
        if self.owner is not None and self.roles[self.owner]!='GK':
            nearby=min(norm(self.pos[j]-self.ball) for j in range(22) if self.teams[j]!=team)
            self.carry_speed=6.2 if nearby>4 and self.counter_window(team) else (5.4 if nearby>4 else 3.2)
        self.target=np.clip(target,[1,1],[104,67])
        return self.target

    def defensive_targets(self,target):
        self.marking={};self.covering=set();self.supporting={};self.screening={}
        if self.owner is None and not (self.flight and self.flight['type']=='pass'):return
        team=int(self.teams[self.owner]) if self.owner is not None else int(self.flight['team'])
        carrier=self.owner if self.owner is not None else self.flight.get('to')
        if getattr(self,'keeper_holding',False):return
        point=self.ball.copy()
        progress=self.local(point,team)
        defenders=[i for i in range(22) if self.teams[i]!=team and self.roles[i]!='GK']
        backs=[i for i in defenders if 'CB' in self.roles[i] or self.roles[i] in ('LB','RB')]
        presser=min(defenders,key=lambda i:norm(self.pos[i]-point))
        self.presser[1-team]=presser
        used={presser}
        if norm(self.pos[presser]-point)<22:target[presser]=point
        cbs=[i for i in backs if 'CB' in self.roles[i] and i!=presser]
        if progress[0]>50 and cbs:
            cover=min(cbs,key=lambda i:norm(self.pos[i]-point))
            target[cover]=self.world(np.array([min(101,progress[0]+6),34+.3*(progress[1]-34)]),team)
            self.covering.add(cover);used.add(cover)
        # A second defender approaches from goal-side, keeping the press staggered.
        if progress[0]>53 and carrier is not None:
            support=[i for i in defenders if i not in used and 'CB' not in self.roles[i]]
            if support:
                helper=min(support,key=lambda i:norm(self.pos[i]-point))
                if norm(self.pos[helper]-point)<22:
                    side=1 if self.local(self.pos[helper],team)[1]>=progress[1] else -1
                    target[helper]=self.world(progress+np.array([2.4,side*1.6]),team)
                    self.supporting[helper]=carrier;self.covering.add(helper);used.add(helper)
        attackers=[i for i in range(22) if self.teams[i]==team and self.roles[i]!='GK' and i!=carrier]
        # A defender can commit to a visible run. Decisions are spaced in time,
        # assignments persist, and urgent ball defence can override them.
        now=self.last_step*self.dt
        for defender,(runner,until) in list(self.mark_assignments.items()):
            if (defender not in defenders or runner not in attackers or now>=until
                    or norm(self.pos[defender]-self.pos[runner])>22
                    or self.local(self.pos[runner],team)[0]<progress[0]-18):
                self.mark_assignments.pop(defender);self.mark_cooldown[defender]=now+3
        if now-self.last_mark_choice>=.6:
            self.last_mark_choice=now
            assigned={r:sum(x==r for x,_ in self.mark_assignments.values()) for r,_ in self.mark_assignments.values()}
            for defender in defenders:
                if defender in used or defender in self.mark_assignments or now<self.mark_cooldown.get(defender,0):continue
                runners=[i for i in attackers if assigned.get(i,0)<2 and norm(self.vel[i])>2.2
                         and self.local(self.pos[i],team)[0]>38 and norm(self.pos[i]-self.pos[defender])<14]
                if not runners:continue
                runner=min(runners,key=lambda i:norm(self.pos[i]-self.pos[defender]))
                # Neither tracking nor handover is certain; faster, nearer threats matter more,
                # and so does a run attacking the space at or behind the defensive line.
                chance=.22+.25*min(1,norm(self.vel[runner])/7)
                runner_local=self.local(self.pos[runner],team)
                if runner_local[0]>progress[0] and (self.directions[runner]*self.vel[runner][0]>1.5 or
                        abs(runner_local[1]-34)<abs(self.local(self.pos[runner]-self.vel[runner],team)[1]-34)):
                    chance+=.25
                # A second marker is plausible for a nearby forward threat, but
                # less likely than the first. Never require a particular outcome.
                if assigned.get(runner,0):chance*=.65
                self.mark_cooldown[defender]=now+2
                if self.rng.random()<chance:
                    self.mark_assignments[defender]=(runner,now+float(self.rng.uniform(5,10)))
                    assigned[runner]=assigned.get(runner,0)+1
        committed=set()
        for defender,(runner,until) in self.mark_assignments.items():
            if defender in used:continue
            mark=self.pos[runner]+.35*self.vel[runner]+[self.directions[runner]*1.8,0]
            target[defender]=mark;self.marking[defender]=runner;used.add(defender);committed.add(defender)
        # Screen dangerous forward lanes before chasing the receiver's current position.
        threats=sorted(attackers,key=lambda i:(self.local(self.pos[i],team)[0]-progress[0])-abs(self.local(self.pos[i],team)[1]-34)*.2,reverse=True)
        screened=set()
        for defender in defenders:
            if defender in used or defender in backs:continue
            choices=[i for i in threats if i not in screened and 4<self.directions[i]*(self.pos[i,0]-point[0])<35]
            if not choices:continue
            receiver=min(choices,key=lambda i:norm(self.pos[defender]-(point+.65*(self.pos[i]-point))))
            screen=point+.65*(self.pos[receiver]-point)
            if norm(self.pos[defender]-screen)<20:
                target[defender]=screen;self.screening[defender]=receiver
                used.add(defender);screened.add(receiver);self.covering.add(defender)
        remaining=set(attackers)-{self.marking[d] for d in committed}
        for defender in sorted(defenders,key=lambda i:norm(self.pos[i]-point)):
            if defender in used or not remaining:continue
            attacker=min(remaining,key=lambda i:norm(self.pos[defender]-self.pos[i]))
            remaining.remove(attacker);self.marking[defender]=attacker
            mark=self.pos[attacker]+[self.directions[attacker]*2.,0]
            # Back-line cover is not abandoned for a distant or deep-decoy player.
            weight=.5 if defender in backs else .6
            if norm(mark-target[defender])<16:target[defender]=(1-weight)*target[defender]+weight*mark
        if progress[0]>58:
            for defender in backs:
                if defender==presser or defender in committed:continue
                local=self.local(target[defender],team);local[0]=max(local[0],min(99,progress[0]+2))
                if abs(progress[1]-34)<15:local[1]=np.clip(local[1],19,49)
                target[defender]=self.world(local,team);self.covering.add(defender)
        # React to observable pass velocity: contest reachable points along its path.
        if self.flight and self.flight['type']=='pass':
            speed=float(norm(self.ball_velocity));horizon=min(.9,self.flight['remaining']/max(speed,1))
            opportunities=[]
            for defender in defenders:
                for dt in (.2,.4,.6,.9):
                    if dt>horizon:continue
                    intercept=point+self.ball_velocity*dt
                    reach=1.+min(self.speeds[defender]*dt,norm(self.vel[defender])*dt+.5*self.accelerations[defender]*dt*dt)
                    gap=float(norm(self.pos[defender]-intercept))
                    if gap<reach:opportunities.append((gap,defender,intercept))
            selected=set()
            for _,defender,intercept in sorted(opportunities,key=lambda row:row[0]):
                if defender in selected:continue
                target[defender]=intercept;self.covering.add(defender);selected.add(defender)
                if len(selected)==2:break
        np.clip(target,[1,1],[104,67],out=target)

    def pass_margin(self,target,team,lofted=False):
        opponents=np.flatnonzero(self.teams!=team)
        vector=target-self.ball;length=float(norm(vector))
        u=np.clip((self.pos[opponents]-self.ball)@vector/max(length*length,.01),0,1)
        separation=norm(self.pos[opponents]-(self.ball+u[:,None]*vector))
        time=u*length/20
        reach=.7+np.maximum(0,time-.25)*np.minimum(self.speeds[opponents],3.+time)
        if lofted:
            # Height along a ballistic arc. A defender still attacks the landing
            # zone; the ball cannot be stopped by a foot under the high middle.
            height=4*5.*u*(1-u)
            separation=separation.copy();separation[height>2.2]=100.
        return float(np.min(separation-reach))

    def make_ball_decision(self,time,step):
        if self.restart or self.owner is None:return
        owner=self.owner;team=int(self.teams[owner]);role=self.roles[owner]
        local=self.local(self.ball,team);distance=float(norm(local-np.array([105.,34.])))
        opponents=np.flatnonzero(self.teams!=team)
        pressure=float(min(norm(self.pos[j]-self.ball) for j in opponents))
        patience=.65 if self.counter_window(team) else float(self.patience[team])
        # No timer forces a shot. Distance, angle, pressure and patience all matter.
        quality,clear=self.shooting_option(owner,self.ball)
        candidates=self.pass_candidates(owner,step)
        better=[]
        if clear:
            for candidate in candidates:
                _,recipient,target,lane=candidate
                if lane>2 and self.shooting_option(recipient,target)[0]>quality*1.25:
                    better.append(candidate)
        hazard=.45*quality/patience
        prefer_shot=clear and not better and self.hold_time>.45
        if role!='GK' and local[0]>70 and distance<35 and self.hold_time>.45:
            if prefer_shot or self.random_events[step,owner,1]<1-math.exp(-hazard*self.dt):
                keeper=11 if team==0 else 0
                keeper_y=self.local(self.pos[keeper],team)[1]
                aim=31.8 if keeper_y>=34 else 36.2
                # Dispersion is always nonzero, and increases with range and pressure.
                sigma=1.4+.18*distance+1.5*max(0,2-pressure)+(1-self.styles[owner].finishing)*3
                aim+=float(self.random_motion[step,owner,1])*sigma
                self.release('shot',self.world(np.array([105.,np.clip(aim,2,66)]),team),time,
                             speed=22+6*self.random_events[step,owner,3])
                return
        dwell=(2.5 if role=='GK' else 1.0)*patience
        if self.state[owner]=='link':dwell=.8
        if pressure<2:dwell=min(dwell,.6)
        if getattr(self,'keeper_holding',False):dwell=min(dwell,3.)
        if self.hold_time<dwell:return
        if clear:candidates=better
        ranked=[]
        for value,recipient,target,lane in candidates:
            receiver_space=min(norm(self.pos[j]-target) for j in opponents)
            lofted=self.loft_option(owner,target)
            if (lane<1.3 and not lofted) or receiver_space<1.5:continue
            margin=self.pass_margin(target,team,lofted=lofted)
            if margin<-.5 and pressure>2:continue
            value-=2*max(0,1-margin)
            advance=self.directions[owner]*(target[0]-self.ball[0])
            # Safe backward/lateral outlets keep a possession alive under pressure.
            value+=.65*min(lane,7)-.025*patience*max(advance,0)
            if lane>2:value+=.08*min(20,max(advance,0))
            # Backward passes remain available when forward routes are blocked.
            ranked.append((value,recipient,target,lane))
        if not ranked:
            if candidates and ((role=='GK' and self.hold_time>4) or (pressure<2 and self.hold_time>.8)):
                # If every option is risky, pressure can force a hurried release.
                # Clear shooting chances have already been considered above.
                ranked=[max(candidates,key=lambda row:row[3]+.04*norm(row[2]-self.ball))]
            else:return
        _,recipient,target,lane=max(ranked,key=lambda row:row[0])
        rate=1.0/patience if pressure>3 else 2.
        if not ((role=='GK' and self.hold_time>5) or (pressure<2 and self.hold_time>1.5)) and self.random_events[step,owner,2]>1-math.exp(-rate*self.dt):return
        # Imperfect execution can carry a pass across the touchline; don't clip it inward.
        error_scale=(1-self.skill[owner])*(2+max(0,3-pressure)*5)
        target=target+self.random_motion[step,owner]*error_scale
        lofted=self.loft_option(owner,target)
        self.release('pass',target,time,recipient,speed=20.)
        if lofted:
            self.flight['loft_peak']=5.
            self.flight['total_distance']=self.flight['remaining']
        # Match the lead calculation and brake at the intended reception point.
        self.flight['receive_target']=target.copy()

    def move_ball(self,time,step):
        self.clock=time
        if self.restart:
            restart=self.restart
            self.owner=None;self.possession=None;self.ball_state='dead';self.flight=None
            # Administrative return during a stoppage, never claimed as a pass.
            self.ball+=limit(restart['target']-self.ball,8*self.h)
            self.ball_velocity[:]=0
            if time>=restart['at'] and norm(self.ball-self.pos[restart['player']])<.9 and norm(self.ball-restart['target'])<.1:
                self.take_possession(restart['player'],'restart',time)
                self.restart=None
            return
        super().move_ball(time,step)

    def position_frames(self):
        for frame in self.frames():
            yield {'kind':'frame','t':frame['t'],'players':frame['players'],
                   'ball':{key:frame['ball'][key] for key in ('x','y')}}


def generate_match(job):
    index,seed,duration,hz,out,*options=job
    html=bool(options[0]) if options else False
    tempo=options[1] if len(options)>1 else 'mixed'
    start_from=options[2] if len(options)>2 else 'mixed'
    league_seed=int(options[3]) if len(options)>3 else LEAGUE_SEED
    from team_player.simulator.league import draw_teams
    if start_from not in ('mixed','goalkeeper','midfield'):raise ValueError('Unknown starting situation')
    rng=np.random.default_rng(np.random.SeedSequence([seed,index]))
    team_names=draw_teams(rng)
    cfg=SimulationConfig(team_names=team_names,league_seed=league_seed,formation_b='4231',seed=int(rng.integers(0,2**32)),duration=duration,hz=hz,
        scenario='free_play',start=0,end=duration,match_tempo=tempo,
        striker_style=str(rng.choice(list(STRIKERS))),fullback_style=str(rng.choice(list(FULLBACKS))),
        winger_style=str(rng.choice(list(WINGERS))),build_up_route=str(rng.choice(['wing_release','return_run'])))
    acquisition=start_from if start_from!='mixed' else str(rng.choice(['goalkeeper','goalkeeper','midfield']))
    cfg.acquisition='goalkeeper' if acquisition=='goalkeeper' else 'turnover'
    sim=MatchSimulator(cfg);name=f'match_{index:04d}.jsonl'
    meta=sim.metadata();meta.update(input_type='positions_only',match_id=Path(name).stem)
    with (Path(out)/name).open('x') as handle:
        handle.write(json.dumps(meta)+'\n')
        for frame in sim.position_frames(): handle.write(json.dumps(frame,separators=(',',':'))+'\n')
    if html:
        from team_player.app.match_replay import create_match_replay
        create_match_replay(Path(out)/name,(Path(out)/name).with_suffix('.html'))
    return {'file':name,'config':asdict(cfg),'master_seed':seed,'player_profiles':{pid:asdict(style) for pid,style in zip(sim.ids,sim.styles)}}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True);p.add_argument('--private-out',required=True)
    p.add_argument('--num-files',type=int,default=1);p.add_argument('--seed',type=int,default=42)
    p.add_argument('--league-seed',type=int,default=LEAGUE_SEED,help='Stable player attributes; keep fixed across comparison datasets')
    p.add_argument('--duration',type=float,default=MATCH_DURATION_S);p.add_argument('--hz',type=int,default=SAMPLE_HZ)
    p.add_argument('--workers',type=int,default=1)
    p.add_argument('--tempo',choices=TEMPOS,default='mixed',help='Build-up patience; durations remain emergent')
    p.add_argument('--start-from',choices=('mixed','goalkeeper','midfield'),default='mixed',help='Initial situation; mixed chooses reproducibly per file')
    p.add_argument('--html',action='store_true',help='Also generate standalone HTML replays with local analysis (off by default)')
    a=p.parse_args();root=Path(a.out).resolve();private=Path(a.private_out).resolve()
    SimulationConfig(duration=a.duration,hz=a.hz).validate()
    if a.num_files<1 or a.workers<1 or a.seed<0 or a.league_seed<0: p.error('Positive counts and nonnegative seed required')
    if root==private or root in private.parents: p.error('Private map must be outside public tree')
    if root.exists() or private.exists(): p.error('Use new output paths')
    private.parent.mkdir(parents=True,exist_ok=True);root.mkdir(parents=True)
    from team_player.simulator.league import public_roster
    (root/'roster.json').write_text(json.dumps({'league_seed':a.league_seed,'players':public_roster()},indent=2))
    jobs=[(i,a.seed,a.duration,a.hz,str(root),a.html,a.tempo,a.start_from,a.league_seed) for i in range(a.num_files)]
    with os.fdopen(os.open(private,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as handle:
        if a.workers==1: results=list(map(generate_match,jobs))
        else:
            with ProcessPoolExecutor(max_workers=a.workers) as pool: results=list(pool.map(generate_match,jobs))
        json.dump({'version':'0.4.0','matches':results},handle,indent=2)
    print(f'Generated {a.num_files} continuous position-only match streams in {root}')

if __name__=='__main__': main()
