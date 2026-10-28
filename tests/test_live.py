import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from team_player.analyser.live import LivePipeline
from team_player.simulator.engine import SimulationConfig
from team_player.simulator.match import MatchSimulator
from team_player.analyser.observation import PossessionGate,TrackingObserver
from team_player.analyser.presentation import Preferences,evidence_snapshot,render_overlay,overlay_is_current,validate_selection
from team_player.agents.foundry import FoundryDirector,bounded_explanation


def fixture_snapshot():
    return {'impact':{'key':'A09:test:12','player':'A09','kind':'striker_receiving_danger','t_start':12,'observed_at':12,'evidence':{'passing_lane_margin_s':.4}},'t':12.,'episode_id':3,'eligible':True,'player':{'player':'A09'},'team':{},'audience':'fan','focus':'all','changes':{},'limitations':['No causal proof'],'recent_events':[],
        'cards':[{'id':'c1','player':'A09','title':'Link','fan':'Offers a pass.','analyst':'Lane margin 0.4 s.',
                  'caveat':'Not causal proof.','topic':'support','value':.4,'unit':'s'}]}

class LiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sim=MatchSimulator(SimulationConfig(duration=8,scenario='free_play',start=0,end=8))
        cls.meta=sim.metadata();cls.frames=list(sim.position_frames())

    def test_source_has_positions_only(self):
        for f in self.frames:
            self.assertEqual(set(f),{'kind','t','players','ball'})
            self.assertEqual(set(f['ball']),{'x','y'})

    def test_prefix_and_hidden_label_invariance(self):
        a=LivePipeline(self.meta);b=LivePipeline(self.meta);prefs=Preferences()
        for frame in self.frames[:35]:
            poisoned=copy.deepcopy(frame);poisoned.update(possession='B',events=[{'type':'goal'}],style='secret')
            poisoned['ball'].update(owner='B09',state='shot')
            left=a.update(frame,prefs);right=b.update(poisoned,prefs)
            left.pop('processing_ms');right.pop('processing_ms');self.assertEqual(left,right)
        c=LivePipeline(self.meta)
        for f in self.frames[:35]:last=c.update(f,prefs)
        last.pop('processing_ms');self.assertEqual(last,left)

    def test_gate_qualifies_now_not_earlier(self):
        gate=PossessionGate(minimum_seconds=2)
        statuses=[]
        for t in (0,1,2):statuses.append(gate.update({'t':t,'possession':'A','ball':{'owner':'A09'}}))
        self.assertFalse(statuses[1]['eligible']);self.assertTrue(statuses[2]['eligible'])
        self.assertEqual(statuses[2]['decision_at'],2)
        after=gate.update({'t':2.1,'possession':'B','ball':{'owner':'B09'}})
        self.assertFalse(after['eligible']);self.assertNotEqual(after['episode_id'],statuses[-1]['episode_id'])

    def test_unsettled_period_suppresses(self):
        gate=PossessionGate(minimum_seconds=.1)
        gate.update({'t':0,'possession':'A','ball':{'owner':'A09'}})
        gate.update({'t':.5,'possession':'A','ball':{'owner':'A09'}})
        result=gate.update({'t':.6,'possession':None,'ball':{'owner':None}})
        self.assertFalse(result['eligible']);self.assertEqual(result['status'],'unsettled')

    def test_personalization_and_evidence_validation(self):
        s=fixture_snapshot();fan=render_overlay(s,['c1']);s['audience']='analyst';analyst=render_overlay(s,['c1'])
        self.assertNotEqual(fan['cards'][0]['body'],analyst['cards'][0]['body'])
        self.assertEqual(fan['cards'][0]['caveat'],analyst['cards'][0]['caveat'])
        with self.assertRaises(ValueError):validate_selection(s,['invented'])
        with self.assertRaises(ValueError):validate_selection(s,['c1','c1'])

    def test_stale_result_rejected(self):
        overlay=render_overlay(fixture_snapshot(),['c1'])
        self.assertTrue(overlay_is_current(overlay,13,3,Preferences()))
        self.assertFalse(overlay_is_current(overlay,11,3,Preferences()))
        self.assertFalse(overlay_is_current(overlay,25,3,Preferences()))
        self.assertFalse(overlay_is_current(overlay,13,4,Preferences()))
        self.assertFalse(overlay_is_current(overlay,13,3,Preferences(player='A07')))

    def test_match_continues_after_goal_without_position_reset(self):
        sim=MatchSimulator(SimulationConfig(duration=15))
        old=sim.pos.copy();sim.finish('goal')
        self.assertFalse(sim.finished);self.assertIsNotNone(sim.restart)
        self.assertTrue((old==sim.pos).all())

    def test_stream_validation_monotonic(self):
        observer=TrackingObserver(self.meta);observer.update(self.frames[0])
        with self.assertRaises(ValueError):observer.update(self.frames[0])

