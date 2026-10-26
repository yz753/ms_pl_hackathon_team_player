"""Reading own value (individual) and team outcome (shared) together, and how agents may say it."""
import asyncio, json, sys, types, unittest
from unittest.mock import patch
from team_player.agents.narration import balance_reading, check_grounding, local_summary, comparison_facts


class BalanceTests(unittest.TestCase):
    def test_four_readings(self):
        self.assertEqual(balance_reading("A09", -0.03, 0.25)["category"], "sacrifice")
        self.assertEqual(balance_reading("A02", 0.02, 0.5)["category"], "both_gain")
        self.assertEqual(balance_reading("A07", 0.01, -0.2)["category"], "own_gain_team_down")
        self.assertEqual(balance_reading("A11", -0.01, -0.2)["category"], "both_down")
        self.assertEqual(balance_reading("A11", 0.001, 0.01)["category"], "unclear")

    def test_sacrifice_is_praised_and_both_down_is_never_a_verdict(self):
        s = balance_reading("A09", -0.03, 0.25, role="ST")["text"]
        self.assertIn("team first", s);self.assertIn("Unselfish", s)
        d = balance_reading("A11", -0.01, -0.2)["text"]
        self.assertIn("can't see everything", d)

    def test_live_lines_are_short_and_keep_both_numbers(self):
        for role in ("ST", "LW", "LB", None):
            for own, team in ((-0.03, 0.25), (0.02, 0.5), (0.01, -0.2), (-0.01, -0.2), (0.001, 0.01)):
                b = balance_reading("A09", own, team, role=role)
                self.assertLessEqual(len(b["text"].split()), 32, b["text"])
                self.assertIn(f"{own:+.3f}", b["text"]);self.assertIn(f"{team:+.2f}", b["text"])
                self.assertIn("own value", b["text"]);self.assertIn("team outcome", b["text"].lower())

    def test_each_role_gets_its_own_definition(self):
        st = balance_reading("A09", -0.03, 0.25, role="ST")["facts"][-1]["text"]
        w = balance_reading("A11", -0.03, 0.25, role="LW")["facts"][-1]["text"]
        fb = balance_reading("A03", -0.03, 0.25, role="LB")["facts"][-1]["text"]
        self.assertIn("drops deep", st);self.assertIn("0.15 xG", st);self.assertNotIn("counter", st)
        self.assertIn("decoy", w);self.assertIn("no cost term", w);self.assertNotIn("missed", w)
        self.assertIn("counter-attack risk", fb);self.assertIn("separate figure", fb)
        self.assertNotIn("Unselfish", balance_reading("A11", -0.03, 0.25, role="LW")["text"])

    def test_agent_cannot_borrow_another_roles_figure(self):
        w = balance_reading("A11", -0.03, 0.25, role="LW")["facts"]
        self.assertTrue(check_grounding("A11 paid a missed chance cost: own value -0.030 xG.", w, "fan"))
        self.assertTrue(check_grounding("A11 sacrificed himself: own value -0.030 xG.", w, "fan"))
        st = balance_reading("A09", -0.03, 0.25, role="ST")["facts"]
        self.assertTrue(check_grounding("A09's counter risk: own value -0.030 xG.", st, "fan"))
        self.assertEqual(check_grounding("A09 left a missed chance: own value -0.030 xG.", st, "fan"), [])

    def test_every_reading_passes_the_agents_own_checks(self):
        for role in ("ST", "LW", "LB", None):
          for own, team in ((-0.03, 0.25), (0.02, 0.5), (0.01, -0.2), (-0.01, -0.2), (0.001, 0.01)):
            b = balance_reading("A09", own, team, role=role)
            self.assertEqual(check_grounding(b["text"], b["facts"], "analyst"), [], b["category"])

    def test_certain_negative_verdicts_are_rejected(self):
        facts = balance_reading("A11", -0.01, -0.2)["facts"]
        for text in ("Such movement created no benefit to the individual player nor the team.",
                     "His runs were useless today.", "The drop made no difference.", "It brought no real value at all.",
                     "The overlap did not help the team."):
            self.assertTrue(any("too certain" in p for p in check_grounding(text, facts, "fan")), text)

    def test_fullback_figures_say_defending_is_not_measured(self):
        for own, team in ((0.001, 0.01), (-0.01, -0.2), (0.01, -0.2), (-0.03, 0.25), (0.02, 0.5)):
            b = balance_reading("A03", own, team, role="LB")
            self.assertTrue(any(f["id"] == "balance_definition_fullback" for f in b["facts"]))
            self.assertEqual(check_grounding(b["text"], b["facts"], "analyst"), [], b["category"])
            self.assertNotIn("defending", b["text"].lower())        # shown as its own "without the ball" line
        self.assertNotIn("defending", balance_reading("A09", 0.001, 0.01, role="ST")["text"])
        facts = balance_reading("A03", 0.001, 0.01, role="LB")["facts"]
        for text in ("A03 was passive today.", "A03 contributed little.", "He is a poor defender on this evidence."):
            self.assertTrue(any("passive or a poor defender" in p for p in check_grounding(text, facts, "fan")), text)
        self.assertEqual(check_grounding("A09 was passive in the build-up.", balance_reading("A09", 0.001, 0.01)["facts"], "fan"), [])

    def test_comparison_passes_role_for_fullbacks(self):
        from team_player.analyser.comparison import compare, FIELDS
        row = lambda pid: {"id": pid, "team": pid[0], "role": "LB", **dict.fromkeys(FIELDS, 0.), "observed_s": 600.,
                           "possession_s": 300., "indiv_net": 0.0002, "value_net": 0.004, "value_n": 2}
        c = compare({"A03": row("A03"), "B03": row("B03")}, "A03", "B03", 0)
        self.assertTrue(any(f["id"].endswith("balance_definition_fullback") for f in c["left"]["balance"]["facts"]))
        self.assertTrue(any("passive" in p or "defending" in p for p in check_grounding("A03 was passive.", comparison_facts(c), "fan")))

    def test_comparison_carries_both_readings(self):
        from team_player.analyser.comparison import compare, FIELDS
        row = lambda pid, own, team: {"id": pid, "team": pid[0], "role": "ST", **dict.fromkeys(FIELDS, 0.), "observed_s": 600.,
                                      "possession_s": 300., "indiv_net": own, "value_net": team, "value_n": 4}
        rows = {"A09": row("A09", -0.015, 0.12), "B09": row("B09", 0.01, 0.2)}
        c = compare(rows, "A09", "B09", 0)
        self.assertEqual(c["left"]["balance"]["category"], "sacrifice");self.assertEqual(c["right"]["balance"]["category"], "both_gain")
        self.assertTrue(any(a["id"] == "A09_balance" for a in c["advice"]))
        text = local_summary("comparison", c, "fan")["text"]
        self.assertIn("team first", text);self.assertIn("paying off", text)
        self.assertIn("(history)", text)


