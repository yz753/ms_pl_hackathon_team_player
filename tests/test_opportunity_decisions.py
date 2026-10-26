import unittest
import numpy as np
from team_player.simulator.match import MatchSimulator
from team_player.simulator.engine import SimulationConfig

class OpportunityTests(unittest.TestCase):
 def test_turnover_opens_temporary_counter_window_but_restart_does_not(self):
  sim=MatchSimulator(SimulationConfig(duration=20))
  sim.ball=sim.pos[19].copy();sim.clock=3
  sim.take_possession(19,'tackle',3)
  self.assertTrue(sim.counter_window(1));self.assertFalse(sim.counter_window(0))
  sim.clock=18;self.assertFalse(sim.counter_window(1))
  sim.ball=sim.pos[0].copy();sim.take_possession(0,'restart',13)
  self.assertFalse(sim.counter_window(0))
 def test_two_markers_can_hold_same_runner_without_forcing_goal(self):
  sim=MatchSimulator(SimulationConfig(duration=10));sim.owner=8;sim.possession=0
  sim.pos[8]=[54,20];sim.ball=sim.pos[8].copy();sim.pos[9]=[68,55];sim.vel[9]=[2,-1]
  sim.pos[12]=[68,58];sim.pos[15]=[70,55];sim.pos[16]=[54,21];sim.pos[17]=[56,22]
  sim.mark_assignments={12:(9,8.),15:(9,8.)};sim.last_step=10
  sim.defensive_targets(sim.phase_shape(0,sim.ball))
  self.assertEqual(sim.marking[12],9);self.assertEqual(sim.marking[15],9)
  self.assertEqual(sum(sim.score),0)
 def test_open_forward_winger_beats_backward_keeper(self):
  sim=MatchSimulator(SimulationConfig(duration=1));sim.owner=10;sim.pos[10]=[42,25];sim.ball=sim.pos[10].copy()
  sim.pos[9]=[77,54];sim.pos[11:]=[[100,34]]+[[85+i*.5,5] for i in range(10)]
  rows={r[1]:r for r in sim.pass_candidates(10,0)}
  self.assertGreater(rows[9][0],rows[0][0]);self.assertGreater(sim.pass_margin(rows[9][2],0),0)
 def test_loft_can_clear_midfield_but_not_marked_landing_zone(self):
  sim=MatchSimulator(SimulationConfig(duration=1,striker_style='drops_and_returns'))
  sim.owner=10;sim.ball=np.array([40.,25.]);sim.pos[10]=sim.ball.copy()
  sim.pos[11:]=[90,60];sim.pos[12]=[60,25];target=np.array([80.,25.])
  self.assertTrue(sim.loft_option(10,target))
  self.assertLess(sim.pass_margin(target,0),0)
  self.assertGreater(sim.pass_margin(target,0,lofted=True),0)
  sim.pos[13]=target
  self.assertFalse(sim.loft_option(10,target))
  self.assertLess(sim.pass_margin(target,0,lofted=True),0)
 def test_lofted_ball_does_not_contact_midfield_feet_but_ground_ball_does(self):
  for lofted in (False,True):
   sim=MatchSimulator(SimulationConfig(duration=2));sim.pos[:]=[95.,60.]
   sim.owner=10;sim.pos[10]=[40,25];sim.ball=sim.pos[10].copy();sim.pos[9]=[80,25]
   sim.release('pass',np.array([80.,25.]),0,9,20.)
   sim.ball=np.array([60.,25.]);sim.flight['remaining']=20.;sim.pos[12]=[60.5,25]
   if lofted:sim.flight.update(loft_peak=5.,total_distance=40.)
   sim.move_ball(1.,1)
   if lofted:self.assertIsNone(sim.owner)
   else:self.assertEqual(sim.owner,12)