class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_sdk_tools_with_mocked_model_runs(self):
        try:import agent_framework
        except ImportError:self.skipTest('Install requirements-agent.txt for SDK test')
        class FakeAgent:
            def __init__(self,**kwargs):self.name=kwargs['name'];self.tools=kwargs['tools']
            async def run(self,*args):
                if self.name=='TacticalScout':
                    self.tools[0]();self.tools[1]();self.tools[2](['c1'])
                else:self.tools[0]();self.tools[1](['c1'])
        director=FoundryDirector.__new__(FoundryDirector);director.client=object()
        with patch('agent_framework.Agent',FakeAgent):result=await director.explain(fixture_snapshot())
        self.assertEqual(result['selection_method'],'microsoft_foundry_agents')
        self.assertEqual([x['tool'] for x in result['trace']][:5],['inspect_player_evidence','inspect_recent_context','propose_cards','check_alternatives','approve_overlay'])
        self.assertIsNotNone(result['narrative'])  # Narrator output or grounded local fallback

    async def test_missing_review_fails_closed(self):
        try:import agent_framework
        except ImportError:self.skipTest('Agent SDK not installed')
        class EmptyAgent:
            def __init__(self,**kwargs):pass
            async def run(self,*args):pass
        director=FoundryDirector.__new__(FoundryDirector);director.client=object()
        with patch('agent_framework.Agent',EmptyAgent):
            with self.assertRaises(RuntimeError):await director.explain(fixture_snapshot())

    async def test_slow_model_has_deadline(self):
        class Slow:
            async def explain(self,snapshot):await asyncio.sleep(10)
        with self.assertRaises(asyncio.TimeoutError):await bounded_explanation(Slow(),{},timeout=.01)

class WebTests(unittest.TestCase):
    def test_websocket_preferences_and_live_frames(self):
        try:
            from fastapi.testclient import TestClient
            from team_player.app.server import app
        except ImportError:self.skipTest('Install web requirements')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'tracking.jsonl'
            path.write_text('\n'.join(json.dumps(x) for x in [LiveTests.meta,*LiveTests.frames[:8]]))
            with patch.dict(os.environ,{'TEAM_PLAYER_MATCH':str(path),'TEAM_PLAYER_AI':'local','TEAM_PLAYER_HISTORY_DB':'','TEAM_PLAYER_DATA':d}):
                with TestClient(app) as client:
                    self.assertEqual(client.get('/').status_code,200)
                    self.assertEqual(client.get('/health').json()['ai_backend'],'local')
                    self.assertEqual(client.get('/private/style_map.json').status_code,404)
                    with client.websocket_connect('/ws') as ws:
                        self.assertEqual(ws.receive_json()['kind'],'metadata')
                        ws.send_json({'type':'preferences','player':'A07','audience':'analyst','focus':'runs'})
                        ticks=[]
                        while True:
                            msg=ws.receive_json()
                            if msg['kind']=='end':break
                            ticks.append(msg)
                        self.assertTrue(ticks)
                        self.assertEqual(ticks[-1]['preferences']['player'],'A07')
                        self.assertTrue(all(t['overlay'] is None for t in ticks))

if __name__=='__main__':unittest.main()
