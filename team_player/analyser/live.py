"""Streaming pipeline. Each update consumes one new observation, never a future frame."""
from collections import deque
import time
from team_player.analyser.analysis import Analyzer
from team_player.analyser.observation import TrackingObserver,PossessionGate
from team_player.analyser.presentation import evidence_snapshot
from team_player.analyser.tactics import TacticalTracker
from team_player.config.analysis import MIN_POSSESSION_S

class LivePipeline:
    def __init__(self,metadata,minimum_possession=MIN_POSSESSION_S):
        self.observer=TrackingObserver(metadata);self.gate=PossessionGate(minimum_possession)
        self.analyzer=Analyzer(metadata);self.recent=deque(maxlen=100)
        self.tactics=TacticalTracker(metadata,self.analyzer)
        self.episodes=deque(maxlen=60)  # recently completed tactical episodes (bounded)
        self.analysis=None;self.latest_gate=None;self.latencies=[]

    def update(self,raw,preferences):
        started=time.perf_counter()
        frame=self.observer.update(raw);gate=self.gate.update(frame)
        for event in frame['events']:self.recent.append({'t':frame['t'],**event})
        while self.recent and self.recent[0]['t']<frame['t']-10:self.recent.popleft()
        analysis=self.analyzer.update(frame)
        tactics=self.tactics.update(frame,analysis)
        self.episodes.extend(tactics['completed'])
        self.latest_gate=gate
        snapshot=None
        if analysis is not None:
            self.analysis=analysis;snapshot=evidence_snapshot(analysis,gate,preferences,self.recent,tactics,self.episodes)
        elapsed=(time.perf_counter()-started)*1000
        return {'frame':frame,'possession':gate,'analysis':analysis,'tactics':tactics,'snapshot':snapshot,'processing_ms':round(elapsed,3)}

    def finish(self):
        """Close open episodes at end of stream (offline pooling only)."""
        done=self.tactics.finish();self.episodes.extend(done);return done
