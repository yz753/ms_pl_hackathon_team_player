"""Bounded, causal publication evidence for a lost winger passing option.

Reuses the analyser's existing lane calculation. Does not change any movement score.
"""
from collections import deque
import copy

class PassingOptionWatch:
    def __init__(self, analyzer):
        self.analyzer = analyzer
        self.samples = deque(maxlen=160)
        self.events = deque(maxlen=300)
        self.findings = {}

    def update(self, packet, episodes):
        frame = packet['frame']; now = frame['t']
        self.events.extend({'t':now, **e} for e in frame.get('events', []))
        while self.events and self.events[0]['t'] < now-60:
            self.events.popleft()
        analysis = packet.get('analysis')
        if analysis is None:
            return
        fields = self.analyzer.fields
        self.samples.append({'t':now, 'possession':frame['possession'],
            'positions':fields['positions'].copy(), 'velocities':fields['velocities'].copy(),
            'ball':fields['ball'].copy(),
            'options':{p['player']:copy.deepcopy(p.get('pass_option')) for p in analysis['players'] if p['role'] in ('LW','RW')}})
        self.findings = {k:v for k,v in self.findings.items() if now-v['observed_at']<=45}
        for ep in episodes:
            if ep.get('kind')!='winger_run' or not ep.get('decoy') or ep.get('space_used'):
                continue
            pid=ep['player']; start=ep['t_start']; end=ep.get('t_end',now)
            key=f'{pid}:{start}'
            if key in self.findings or now-end>10 or (ep.get('value') or {}).get('net',0)>=0:
                continue
            team=ep['team']
            before=[s for s in self.samples if start-1.5<=s['t']<=start and s['possession']==team][-2:]
            after=[s for s in self.samples if max(start,end-1.5)<=s['t']<=end and s['possession']==team][-2:]
            if len(before)<2 or len(after)<2 or not all((s['options'].get(pid) or {}).get('accessible') for s in before):
                continue
            if any((s['options'].get(pid) or {}).get('accessible') for s in after):
                continue
            loss=next((e for e in self.events if end<=e['t']<=min(now,end+8) and e.get('type')=='possession_change' and e.get('team') not in (None,team)),None)
            if not loss or any(start<=e['t']<=loss['t'] and e.get('type') in ('shot','goal') and e.get('team')==team for e in self.events):
                continue
            index=self.analyzer.index[pid]; team_index=int(self.analyzer.teams[index])
            held_position=before[-1]['positions'][index]; checks=[]
            for sample in after:
                held=sample['positions'].copy(); held[index]=held_position
                option=self.analyzer.pass_options(held,sample['velocities'],sample['ball'],team_index).get(pid) or {}
                checks.append({'t':sample['t'],'actual_accessible':False,'earlier_position_accessible':bool(option.get('accessible'))})
            if not all(c['earlier_position_accessible'] for c in checks):
                continue
            self.findings[key]={'player':pid,'t_start':start,'t_end':end,'observed_at':now,
                'loss_at':loss['t'],'opponent':loss['team'],'lane_checks':checks,
                'value':copy.deepcopy(ep.get('value')),'individual':copy.deepcopy(ep.get('individual')),
                'limitation':'An accessible option disappeared during the run, then possession was lost. Holding the earlier position preserves a lane in the same later geometry; this does not establish that the run caused the turnover.'}

    def for_player(self, player):
        rows=[v for v in self.findings.values() if v['player']==player]
        return copy.deepcopy(max(rows,key=lambda v:v['observed_at'])) if rows else None

    def forward_stall(self, player, episodes):
        """Report a received forward link with blocked continuation and observed loss."""
        import numpy as np
        if not self.samples:return None
        now=self.samples[-1]['t'];team=player[0]
        for ep in reversed(list(episodes)):
            if ep.get('player')!=player or ep.get('kind')!='striker_drop' or not ep.get('received'):continue
            for link in reversed(ep.get('forward_passes', [])):
                t=link['t']
                if not 0<=now-t<=40 or link.get('forward_m',0)<5 or (link.get('end_local') or [0])[0]<52.5:continue
                loss=next((e for e in self.events if t<e['t']<=now and e.get('type')=='possession_change' and e.get('team') not in (None,team)),None)
                if not loss or any(t<=e['t']<=loss['t'] and e.get('type') in ('shot','goal') and (e.get('team')==team or str(e.get('player','')).startswith(team)) for e in self.events):continue
                sample=min(self.samples,key=lambda x:abs(x['t']-t))
                if abs(sample['t']-t)>.6:continue
                a=self.analyzer;idx=a.index[link['receiver']];ti=int(a.teams[idx]);P=sample['positions'];ball=P[idx]
                options=a.pass_options(P,sample['velocities'],ball,ti)
                forward={p:v for p,v in options.items() if v['forward_m']>=5 and not v['offside']}
                blocked=[p for p,v in forward.items() if not v['accessible']]
                near=np.linalg.norm(P-ball,axis=1)<=20;attackers=int(np.sum(near & (a.teams==ti)));defenders=int(np.sum(near & (a.teams!=ti)))
                if not blocked or (any(v['accessible'] for v in forward.values()) and defenders<=attackers):continue
                return {'player':player,'t_start':ep['t_start'],'observed_at':now,'pass':link,'loss_at':loss['t'],'blocked_players':blocked,'nearby_attackers':attackers,'nearby_defenders':defenders,'value':ep.get('value'),'individual':ep.get('individual')}
        return None
