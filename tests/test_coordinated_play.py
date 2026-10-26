import unittest
import numpy as np
from team_player.simulator.match import MatchSimulator
from team_player.simulator.engine import SimulationConfig
from team_player.utils.geometry import norm

class CoordinatedPlayTests(unittest.TestCase):
    def test_goalkeeper_build_up_shape_persists(self):
        sim=MatchSimulator(SimulationConfig(duration=10,intervention=False))
        initial=sim.pos.copy()
        for step in range(1,61):
            target=sim.target_positions(step*.1,step)
            for _ in range(sim.substeps):
                sim.move_players(target,step*.1);sim.move_ball(step*.1,step)
        self.assertGreater(abs(sim.pos[1,1]-sim.pos[4,1]),45)
        self.assertLess(norm(sim.pos[2]-initial[2]),1.)
        self.assertLess(norm(sim.pos[3]-initial[3]),1.)

    def test_secondary_pressure_and_lane_screening(self):
        sim=MatchSimulator(SimulationConfig(duration=2,intervention=False))
        sim.owner=8;sim.possession=0;sim.pos[8]=[60,34];sim.ball=sim.pos[8].copy()
        sim.pos[16]=[63,31];sim.pos[17]=[66,39];sim.pos[18]=[68,30]
        sim.pos[7]=[75,15];sim.pos[9]=[78,53];sim.pos[10]=[80,34]
        targets=sim.target_positions(.1,1)
        self.assertTrue(sim.supporting)
        self.assertIn(8,sim.supporting.values())
        self.assertTrue(sim.screening)
        for defender,recipient in sim.screening.items():
            route=sim.pos[recipient]-sim.ball;screen=targets[defender]-sim.ball
            self.assertAlmostEqual(route[0]*screen[1]-route[1]*screen[0],0,places=6)

    def test_held_save_cannot_be_tackled_and_release_ends_protection(self):
        sim=MatchSimulator(SimulationConfig(duration=1))
        sim.pos[0]=[7,34];sim.ball=sim.pos[0].copy();sim.pos[21]=[7.5,34]
        sim.take_possession(0,'save',0);sim.hold_time=5;sim.random_events[:]=0
        sim.move_ball(.1,1)
        self.assertEqual(sim.owner,0)
        self.assertTrue(sim.keeper_holding)
        sim.release('pass',np.array([18.,25.]),.2,2,20)
        self.assertFalse(sim.keeper_holding)

    def test_keeper_distributes_when_safe_options_are_exhausted(self):
        sim=MatchSimulator(SimulationConfig(duration=1))
        sim.take_possession(0,'save',0);sim.hold_time=6
        sim.random_events[:]=.99999
        target=sim.pos[2].copy()
        sim.pass_candidates=lambda owner,step:[(-5,2,target,.5)]
        sim.make_ball_decision(.1,1)
        self.assertEqual(sim.flight['type'],'pass')
        self.assertFalse(sim.keeper_holding)

    def test_pressure_can_force_risky_release_instead_of_stalemate(self):
        sim=MatchSimulator(SimulationConfig(duration=1))
        sim.owner=8;sim.possession=0;sim.ball=np.array([48.,34.]);sim.pos[8]=sim.ball.copy()
        sim.pos[16]=[48.8,34];sim.hold_time=2;sim.random_events[:]=.99999
        sim.pass_candidates=lambda owner,step:[(-5,2,np.array([30.,20.]),.5)]
        sim.make_ball_decision(.1,1)
        self.assertEqual(sim.flight['type'],'pass')

    def test_carried_ball_crossing_touchline_ends_possession(self):
        sim=MatchSimulator(SimulationConfig(duration=1))
        sim.owner=8;sim.possession=0;sim.last_team=0
        sim.pos[8]=[50,67.95];sim.ball=sim.pos[8].copy();sim.vel[8]=[0,3]
        sim.move_ball(.1,1)
        self.assertIsNone(sim.owner)
        self.assertEqual(sim.ball_state,'dead')
        self.assertLessEqual(sim.ball[1],68)
        self.assertEqual(sim.restart['reason'],'out_of_play')

    def test_interception_margin_prefers_open_route(self):
        sim=MatchSimulator(SimulationConfig(duration=1))
        sim.ball=np.array([30.,34.]);sim.pos[11:]=[90,60]
        open_margin=sim.pass_margin(np.array([50.,34.]),0)
        sim.pos[16]=[40,36]
        self.assertLess(sim.pass_margin(np.array([50.,34.]),0),open_margin)

if __name__=='__main__':unittest.main()
