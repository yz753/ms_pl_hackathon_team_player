"""Regression checks for the concrete failures reported in the first replay."""
from dataclasses import replace
import json
import unittest
import numpy as np
from team_player.simulator.engine import Simulator, SimulationConfig
from team_player.utils.geometry import norm
from team_player.schema import validate_frame


class RealismTests(unittest.TestCase):
    def test_controlled_ball_always_has_a_nearby_owner(self):
        for scenario in ('drop_deep','overlap','decoy'):
            sim=Simulator(SimulationConfig(seed=1,scenario=scenario,duration=45))
            for frame in sim.frames():
                ball=frame['ball'];owner=ball['owner']
                if owner is not None:
                    p=next(p for p in frame['players'] if p['id']==owner)
                    self.assertLessEqual(np.hypot(p['x']-ball['x'],p['y']-ball['y']),1.051)
                    self.assertEqual(ball['state'],'controlled')
                if ball['state'] in ('loose','dead'):
                    self.assertIsNone(owner)
                    self.assertIsNone(frame['possession'])
                if ball['state'] in ('pass','shot'):
                    self.assertIsNone(owner)

    def test_shot_separates_from_shooter_and_has_one_terminal_event(self):
        sim=Simulator(SimulationConfig(duration=2))
        sim.pos[:,1]=5
        sim.pos[10]=[87,34]
        sim.owner=10;sim.ball=sim.pos[10].copy();sim.possession=0
        sim.release('shot',np.array([105.,31.4]),0,speed=26)
        initial=sim.pos[10].copy()
        for step in range(1,51):
            sim.move_players(sim.pos.copy(),step*sim.h)
            sim.move_ball(step*sim.h,1)
            if sim.finished: break
        self.assertIsNone(sim.owner)
        self.assertGreater(norm(sim.ball-sim.pos[10]),15)
        self.assertLess(norm(sim.pos[10]-initial),2)
        self.assertEqual(sim.score,[1,0])
        self.assertEqual([e['type'] for e in sim.events],['possession_start','shot','goal','possession_end'])

    def test_no_distant_reception(self):
        sim=Simulator(SimulationConfig(duration=1))
        sim.ball=np.array([52.5,34])
        sim.pos[10]=[55,34]
        with self.assertRaises(RuntimeError): sim.take_possession(10,'reception')

    def test_loose_ball_is_pursued_and_recovered(self):
        sim=Simulator(SimulationConfig(duration=8))
        sim.owner=None;sim.flight=None;sim.ball_state='loose';sim.possession=None
        sim.ball=np.array([25.,20.]);sim.pos[2]=[21,20]
        frames=list(sim.frames())
        recoveries=[f for f in frames if any(e['type']=='recovery' for e in f['events'])]
        self.assertTrue(recoveries)
        self.assertLess(recoveries[0]['t'],4)

    def test_contested_loose_ball_does_not_deadlock(self):
        sim=Simulator(SimulationConfig(duration=3))
        sim.owner=None;sim.flight=None;sim.ball_state='loose';sim.possession=None
        sim.ball=np.array([30.,34.]);sim.pos[5]=[30,34.92];sim.pos[21]=[30,33.08]
        frames=list(sim.frames())
        self.assertTrue(any(f['ball']['owner'] is not None for f in frames[1:11]))

    def test_loose_pass_does_not_erase_receiving_intention(self):
        sim=Simulator(SimulationConfig(duration=5))
        sim.state[10]='drop';sim.state_since[10]=1
        sim.owner=None;sim.flight=None;sim.ball_state='loose';sim.possession=None
        sim.last_team=0;sim.ball=np.array([30.,34.])
        sim.update_intentions(3,3)
        self.assertEqual(sim.state[10],'drop')

    def test_receiver_moves_to_meet_pass(self):
        sim=Simulator(SimulationConfig(duration=1))
        sim.release('pass',np.array([22.,24.]),0,receiver=2,speed=18)
        targets=sim.target_positions(.1,1)
        np.testing.assert_allclose(targets[2],sim.flight['target'])

    def test_realistic_parameter_bounds_and_private_styles(self):
        base=SimulationConfig(duration=1)
        for style in ('drops_and_returns','stays_up_front','decoy_runner','drops_and_stays'):
            sim=Simulator(replace(base,striker_style=style))
            self.assertLessEqual(sim.speeds.max(),9.9)
            self.assertLessEqual(sim.accelerations.max(),4.2)
            public=json.dumps([sim.metadata(),*sim.frames()])
            for secret in (style,'return_urgency','top_speed','drop','styles','states'):
                self.assertNotIn('"'+secret+'"',public)
        deep=Simulator(replace(base,striker_style='drops_and_returns'))
        box=Simulator(replace(base,striker_style='stays_up_front'))
        self.assertGreater(deep.styles[10].drop,box.styles[10].drop)
        self.assertLess(deep.styles[10].top_speed,box.styles[10].top_speed)

    def test_return_urgency_changes_return_target(self):
        sims=[Simulator(SimulationConfig(duration=10,striker_style=style)) for style in ('drops_and_returns','drops_and_stays')]
        for sim in sims:
            sim.set_state(10,'return',2)
            sim.pos[10]=[35,28];sim.ball=np.array([48.,34.]);sim.owner=5
        # Exercise the sampled midfield-hold branch; the immediate-return branch remains possible.
        sims[1].return_hold_until[10]=8
        fast=sims[0].target_positions(3,3)[10,0]
        patient=sims[1].target_positions(3,3)[10,0]
        self.assertGreater(fast,patient+10)

    def test_player_ids_and_ball_state_visible_in_replay_source(self):
        from pathlib import Path
        source=(Path(__file__).parents[1]/'team_player'/'app'/'replay.py').read_text()
        self.assertIn('${p.id} · ${lookup[p.id].role}',source)
        self.assertIn('ballState',source)
        self.assertIn('No established possession',source)

if __name__=='__main__': unittest.main()
