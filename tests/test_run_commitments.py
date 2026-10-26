import unittest
import numpy as np
from team_player.simulator.match import MatchSimulator
from team_player.simulator.engine import SimulationConfig

class RunCommitmentTests(unittest.TestCase):
    def test_runs_include_diagonal_channels_and_stay_in_pitch(self):
        sim=MatchSimulator(SimulationConfig(duration=2));sim.pos[9]=[65,55]
        points=np.array([sim.choose_run_destination(9) for _ in range(60)])
        self.assertTrue(np.all((points[:,1]>=8)&(points[:,1]<=60)))
        self.assertTrue(np.any(points[:,1]<50))
        self.assertTrue(np.any(points[:,1]>55))
        self.assertTrue(np.all(points[:,0]>sim.pos[9,0]))
    def test_committed_marker_does_not_reassign_to_new_nearest_player(self):
        sim=MatchSimulator(SimulationConfig(duration=10))
        sim.owner=8;sim.possession=0;sim.pos[8]=[54,20];sim.ball=sim.pos[8].copy()
        sim.pos[9]=[68,55];sim.vel[9]=[2,-1];sim.pos[12]=[68,58]
        sim.pos[7]=[68,57];sim.pos[16]=[54,21];sim.pos[17]=[56,22]
        sim.mark_assignments={12:(9,8.)};sim.last_step=10
        target=sim.phase_shape(0,sim.ball);sim.defensive_targets(target)
        self.assertEqual(sim.marking[12],9)
        sim.pos[7]=sim.pos[12]+[.1,0];sim.last_step=20
        target=sim.phase_shape(0,sim.ball);sim.defensive_targets(target)
        self.assertEqual(sim.marking[12],9)
        self.assertLess(np.linalg.norm(target[12]-sim.pos[9]),4)
    def test_commitment_expires_and_turnover_releases_assignment(self):
        sim=MatchSimulator(SimulationConfig(duration=10));sim.mark_assignments={12:(9,.1)};sim.last_step=20
        sim.defensive_targets(sim.phase_shape(0,sim.ball));self.assertNotIn(12,sim.mark_assignments)
        sim.mark_assignments={12:(9,8.)};sim.owner=19;sim.possession=1;sim.ball=sim.pos[19].copy()
        sim.defensive_targets(sim.phase_shape(1,sim.ball));self.assertNotIn(12,sim.mark_assignments)

if __name__=='__main__':unittest.main()

class PersonalityRateTests(unittest.TestCase):
    def test_stays_up_front_does_not_share_drops_and_returns_trigger_rate(self):
        deep=MatchSimulator(SimulationConfig(duration=1,start=0,end=1,striker_style='drops_and_returns'))
        box=MatchSimulator(SimulationConfig(duration=1,start=0,end=1,striker_style='stays_up_front'))
        for sim in (deep,box):sim.random_events[:]=.02;sim.update_intentions(.4,2)
        self.assertEqual(deep.state[10],'drop');self.assertEqual(box.state[10],'shape')
    def test_decoy_runner_can_make_off_ball_channel_run(self):
        sim=MatchSimulator(SimulationConfig(duration=1,start=0,end=1,striker_style='decoy_runner'))
        sim.owner=8;sim.possession=0;sim.pos[8]=[55,34];sim.ball=sim.pos[8].copy();sim.random_events[:]=0
        sim.update_intentions(.4,2)
        self.assertEqual(sim.state[10],'decoy')
    def test_stays_back_has_lower_advance_rate(self):
        active=MatchSimulator(SimulationConfig(duration=1,start=0,end=1,scenario='free_play',fullback_style='often_overlaps'))
        conservative=MatchSimulator(SimulationConfig(duration=1,start=0,end=1,scenario='free_play',fullback_style='stays_back'))
        for sim in (active,conservative):
            sim.owner=8;sim.possession=0;sim.pos[8]=[50,34];sim.ball=sim.pos[8].copy();sim.random_events[:]=.02;sim.update_intentions(.4,2)
        self.assertEqual(active.state[4],'overlap');self.assertEqual(conservative.state[4],'shape')

class RunHoldTests(unittest.TestCase):
    def test_arriving_winger_holds_position_before_returning_to_shape(self):
        sim=MatchSimulator(SimulationConfig(duration=8,start=0,end=8,scenario='free_play'))
        sim.owner=8;sim.possession=0;sim.ball=sim.pos[8].copy()
        sim.state[9]='decoy';sim.state_since[9]=0;sim.run_destinations[9]=sim.pos[9].copy()
        sim.update_intentions(3,15);self.assertEqual(sim.state[9],'decoy')
        sim.update_intentions(6.2,31);self.assertEqual(sim.state[9],'shape')

class ReturnSupportTests(unittest.TestCase):
    def test_low_urgency_hold_preserves_midfield_support_without_teleporting(self):
        sim=MatchSimulator(SimulationConfig(duration=2,striker_style='drops_and_stays'))
        sim.owner=8;sim.possession=0;sim.pos[8]=[85,30];sim.ball=sim.pos[8].copy()
        sim.pos[10]=[50,34];sim.state[10]='return';sim.return_hold_until[10]=12
        sim.pos[11:]=[98,55]
        target=sim.target_positions(.4,2)
        self.assertLessEqual(target[10,0],65)
        self.assertGreaterEqual(target[10,0],50)
        self.assertEqual(sim.pos[10,0],50)
