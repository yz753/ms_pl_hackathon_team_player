from dataclasses import replace
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from team_player.analyser.analysis import Analyzer, AnalysisConfig, arrival_times
from team_player.app.replay import create_replay
from team_player.schema import validate_frame
from team_player.simulator.engine import Simulator, SimulationConfig


def analyze_frames(metadata, frames, config=None):
    analyzer = Analyzer(metadata, config)
    return [result for frame in frames if (result := analyzer.update(frame)) is not None]


class PipelineTests(unittest.TestCase):
    def test_reproducible_and_seed_sensitive(self):
        config = SimulationConfig(duration=8)
        a = list(Simulator(config).frames())
        self.assertEqual(a, list(Simulator(config).frames()))
        self.assertNotEqual(a, list(Simulator(replace(config, seed=2)).frames()))

    def test_identical_pre_intervention_and_ordinary_control(self):
        for scenario in ("drop_deep", "overlap", "decoy", "ordinary"):
            config = SimulationConfig(scenario=scenario, duration=30)
            sim = Simulator(config)
            a = list(sim.frames())
            b = list(Simulator(replace(config, intervention=False)).frames())
            self.assertEqual([f for f in a if f["t"] < config.start], [f for f in b if f["t"] < config.start])
            if scenario == "ordinary":
                self.assertEqual(a, b)
            elif any(v["states"][sim.focus] in ("drop", "overlap", "decoy") for v in sim.private):
                self.assertTrue(a != b, "An activated special routine must change the paired trajectory")

    def test_physics_and_schema(self):
        for scenario in ("drop_deep", "overlap", "decoy"):
            for seed in (3, 8):
                sim = Simulator(SimulationConfig(seed=seed, scenario=scenario, duration=30))
                frames = list(sim.frames())
                previous = None
                for frame in frames:
                    validate_frame(frame, sim.metadata(), previous)
                    previous = frame["t"]
                positions = np.array([[[p["x"], p["y"]] for p in f["players"]] for f in frames])
                speed = np.linalg.norm(np.diff(positions, axis=0) / sim.dt, axis=2)
                self.assertTrue(np.all(speed <= sim.speeds + .002))
                acceleration = np.linalg.norm(np.diff(positions, n=2, axis=0) / sim.dt**2, axis=2)
                self.assertLessEqual(acceleration.max(), sim.acceleration + .01)
                ball = np.array([[f["ball"]["x"], f["ball"]["y"]] for f in frames])
                self.assertLessEqual(np.linalg.norm(np.diff(ball, axis=0) / sim.dt, axis=1).max(), 28.002)

    def test_public_stream_has_no_hidden_labels(self):
        sim = Simulator(SimulationConfig(duration=6))
        serialized = json.dumps([sim.metadata(), *sim.frames()])
        for hidden in ("scenario", "variant", "targets", "intervention", "focus", "marking_target", "seed", "styles", "drop", "return_urgency", "states"):
            self.assertNotIn(f'"{hidden}"', serialized)

    def test_analysis_does_not_import_simulator(self):
        source = "import sys; from team_player.analyser.analysis import Analyzer; import team_player.analyser.live, team_player.agents.foundry; assert not any(m.startswith('team_player.simulator') for m in sys.modules)"
        subprocess.run([sys.executable, "-c", source], check=True)

    def test_prefix_causality_and_player_order_invariance(self):
        sim = Simulator(SimulationConfig(duration=10))
        frames = list(sim.frames())
        full = analyze_frames(sim.metadata(), frames)
        prefix = analyze_frames(sim.metadata(), frames[:61])
        self.assertEqual(prefix, [r for r in full if r["t"] <= frames[60]["t"]])
        reordered = [{**f, "players": list(reversed(f["players"]))} for f in frames]
        self.assertEqual(full, analyze_frames(sim.metadata(), reordered))

    def test_invalid_time_rejected(self):
        sim = Simulator(SimulationConfig(duration=1))
        frame = next(sim.frames())
        analyzer = Analyzer(sim.metadata())
        analyzer.update(frame)
        with self.assertRaises(ValueError):
            analyzer.update(frame)

    def test_arrival_times_geometry(self):
        cfg = AnalysisConfig()
        points = np.array([[0, 0], [10, 0], [20, 0]])
        stationary = arrival_times(np.array([[0, 0]]), np.array([[0, 0]]), points, cfg)[0]
        approaching = arrival_times(np.array([[0, 0]]), np.array([[4, 0]]), points, cfg)[0]
        retreating = arrival_times(np.array([[0, 0]]), np.array([[-4, 0]]), points, cfg)[0]
        self.assertEqual(stationary[0], 0)
        self.assertTrue(np.all(np.diff(stationary) > 0))
        self.assertTrue(np.all(approaching[1:] < stationary[1:]))
        self.assertTrue(np.all(retreating[1:] > stationary[1:]))

    def test_offside_both_directions(self):
        sim = Simulator(SimulationConfig(duration=1))
        sim.pos[:11, 0] = 25
        sim.pos[11:, 0] = 80
        sim.pos[10, 0] = 90
        self.assertTrue(sim.offside(10, np.array([70, 34])))
        sim.pos[10, 0] = 75
        self.assertFalse(sim.offside(10, np.array([70, 34])))
        sim.pos[21, 0] = 15
        self.assertTrue(sim.offside(21, np.array([35, 34])))
        sim.pos[21, 0] = 30
        self.assertFalse(sim.offside(21, np.array([35, 34])))

    def test_following_detector_positive_and_shared_shift_control(self):
        sim = Simulator(SimulationConfig(duration=1))
        meta = sim.metadata()
        base = sim.pos.copy()
        base[9] = [60, 48]
        base[13] = [63, 48]
        def run(shared):
            analyzer = Analyzer(meta)
            output = []
            for step in range(51):
                t = step / 10
                pos = base.copy()
                pos[9, 1] += 3 * t
                if shared:
                    pos[12:, 1] += 3 * max(0, t - .3)
                else:
                    pos[13, 1] += 3 * max(0, t - .3)
                pos = np.clip(pos, [0, 0], [105, 68])
                frame = {"kind": "frame", "t": t, "players": [dict(id=pid, x=float(p[0]), y=float(p[1])) for pid, p in zip(sim.ids, pos)],
                         "ball": {"x": 40., "y": 34., "owner": "A06"}, "possession": "A", "events": [], "score": [0, 0]}
                result = analyzer.update(frame)
                if result:
                    output.extend(result["following"])
            return output
        self.assertTrue(any(f["runner"] == "A07" and f["defender"] == "B05" for f in run(False)))
        self.assertFalse(any(f["runner"] == "A07" for f in run(True)))

    def test_analysis_output_finite(self):
        sim = Simulator(SimulationConfig(duration=10))
        frames = list(sim.frames())
        results = analyze_frames(sim.metadata(), frames)
        json.dumps(results, allow_nan=False)
        self.assertEqual(len(results), int(frames[-1]["t"] * 2) + 1)
        self.assertEqual(results[0]["changes_over_2s"], {})

    def test_replay_contains_public_data_only(self):
        sim = Simulator(SimulationConfig(duration=1))
        frames = list(sim.frames())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "replay.html"
            create_replay(sim.metadata(), frames, analyze_frames(sim.metadata(), frames), path)
            text = path.read_text()
            self.assertNotIn("__DATA__", text)
            self.assertNotIn('"active_intervention"', text)
            self.assertIn("<!doctype html>", text)


if __name__ == "__main__":
    unittest.main()
