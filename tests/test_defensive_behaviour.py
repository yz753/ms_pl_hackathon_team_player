import unittest
import numpy as np
from team_player.simulator.match import MatchSimulator
from team_player.simulator.engine import SimulationConfig
from team_player.utils.geometry import norm

class DefensiveBehaviourTests(unittest.TestCase):
    def test_marking_is_unique_and_continues_during_pass(self):
        sim=MatchSimulator(SimulationConfig(duration=3))
        sim.owner=8;sim.possession=0;sim.ball=sim.pos[8].copy()
        sim.target_positions(0,1)
        self.assertEqual(len(sim.marking),len(set(sim.marking.values())))
        self.assertNotIn(8,sim.marking.values())
        sim.release('pass',sim.pos[9].copy(),0,9,20)
        sim.target_positions(.1,2)
        self.assertTrue(sim.marking)
        self.assertEqual(len(sim.marking),len(set(sim.marking.values())))
        self.assertNotIn(9,sim.marking.values())
        self.assertTrue(any('CB' in sim.roles[i] for i in sim.marking))

    def test_soft_parry_can_be_collected_by_keeper(self):
        sim=MatchSimulator(SimulationConfig(duration=5))
        sim.pos[:]=[50,5];sim.pos[11]=[100,34];sim.vel[:]=0
        sim.owner=None;sim.possession=0;sim.last_team=0
        sim.ball=np.array([99.,35.5]);sim.ball_velocity=np.array([25.,0.])
        sim.last_kicker=10;sim.release_time=0;sim.random_events[:,:,4]=0
        sim.flight={'type':'shot','team':0,'from':10,'to':None,'target':np.array([105.,35.5]),'remaining':6.}
        sim.move_ball(.1,1)
        self.assertEqual(sim.ball_state,'loose')
        for k in range(1,150):
            time=.1+k*sim.h;step=min(49,k//5+1)
            target=sim.target_positions(time,step)
            sim.move_players(target,time);sim.move_ball(time,step)
            if sim.owner is not None:break
        self.assertEqual(sim.owner,11)
        self.assertLessEqual(norm(sim.ball-sim.pos[11]),1.05)

    def test_default_build_up_is_slower_than_direct(self):
        normal=MatchSimulator(SimulationConfig(duration=1))
        direct=MatchSimulator(SimulationConfig(duration=1,match_tempo='direct'))
        self.assertTrue(np.all(normal.patience>direct.patience))
        self.assertLess(normal.carry_speed,direct.carry_speed)

if __name__=='__main__':unittest.main()
