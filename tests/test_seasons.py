"""Seasons, history store, similar-situation stories and data source."""
import collections
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from team_player.simulator.season import fixtures, write_fixture, relative_path
from team_player.config.league import TEAMS, CURRENT_SEASON, HISTORY_SEASONS


class FixtureTests(unittest.TestCase):
    def test_premier_league_format(self):
        F = fixtures(HISTORY_SEASONS[0])
        self.assertEqual(len(F), 380)
        games = collections.Counter();home = collections.Counter();pairs = collections.Counter();weeks = collections.defaultdict(set)
        for f in F:
            games[f["home"]] += 1;games[f["away"]] += 1;home[f["home"]] += 1;pairs[(f["home"], f["away"])] += 1
            weeks[f["matchweek"]] |= {f["home"], f["away"]}
        self.assertEqual(set(games.values()), {38});self.assertEqual(set(home.values()), {19})
        self.assertEqual(len(pairs), 20 * 19);self.assertEqual(max(pairs.values()), 1)      # each ordered pair once
        self.assertTrue(all(len(v) == len(TEAMS) for v in weeks.values()))                  # everyone plays every week
        self.assertEqual(F, fixtures(HISTORY_SEASONS[0]))                                    # reproducible
        self.assertNotEqual([f["seed"] for f in F], [f["seed"] for f in fixtures(HISTORY_SEASONS[1])])

    def test_match_file_has_season_metadata_and_is_reproducible(self):
        self.assertEqual(CURRENT_SEASON, "2026-27")
        self.assertNotIn(CURRENT_SEASON, HISTORY_SEASONS)
        fx = fixtures(CURRENT_SEASON)[0]
        with tempfile.TemporaryDirectory() as d:
            write_fixture(fx, Path(d) / "a", duration=2);write_fixture(fx, Path(d) / "b", duration=2)
            a, b = Path(d) / "a" / relative_path(fx), Path(d) / "b" / relative_path(fx)
            self.assertTrue(str(relative_path(fx)).startswith("current"))
            with gzip.open(a, "rt") as h:
                meta = json.loads(next(h));frame = json.loads(next(h))
            self.assertEqual((meta["season"], meta["matchweek"], meta["home"], meta["away"]), (fx["season"], 1, fx["home"], fx["away"]))
            self.assertEqual(set(frame), {"kind", "t", "players", "ball"})                    # positions only
            self.assertEqual(gzip.decompress(a.read_bytes()), gzip.decompress(b.read_bytes()))


class HistoryStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from team_player.analyser.history import analyse_folder, build_store, HistoryStore
        cls.tmp = tempfile.TemporaryDirectory();root = Path(cls.tmp.name)
        for fx in fixtures(HISTORY_SEASONS[0])[:2]:
            write_fixture(fx, root / "data", duration=90)
        analyse_folder(root / "data" / "history", root / "analysis")
        build_store(root / "analysis", root / "history.db");cls.store = HistoryStore(root / "history.db")

    @classmethod
    def tearDownClass(cls):
        cls.store.con.close();cls.tmp.cleanup()

    def test_store_summary_player_history_and_index(self):
        s = self.store.summary();self.assertEqual(s["matches"], 2);self.assertEqual(s["seasons"], [HISTORY_SEASONS[0]])
        home = fixtures(HISTORY_SEASONS[0])[0]["home"]
        h = self.store.player_history(f"{home}09");self.assertEqual(h["matches"], 1);self.assertEqual(h["role"], "ST")
        self.assertGreater(h["totals"]["observed_s"], 0)
        self.assertEqual(len(self.store.index()), s["windows"])

    def test_similar_story_mentions_season_match_time_player_and_result(self):
        from team_player.agents.narration import similar_story
        if not self.store.summary()["windows"]:
            self.skipTest("short matches produced no decision windows")
        w = self.store.window_detail(1)
        context = {k[2:]: w[k] for k in w if k.startswith("f_")}
        r = self.store.index().query(context, None, w["team"], w["opponent"], k=10, current=w)
        r.update(mode="live", team=w["team"], current_choices=w["choices"])
        story = similar_story(r, w["lineup"]["ST"], "ST", w["team"])
        self.assertIsNotNone(story)
        self.assertIn(HISTORY_SEASONS[0], story["text"]);self.assertIn("matchweek", story["text"]);self.assertIn("result:", story["text"])
        self.assertIsNone(similar_story(r, "X05", "LDM", w["team"]))       # unsupported role: nothing to say


class DataSourceTests(unittest.TestCase):
    def test_lists_current_season_and_rejects_unsafe_names(self):
        from team_player.app.storage import DataSource
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "current" / CURRENT_SEASON;folder.mkdir(parents=True)
            (folder / "mw02_B-A.jsonl.gz").write_bytes(b"");(folder / "mw01_C-D.jsonl.gz").write_bytes(b"")
            with patch.dict(os.environ, {"TEAM_PLAYER_DATA": d, "TEAM_PLAYER_BLOB_ACCOUNT_URL": "", "TEAM_PLAYER_HISTORY_DB": ""}):
                src = DataSource()
                self.assertEqual([m["label"] for m in src.list_current()], ["MW01 · C v D", "MW02 · B v A"])
                self.assertIsNone(src.history_db())
                for bad in ("../x.jsonl.gz", "a/b.jsonl.gz", "x.txt"):
                    with self.assertRaises(ValueError):
                        src.match_path(bad)


if __name__ == "__main__":
    unittest.main()
