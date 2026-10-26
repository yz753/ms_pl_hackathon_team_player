import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from team_player.simulator.league import draw_teams,team_styles,public_roster
from team_player.simulator.engine import SimulationConfig
from team_player.simulator.match import MatchSimulator
from team_player.analyser.comparison import Accumulator,History,compare,FIELDS
from team_player.utils.io import fingerprint

class LeagueTests(unittest.TestCase):
    def test_uniform_draw_and_persistent_players(self):
        rng=np.random.default_rng(1);counts=dict.fromkeys('ABCDEFGHIJKLMNOPQRST',0)
        for _ in range(10000):
            a,b=draw_teams(rng);self.assertNotEqual(a,b);counts[a]+=1;counts[b]+=1
        self.assertTrue(all(850<n<1150 for n in counts.values()))
        self.assertEqual(team_styles('H'),team_styles('H'))
        self.assertNotEqual(team_styles('A'),team_styles('H'))
        a=MatchSimulator(SimulationConfig(team_names=('H','T'),league_seed=2026,formation_b='4231',duration=.1))
        b=MatchSimulator(SimulationConfig(team_names=('A','H'),league_seed=2026,formation_b='4231',duration=.1))
        self.assertEqual(a.styles[:11],b.styles[11:])
        self.assertEqual(a.metadata()['players'][10]['id'],'H09')
        self.assertEqual(len(public_roster()),220)
    def test_midfield_pocket_and_no_scripted_route(self):
        a=MatchSimulator(SimulationConfig(duration=1,build_up_route='wing_release'))
        b=MatchSimulator(SimulationConfig(duration=1,build_up_route='return_run'))
        self.assertGreaterEqual(a.local(a.choose_pocket(10),0)[0],34)
        self.assertLessEqual(a.local(a.choose_pocket(10),0)[0],58)
        for sim in (a,b):
            sim.owner=10;sim.pos[10]=[43,26];sim.ball=sim.pos[10].copy();sim.state[10]='link'
        ca=a.pass_candidates(10,0);cb=b.pass_candidates(10,0)
        self.assertEqual([x[:2] for x in ca],[x[:2] for x in cb])
    def test_open_long_forward_outlet_is_available(self):
        sim=MatchSimulator(SimulationConfig(duration=1));sim.owner=10;sim.pos[10]=[42,25];sim.ball=sim.pos[10].copy();sim.state[10]='link'
        sim.pos[9]=[77,54];sim.pos[11:]=[[100,34]]+[[85+i*.5,5] for i in range(10)]
        options=sim.pass_candidates(10,0)
        winger=next(row for row in options if row[1]==9)
        self.assertGreater(np.linalg.norm(winger[2]-sim.ball),30)
        self.assertGreater(sim.pass_margin(winger[2],0),0)
        self.assertGreater(winger[0],next(row[0] for row in options if row[1]==0))

class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.sim=MatchSimulator(SimulationConfig(duration=1));self.meta=self.sim.metadata()
    def test_incompatible_and_missing_evidence(self):
        acc=Accumulator(self.meta);rows=acc.rows
        result=compare(rows,'A09','B01',0)
        self.assertFalse(result['compatible']);self.assertGreater(len(result['warnings']),1)
        self.assertIsNone(result['left']['percentages']['box_s'])
        self.assertTrue(compare(rows,'A09','B09',0)['compatible'])
    def test_renamed_active_recording_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);active=root/'renamed.jsonl';active.write_text('public input')
            acc=Accumulator(self.meta);future=list(acc.rows.values());future[10]['observed_s']=600
            (root/'comparison_history.json').write_text(json.dumps({'version':1,'matches':[{'file':'original.jsonl','fingerprint':fingerprint(active),'players':future}]}))
            history=History(root,active)
            self.assertFalse(history.matches)
            self.assertEqual(history.rows(Accumulator(self.meta))['A09']['observed_s'],0)
    def test_accumulation_prefix_and_duplicate_rejected(self):
        from team_player.analyser.live import LivePipeline
        from team_player.analyser.presentation import Preferences
        acc=Accumulator(self.meta);pipe=LivePipeline(self.meta)
        for raw in self.sim.position_frames():
            packet=pipe.update(raw,Preferences());acc.update(packet['frame'],packet['analysis'])
        self.assertLessEqual(acc.rows['A09']['observed_s'],1)
        if packet['analysis']:
            with self.assertRaises(ValueError):acc.update(packet['frame'],packet['analysis'])

if __name__=='__main__':unittest.main()

class ConsultantTests(unittest.IsolatedAsyncioTestCase):
    async def test_consultant_cannot_remove_incompatibility(self):
        from team_player.agents.foundry import FoundryDirector
        try:import agent_framework
        except ImportError:self.skipTest('Agent SDK unavailable')
        class FakeAgent:
            def __init__(self,**kwargs):self.tools=kwargs['tools']
            async def run(self,*args):self.tools[0]();self.tools[1](['context'])
        sim=MatchSimulator(SimulationConfig(duration=.1));snap=compare(Accumulator(sim.metadata()).rows,'A09','B01',0)
        director=FoundryDirector.__new__(FoundryDirector);director.client=object()
        with patch('agent_framework.Agent',FakeAgent):result=await director.compare(snap)
        self.assertEqual(result['warnings'],snap['warnings'])
        self.assertEqual(result['method'],'microsoft_foundry_agent')

class ComparisonWebTests(unittest.TestCase):
    def test_random_team_ids_and_comparison_controls(self):
        try:from fastapi.testclient import TestClient
        except ImportError:self.skipTest('Install web requirements')
        from team_player.app.server import app
        import os
        sim=MatchSimulator(SimulationConfig(team_names=('H','T'),league_seed=2026,formation_b='4231',duration=.6))
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'match.jsonl';path.write_text('\n'.join(json.dumps(x) for x in [sim.metadata(),*sim.position_frames()]))
            with patch.dict(os.environ,{'TEAM_PLAYER_MATCH':str(path),'TEAM_PLAYER_AI':'local','TEAM_PLAYER_FAVOURITE':'A09','TEAM_PLAYER_HISTORY_DB':'','TEAM_PLAYER_DATA':d}):
                with TestClient(app) as client:
                    with client.websocket_connect('/ws') as ws:
                        meta=ws.receive_json();self.assertEqual(meta['preferences']['player'],'H09')
                        ws.send_json({'type':'comparison','left':'H09','right':'T01','review':True})
                        ticks=[]
                        while True:
                            msg=ws.receive_json()
                            if msg['kind']=='end':break
                            self.assertEqual(msg['kind'],'tick');ticks.append(msg)
                        last=ticks[-1];self.assertFalse(last['comparison']['compatible'])
                        self.assertEqual(last['comparison']['right']['id'],'T01')
                        self.assertLessEqual(last['comparison']['left']['observed_s'],.6)
                        self.assertEqual(last['consultant']['method'],'local_rules')
