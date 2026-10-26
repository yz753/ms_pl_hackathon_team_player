import unittest
import numpy as np
from team_player.simulator.match import MatchSimulator
from team_player.simulator.engine import SimulationConfig
from team_player.utils.geometry import norm

class MatchBehaviourTests(unittest.TestCase):
    def test_goal_restarts_from_opponent_keeper_in_both_directions(self):
        for scoring_team in (0,1):
            sim=MatchSimulator(SimulationConfig(duration=30,match_tempo='patient'))
            sim.last_team=scoring_team;sim.possession=None;sim.owner=None
            sim.ball=np.array([105. if scoring_team==0 else 0.,34.])
            sim.ball_state='dead';before=sim.pos.copy();sim.finish('goal')
            keeper=11 if scoring_team==0 else 0
            self.assertEqual(sim.restart['player'],keeper)
            np.testing.assert_array_equal(sim.pos,before)
            target=sim.restart['target'].copy();last_distance=norm(sim.ball-target)
            for step in range(1,200):
                time=step*sim.h
                targets=sim.target_positions(time,step)
                sim.make_ball_decision(time,step)
                sim.move_players(targets,time);sim.move_ball(time,step)
                distance=norm(sim.ball-target)
                self.assertLessEqual(distance,last_distance+1e-8)
                last_distance=distance
                if sim.restart is None:break
            # The four-second pause is intentionally not skipped to force ownership.
            for step in range(200,1000):
                if sim.restart is None:break
                time=step*sim.h
                # Keep random-array indices within the configured sample horizon.
                targets=sim.target_positions(time,min(300,step//5))
                sim.move_players(targets,time);sim.move_ball(time,min(300,step//5))
            self.assertIsNone(sim.restart)
            self.assertEqual(sim.owner,keeper)
            self.assertEqual(sim.possession,1-scoring_team)
            self.assertLessEqual(norm(sim.ball-sim.pos[keeper]),1.05)

    def test_stretched_keeper_parries_without_claiming_possession(self):
        sim=MatchSimulator(SimulationConfig(duration=1))
        sim.pos[:]=[50,5];sim.pos[11]=[100,34]
        sim.owner=None;sim.possession=0;sim.last_team=0
        sim.ball=np.array([99.,35.5]);sim.ball_velocity=np.array([25.,0.])
        sim.last_kicker=10;sim.release_time=0
        sim.flight={'type':'shot','team':0,'from':10,'to':None,
                    'target':np.array([105.,35.5]),'remaining':6.}
        sim.move_ball(.1,1)
        self.assertEqual(sim.ball_state,'loose')
        self.assertIsNone(sim.owner)
        self.assertTrue(any(e['type']=='parry' for e in sim.events))
        self.assertEqual(sum(sim.score),0)

    def test_long_hold_does_not_force_a_shot(self):
        sim=MatchSimulator(SimulationConfig(duration=1))
        sim.owner=10;sim.pos[10]=[85,34];sim.ball=sim.pos[10].copy();sim.hold_time=150
        sim.pos[12]=[90,34]  # An actual blocking defender: elapsed holding time is not a reason to shoot.
        sim.random_events[1,:,:]=.999999
        sim.make_ball_decision(0,1)
        self.assertFalse(any(e['type']=='shot' for e in sim.events))

    def test_dead_ball_cannot_be_kicked_before_restart(self):
        sim=MatchSimulator(SimulationConfig(duration=1));sim.finish('goal');sim.hold_time=100
        sim.make_ball_decision(0,1)
        self.assertIsNone(sim.flight)

    def test_miss_and_out_use_opponent_restart(self):
        for reason in ('missed_shot','out_of_play'):
            sim=MatchSimulator(SimulationConfig(duration=1));sim.last_team=1;sim.finish(reason)
            self.assertEqual(sim.restart['team'],0)
            if reason=='missed_shot':self.assertEqual(sim.restart['player'],0)

if __name__=='__main__':unittest.main()