class NarratorBalanceTests(unittest.TestCase):
    def test_narrator_reads_balance_and_cannot_overclaim(self):
        from team_player.agents.foundry import FoundryDirector
        bal = balance_reading("A11", -0.01, -0.2)
        snap = {"t": 30., "audience": "fan", "verdicts": [], "balance": bal}
        card = {"id": "c1", "title": "A run", "fan": "A11 ran 15 m.", "analyst": "Peak 6.1 m/s.", "caveat": "Association only."}
        fake = types.ModuleType("agent_framework");fake.tool = lambda **kw: (lambda f: f);seen = {}
        class Agent:
            def __init__(self, **kw):self.tools, seen["instructions"] = kw["tools"], kw["instructions"]
            async def run(self, *a):
                seen["facts"] = json.loads(self.tools[0]())["facts"]
                seen["r1"] = self.tools[1]("Wasted run", "A11's 15 m run created no benefit for him nor the team.")
                seen["r2"] = self.tools[1]("A run to watch", "A11 ran 15 m; the figures (-0.010 and -0.20) did not pick up a gain, and tracking cannot see everything.")
        fake.Agent = Agent
        d = FoundryDirector.__new__(FoundryDirector);d.client = object()
        with patch.dict(sys.modules, {"agent_framework": fake}):
            out = asyncio.run(d.narrate("overlay", snap, "fan", [card]))
        self.assertTrue(any(f["id"] == "balance" for f in seen["facts"]))
        self.assertIn("unselfish, team-first", seen["instructions"])
        self.assertTrue(seen["r1"].startswith("Rejected"));self.assertEqual(seen["r2"], "Accepted. Stop.")
        self.assertEqual(out["method"], "microsoft_foundry_agent")


if __name__ == "__main__":
    unittest.main()
