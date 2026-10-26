import json
from pathlib import Path
import unittest
import numpy as np
from team_player.simulator.match import MatchSimulator
from team_player.simulator.engine import SimulationConfig
from team_player.utils.geometry import norm

class ShotSelectionTests(unittest.TestCase):
    def snapshot(self):
        sim=MatchSimulator(SimulationConfig(duration=2,formation_b='433'))
        frame=json.loads((Path(__file__).parent/'fixtures/chance_91_1.json').read_text())
        sim.pos=np.array([[p['x'],p['y']] for p in frame['players']])
        sim.ball=np.array([frame['ball']['x'],frame['ball']['y']])
        sim.owner=21;sim.possession=1;sim.last_team=1;sim.hold_time=1
        sim.random_events[:]=.999999
        return sim

    def test_clear_91_1_chance_prefers_shot_to_wide_pass(self):
        sim=self.snapshot();sim.make_ball_decision(.1,1)
        self.assertEqual(sim.flight['type'],'shot')
        self.assertIsNone(sim.owner)

    def test_front_blocker_changes_shooting_assessment(self):
        sim=self.snapshot();quality,clear=sim.shooting_option(21,sim.ball)
        self.assertTrue(clear)
        sim.pos[2]=[20,34]
        blocked,clear=sim.shooting_option(21,sim.ball)
        self.assertFalse(clear);self.assertLess(blocked,quality)

    def test_back_line_recovers_goal_side_in_danger(self):
        sim=self.snapshot();target=sim.target_positions(.1,1)
        backs=[1,2,3,4]
        self.assertGreaterEqual(sum(target[i,0]<sim.ball[0] for i in backs),3)
        self.assertTrue(sim.covering)

    def test_keeper_gathers_without_teleport_or_distant_control(self):
        sim=MatchSimulator(SimulationConfig(duration=1))
        sim.owner=None;sim.possession=None;sim.ball_state='loose';sim.keeper_gather=11
        sim.ball=sim.pos[11]+np.array([-1.7,0.])
        before=sim.ball.copy()
        sim.move_ball(.02,1)
        self.assertLessEqual(norm(sim.ball-before),6*sim.h+1e-8)
        self.assertIsNone(sim.owner)
        for i in range(20):
            sim.move_ball(.04+i*sim.h,1)
            if sim.owner is not None:break
        self.assertEqual(sim.owner,11)
        self.assertLessEqual(norm(sim.ball-sim.pos[11]),1.0)

    def test_goalkeeper_and_midfield_starting_shapes(self):
        keeper=MatchSimulator(SimulationConfig(duration=1,acquisition='goalkeeper'))
        midfield=MatchSimulator(SimulationConfig(duration=1,acquisition='turnover'))
        self.assertEqual(keeper.owner,0)
        self.assertGreater(keeper.pos[1,0],keeper.pos[2,0])
        self.assertNotEqual(midfield.owner,0)
        self.assertEqual(midfield.ball[0],48)
        for sim in (keeper,midfield):
            distances=norm(sim.pos[:,None,:]-sim.pos[None,:,:]);np.fill_diagonal(distances,np.inf)
            self.assertGreater(distances.min(),.65)
            np.testing.assert_equal(sim.ball,sim.pos[sim.owner])

if __name__=='__main__':unittest.main()
