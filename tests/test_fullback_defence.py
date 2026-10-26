"""Fullback figures without the ball: phases, no double count, and how the AI reads them."""
import unittest
import numpy as np
from team_player.analyser.tactics import TacticalTracker
from team_player.config.analysis import RECOVERY_SPEED_M_S, COUNTER_WINDOW, DEF_TEMPLATES
from team_player.agents.narration import defence_reading, defence_from_row, check_grounding


class RecoveryBaselineTests(unittest.TestCase):
    def test_baseline_starts_where_he_lost_the_ball(self):
        p0, spot = np.array([80.0, 8.0]), np.array([30.0, 12.0])
        base, vel = TacticalTracker.recovery_baseline(p0, spot, 0.0)
        self.assertTrue(np.allclose(base, p0))   # far forward at the loss: no charge in recovery
        self.assertAlmostEqual(float(np.linalg.norm(vel)), RECOVERY_SPEED_M_S)

    def test_baseline_moves_at_the_average_recovery_speed_and_stops_at_the_spot(self):
        p0, spot = np.array([80.0, 8.0]), np.array([30.0, 8.0])
        base, _ = TacticalTracker.recovery_baseline(p0, spot, 5.0)
        self.assertAlmostEqual(float(np.linalg.norm(base - p0)), 5 * RECOVERY_SPEED_M_S)
        base, vel = TacticalTracker.recovery_baseline(p0, spot, 1000.0)
        self.assertTrue(np.allclose(base, spot));self.assertTrue(np.allclose(vel, 0))

    def test_templates_cover_every_ball_bin(self):
        for role in ("LB", "RB"):
            self.assertEqual(len(DEF_TEMPLATES[role]), 14)
            self.assertTrue(all((x < 52.5) and ((y < 34) == (role == "LB")) for x, y in DEF_TEMPLATES[role]))


class PipelinePhaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from team_player.simulator.match import MatchSimulator
        from team_player.simulator.engine import SimulationConfig
        from team_player.analyser.live import LivePipeline
        from team_player.analyser.presentation import Preferences
        sim = MatchSimulator(SimulationConfig(seed=11, duration=120, hz=10, scenario='free_play', start=0, end=120, formation_b='4231'))
        meta = sim.metadata();meta.update(input_type='positions_only', match_id='def_test')
        pipe = LivePipeline(meta);pref = Preferences(player=meta['players'][1]['id']);cls.eps = []
        for fr in sim.position_frames():
            cls.eps += pipe.update(fr, pref)['tactics']['completed']
        cls.eps += pipe.finish()

    def test_recovery_then_settled_and_recovery_is_short(self):
        d = [e for e in self.eps if e['kind'] in ('fullback_recovery', 'fullback_defence')]
        self.assertTrue(any(e['kind'] == 'fullback_recovery' for e in d))
        for e in d:
            if e['kind'] == 'fullback_recovery':
                self.assertLessEqual(e['duration_s'], COUNTER_WINDOW + 0.2)
            self.assertEqual(set(e['value']), {'threat_start', 'shots_xg', 'windows', 'net'})
        for p in {e['player'] for e in d}:
            mine = sorted([e for e in d if e['player'] == p], key=lambda e: e['t_start'])
            for a, b in zip(mine, mine[1:]):
                self.assertGreaterEqual(b['t_start'] + 1e-6, a['t_end'])          # phases never overlap
                if b['kind'] == 'fullback_defence' and abs(b['t_start'] - a['t_end']) < 1e-6:
                    self.assertEqual(a['kind'], 'fullback_recovery')

    def test_defending_episodes_are_kept_out_of_the_attacking_figures(self):
        from team_player.analyser.comparison import add_episode, FIELDS
        row = dict.fromkeys(FIELDS, 0.)
        for e in self.eps:
            if e['kind'] in ('fullback_recovery', 'fullback_defence'):
                add_episode(row, e)
        self.assertEqual(row['indiv_net'], 0.);self.assertEqual(row['value_net'], 0.)
        self.assertGreater(row['def_rec_n'] + row['def_set_n'], 0)


class DefenceReadingTests(unittest.TestCase):
    def test_short_lines_with_both_numbers_that_pass_the_checks(self):
        for own, team in ((0.01, 0.2), (0.01, -0.2), (-0.01, 0.2), (-0.01, -0.2), (0.001, 0.01)):
            b = defence_reading("A03", own, team)
            self.assertLessEqual(len(b["text"].split()), 32, b["text"])
            self.assertIn(f"{own:+.3f}", b["text"]);self.assertIn(f"{team:+.2f}", b["text"])
            self.assertIn("defending value", b["text"]);self.assertIn("xG prevented", b["text"])
            self.assertEqual(check_grounding(b["text"], b["facts"], "fan"), [], b["text"])
        self.assertIn("can't see tackles", defence_reading("A03", -0.01, -0.2)["text"])

    def test_agent_cannot_call_him_a_poor_defender(self):
        facts = defence_reading("A03", -0.01, -0.2)["facts"]
        for text in ("A03 was passive without the ball: -0.010.", "A03 is a poor defender: -0.010."):
            self.assertTrue(check_grounding(text, facts, "fan"), text)

    def test_only_fullbacks_get_a_defending_line(self):
        row = {"id": "A03", "role": "LB", "opp_possession_s": 300., "def_rec_own": .001, "def_set_own": .004,
               "def_rec_team": .05, "def_set_team": .1}
        d = defence_from_row(row)
        self.assertAlmostEqual(d["own"], 0.01);self.assertAlmostEqual(d["team"], 0.3)
        self.assertIsNone(defence_from_row({**row, "id": "A09", "role": "ST"}))


if __name__ == "__main__":
    unittest.main()
