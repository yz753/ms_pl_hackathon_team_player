"""Value models, online tactical episodes and similar-situation retrieval."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from team_player.analyser.models import XGModel, ThreatModel, pitch_control, fit_xg, fit_xt, to_local
from team_player.simulator.match import MatchSimulator
from team_player.simulator.engine import SimulationConfig
from team_player.analyser.live import LivePipeline
from team_player.analyser.presentation import Preferences
from team_player.analyser.situations import SituationIndex, FEATURES, choice_flags


def base():
    sim = MatchSimulator(SimulationConfig(duration=.1))
    meta = sim.metadata();frame = next(sim.position_frames())
    return meta, {p["id"]: [p["x"], p["y"]] for p in frame["players"]}


def frame(t, pos, ball):
    return {"kind": "frame", "t": round(t, 3), "players": [{"id": k, "x": v[0], "y": v[1]} for k, v in pos.items()],
            "ball": {"x": ball[0], "y": ball[1]}}


class ValueModelTests(unittest.TestCase):
    def test_xg_benchmarks_and_freeze_frame(self):
        xg = XGModel()
        spot = float(xg.location([94., 34.]));six = float(xg.location([100., 34.]));far = float(xg.location([80., 34.]))
        self.assertTrue(.15 < spot < .3 and six > spot > far and far < .06)
        self.assertLess(float(xg.location([100., 5.])), float(xg.location([100., 34.])))  # tight angle
        open_shot = xg.shot([94., 34.], np.array([[60., 10.]]))
        blocked = xg.shot([94., 34.], np.array([[97., 34.], [95., 34.2]]))
        self.assertGreater(open_shot["xg"], blocked["xg"]);self.assertEqual(blocked["blockers"], 2)

    def test_threat_increases_towards_goal(self):
        th = ThreatModel()
        self.assertLess(th.value([10., 34.]), th.value([52.5, 34.]))
        self.assertLess(th.value([52.5, 34.]), th.value([95., 34.]))
        self.assertGreater(th.value([95., 34.]), th.value([95., 3.]))

    def test_pitch_control_is_symmetric_logistic(self):
        self.assertAlmostEqual(float(pitch_control(np.array(1.), np.array(1.))), .5)
        self.assertGreater(float(pitch_control(np.array(1.), np.array(2.))), .95)

    def test_calibration_fallbacks(self):
        self.assertEqual(fit_xg([])["note"], "too few shots; prior kept")
        grid = fit_xt([((6, 10), (6, 14), True)] * 20, [((6, 15), 1), ((6, 15), 0)])
        self.assertEqual(grid.shape, (12, 16));self.assertTrue(np.all(grid >= 0))

    def test_local_coordinates_mirror_both_axes(self):
        self.assertEqual(to_local(np.array([100., 60.]), -1).tolist(), [5., 8.])


class TacticalEpisodeTests(unittest.TestCase):
    def run_frames(self, meta, frames, player="A03"):
        pipe = LivePipeline(meta);out = []
        for f in frames:
            out.append(pipe.update(f, Preferences(player=player)))
        return pipe, out

    def test_fullback_advance_then_counter_is_costed(self):
        meta, pos = base();frames = [];t = 0.
        pos["A03"] = [70., 8.]  # LB beyond halfway and the DM line + 6 m
        for _ in range(40):
            frames.append(frame(t, pos, pos["A06"]));t += .1
        lost = copy.deepcopy(pos)
        for _ in range(140):  # B wins it and attacks down A's vacated left
            lost["B07"] = [max(5., lost["B07"][0] - .6), 12.]
            frames.append(frame(t, lost, lost["B07"]));t += .1
        pipe, out = self.run_frames(meta, frames)
        states = [o["tactics"]["live"]["A03"]["state"] for o in out[:40]]
        self.assertIn("advanced", states)
        done = [e for o in out for e in o["tactics"]["completed"] if e["kind"] == "fullback_advance"]
        self.assertEqual(len(done), 1)
        self.assertTrue(done[0]["turnover_while_advanced"])
        self.assertTrue(done[0]["counter"]["through_vacated_flank"])
        self.assertIsNotNone(done[0]["mean_recovery_deficit_s"])
        # Individual figure: his own position value (vs an average LB) and the expected counter risk he added.
        own = done[0]["individual"]
        self.assertTrue(np.isfinite(own["position_value"]));self.assertGreaterEqual(own["cost_xg"], 0)
        self.assertAlmostEqual(own["net"], own["position_value"] - own["cost_xg"], places=4)
        # Realised counter xG stays in the shared team value, not in his individual cost.
        self.assertEqual(done[0]["value"]["cost_xg"], done[0]["counter"]["xg_against"])

    def test_striker_cost_only_for_a_clear_missed_chance(self):
        from team_player.analyser.live import LivePipeline
        meta, pos = base()
        pos["A09"] = [45., 34.]                          # striker dropped deep
        pos["A07"] = [96., 54.]                          # winger with the ball at the corner of the box
        for k in [k for k in pos if k.startswith("B") and k != "B01"]:
            pos[k] = [pos[k][0], 64. if pos[k][1] > 34 else 4.]   # defenders out wide: box open
        pipe = LivePipeline(meta)
        for n in range(25):
            pipe.update(frame(n * .1, pos, pos["A07"]), Preferences(player="A09"))
        tr = pipe.tactics;P = np.array([pos[i] for i in tr.ids]);B = np.array(pos["A07"])
        empty = tr.missed_chance(10, 0, P, B)
        self.assertGreaterEqual(empty, .15)               # nobody there to finish: a clear missed chance
        covered = dict(pos);covered["A10"] = [94., 34.]    # attacking midfielder already on the spot
        P2 = np.array([covered[i] for i in tr.ids])
        self.assertEqual(tr.missed_chance(10, 0, P2, B), 0.0)   # striker acting as a DM is fine

    def test_striker_drop_detected_only_after_possession_settles(self):
        meta, pos = base();frames = [];t = 0.
        pos["A09"] = [40., 30.]  # 21+ m short of B's last outfield defender
        for _ in range(50):
            frames.append(frame(t, pos, pos["A06"]));t += .1
        pipe, out = self.run_frames(meta, frames, "A09")
        states = [o["tactics"]["live"]["A09"]["state"] for o in out]
        first = states.index("dropping_deep")
        self.assertGreaterEqual(out[first]["frame"]["t"], 2.0)

    def test_decoy_run_draws_follower(self):
        meta, pos = base();frames = [];t = 0.
        for _ in range(25):
            frames.append(frame(t, pos, pos["A06"]));t += .1
        for _ in range(25):  # winger runs inside at 6 m/s, B's defender tracks him
            pos["A11"] = [pos["A11"][0] + .3, pos["A11"][1] + .5]
            pos["B02"] = [pos["B02"][0] - .05, pos["B02"][1] + .45]
            frames.append(frame(t, pos, pos["A06"]));t += .1
        for _ in range(60):
            frames.append(frame(t, pos, pos["A06"]));t += .1
        pipe, out = self.run_frames(meta, frames, "A11")
        runs = [e for o in out for e in o["tactics"]["completed"] if e["kind"] == "winger_run"]
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0]["decoy"]);self.assertIn("B02", runs[0]["drawn_defenders"])
        self.assertIsNotNone(runs[0]["space_created_m2"])

    def test_goal_inference_and_prefix_causality(self):
        meta, pos = base();frames = [];t = 0.
        pos["A09"] = [92., 34.];pos["B01"] = [104., 30.]
        for _ in range(20):
            frames.append(frame(t, pos, pos["A09"]));t += .1
        ball = np.array(pos["A09"], float)
        for _ in range(6):
            ball = np.minimum(ball + [2.5, 0.], [105., 68.]);frames.append(frame(t, pos, ball.tolist()));t += .1
        pipe, out = self.run_frames(meta, frames, "A09")
        events = [e["type"] for o in out for e in o["frame"]["events"]]
        self.assertIn("possible_shot", events);self.assertIn("possible_goal", events)
        self.assertEqual(out[-1]["tactics"]["score"]["A"], 1)
        # Same prefix, same output: nothing depends on later frames.
        _, short = self.run_frames(meta, frames[:22], "A09")
        self.assertEqual(json.dumps(short[-1]["tactics"]), json.dumps(out[21]["tactics"]))


class SituationTests(unittest.TestCase):
    def library(self):
        rng = np.random.default_rng(0);windows = []
        for i in range(120):
            ctx = {f: float(rng.normal(50 if "x" in f else 5, 5)) for f, _ in FEATURES}
            dropped = bool(i % 2)
            windows.append({"t": float(i), "t_start": float(i), "t_end": i + 5., "context": ctx,
                            "choices": {"striker_dropped": dropped, "striker_link_pass": False, "fb_advanced": i % 3 == 0, "winger_decoy": i % 4 == 0},
                            "xg": .2 if dropped else .05, "shots": 1, "goal": False, "max_xt": .1, "xt_gain": .02,
                            "final_third": True, "conceded_xg_12s": 0., "conceded_goal_12s": False, "lost_in_horizon": False,
                            "file": f"m{i % 6}.jsonl", "fingerprint": f"f{i % 6}", "team": "A", "opponent": "B",
                            "lineup": {"ST": "A09", "LB": "A03", "RB": "A02", "LW": "A11", "RW": "A07"}, "end_reason": "shot"})
        mean = np.mean([[w["context"][f] for f, _ in FEATURES] for w in windows], 0).tolist()
        return {"version": 1, "feature_stats": {"mean": mean, "std": [5.] * len(FEATURES)}, "windows": windows, "tendencies": {}}

    def test_query_splits_by_choice_and_excludes_active(self):
        lib = self.library();index = SituationIndex(lib, exclude_fingerprint="f0")
        self.assertEqual(len(index), 100)
        cur = lib["windows"][0]
        r = index.query(cur["context"], cur["lineup"], "A", "B", k=40, current=cur)
        sd = r["by_choice"]["striker_dropped"]
        self.assertGreater(sd["yes"]["xg"], sd["no"]["xg"]);self.assertAlmostEqual(sd["xg_difference"], .15, places=3)
        self.assertTrue(all(a["choices"]["striker_dropped"] for a in sd["closest_alternative"]))  # current did not drop
        self.assertTrue(all(n["file"] != "m0.jsonl" for n in r["neighbours"]))
        self.assertTrue(any("Associative" in w for w in r["warnings"]))

    def test_choice_flag_formats(self):
        self.assertTrue(choice_flags({"fb_advanced_R": True})["fb_advanced"])
        self.assertTrue(choice_flags({"striker_dropped": True, "fb_advanced": False, "winger_decoy": False})["striker_dropped"])


if __name__ == "__main__":
    unittest.main()
