import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from team_player.simulator.engine import Simulator, SimulationConfig
from team_player.schema import validate_frame
from team_player.app.blob_transfer import safe_destination

class PossessionTests(unittest.TestCase):
    def test_terminal_control_stops(self):
        sim=Simulator(SimulationConfig(duration=10))
        sim.pos[12]=sim.ball+[.6,0]
        sim.take_possession(12,'tackle')
        self.assertTrue(sim.finished)
        self.assertEqual(sim.termination,'opponent_control')
        before=sim.ball.copy();sim.move_ball(1,1)
        np.testing.assert_array_equal(before,sim.ball)

    def test_truncation_is_not_possession_end(self):
        sim=Simulator(SimulationConfig(duration=.1))
        frames=list(sim.frames())
        self.assertFalse(sim.finished)
        self.assertEqual(frames[-1]['events'][-1]['type'],'clip_truncated')

    def test_recovered_start_and_schema(self):
        sim=Simulator(SimulationConfig(duration=20,acquisition='turnover'))
        frames=list(sim.frames())
        self.assertNotEqual(frames[0]['ball']['owner'],'A01')
        self.assertEqual(frames[0]['events'][0]['source'],'turnover')
        previous=None
        for f in frames:
            validate_frame(f,sim.metadata(),previous);previous=f['t']
        if sim.finished:
            self.assertEqual(sum(e['type']=='possession_end' for f in frames for e in f['events']),1)
            self.assertFalse(any(f['possession']=='B' for f in frames[:-1]))

    def test_close_contest_probability(self):
        sim=Simulator(SimulationConfig(duration=5))
        sim.pos[12]=sim.ball+[.8,0];sim.hold_time=1
        sim.random_events[1,12,3]=0
        sim.move_ball(.02,1)
        self.assertEqual(sim.owner,12)
        self.assertTrue(any(e['type']=='contest' for e in sim.events))

    def test_blob_path_traversal_rejected(self):
        root=Path('/tmp/public')
        for name in ('../secret','/secret','role/../../secret','role\\secret'):
            with self.assertRaises(ValueError): safe_destination(root,name)

if __name__=='__main__': unittest.main()
