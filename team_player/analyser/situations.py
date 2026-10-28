"""Similar past situations: "what happened when they chose otherwise?"

Unit: one DECISION WINDOW. After 1.5 s of control, and then every 8 s, the tracker freezes
the pre-decision context (ball position, opponent block height/compactness/width, numbers
goal-side, how the ball was won, score, minute). It reads the CHOICES made in the next 8 s
(striker dropped? fullback advanced? winger decoy run?) and the OUTCOMES over the next 15 s
or until the ball is lost (xG, goal, max xT, final third, xG conceded after a loss).

The index holds every decision window of the history seasons (built by
`analyser/history.py` into history.db). A query returns the k nearest windows split by
choice, with n, kernel-weighted xG, bootstrap intervals, and the closest moments where
the other choice was made. This is matching on observed context: associative, not a
counterfactual replay.
"""
from __future__ import annotations
import numpy as np

from team_player.config.analysis import (SIMILARITY_FEATURES as FEATURES, CHOICES, SIMILARITY_K, SAME_TEAM_BONUS,
                                         SAME_OPPONENT_BONUS, MIN_SIDE_N)

OUTCOMES = ("xg", "goal", "shots", "max_xt", "xt_gain", "final_third", "conceded_xg_12s", "conceded_goal_12s")
CHOICE_KEYS = tuple(CHOICES)


def choice_flags(choices):
    if "fb_advanced" in choices:  # decision-window format
        return {k: bool(choices.get(k)) for k in CHOICES}
    return {"striker_dropped": bool(choices.get("striker_dropped")),
            "fb_advanced": bool(choices.get("fb_advanced_L") or choices.get("fb_advanced_R")),
            "winger_decoy": choices.get("winger_decoy_runs", 0) > 0}


def table_from_windows(windows):
    """Column arrays from a list of window dicts (small libraries and tests)."""
    return {"id": np.arange(len(windows)), "team": np.array([w["team"] for w in windows]),
            "opp": np.array([w["opponent"] for w in windows]),
            "X": np.array([[float(w["context"][f]) for f, _ in FEATURES] for w in windows]).reshape(len(windows), len(FEATURES)),
            "choices": np.array([[choice_flags(w["choices"])[k] for k in CHOICE_KEYS] for w in windows], bool).reshape(len(windows), 3),
            "xg": np.array([w["xg"] for w in windows], float), "goal": np.array([w["goal"] for w in windows], float),
            "final_third": np.array([w["final_third"] for w in windows], float),
            "conceded": np.array([w.get("conceded_xg_12s", 0) for w in windows], float),
            "max_xt": np.array([w["max_xt"] for w in windows], float),
            "fingerprint": np.array([w.get("fingerprint", "") for w in windows])}


class SituationIndex:
    """Nearest-neighbour search over decision windows held as numpy columns.

    Build from history.db (`HistoryStore.index()`), or from a dict/list of window records
    (`SituationIndex({"windows": [...]})`). `detail(id)` returns the full record of a window
    for the narrative (season, teams, time, what each player did, the result)."""

    def __init__(self, library=None, exclude_fingerprint=None, *, table=None, detail=None):
        if table is None:
            windows = library["windows"] if isinstance(library, dict) else list(library or [])
            table = table_from_windows(windows);detail = detail or (lambda i: windows[int(i)])
        keep = np.ones(len(table["id"]), bool)
        if exclude_fingerprint is not None and "fingerprint" in table:
            keep = table["fingerprint"] != exclude_fingerprint   # never match the live recording with itself
        self.t = {k: v[keep] for k, v in table.items()}
        self.detail = detail
        X = self.t["X"]
        self.mean = X.mean(0) if len(X) else np.zeros(len(FEATURES))
        self.std = np.maximum(X.std(0), 1e-6) if len(X) else np.ones(len(FEATURES))
        self.w = np.array([w for _, w in FEATURES]);self.Z = (X - self.mean) / self.std

    def __len__(self):
        return len(self.t["id"])

    def query(self, context, lineup=None, team=None, opponent=None, k=SIMILARITY_K, current=None,
              same_team_bonus=SAME_TEAM_BONUS, same_opponent_bonus=SAME_OPPONENT_BONUS, rng_seed=0, exclude_team=None):
        """k nearest windows and outcome summaries split by each choice."""
        if not len(self):
            return {"available": False, "warnings": ["No history matches indexed yet. Build history.db from the history seasons."]}
        q = (np.array([float(context[f]) for f, _ in FEATURES]) - self.mean) / self.std
        d = np.sqrt(np.sum(self.w * (self.Z - q) ** 2, axis=1))
        if team:
            d = d - same_team_bonus * (self.t["team"] == team)
        if opponent:
            d = d - same_opponent_bonus * (self.t["opp"] == opponent)
        eligible = np.flatnonzero(self.t['team'] != exclude_team) if exclude_team else np.arange(len(self))
        if not len(eligible):
            return {'available': False, 'warnings': ['No historical situations from a different focus team are available.']}
        k = min(k, len(eligible));order = eligible[np.argsort(d[eligible])[:k]];dk = d[order]
        h = max(float(np.median(dk)), 1e-6)
        log_weights = -(dk / h) ** 2 / 2  # Keep Gaussian weights in log space until each group is normalised.
        rng = np.random.default_rng(rng_seed)
        C = self.t["choices"][order]
        flags_now = choice_flags(current["choices"]) if current else None
        by_choice = {}
        for c, (key, label) in enumerate(CHOICES.items()):
            groups = {("yes" if flag else "no"): self.summarise(order[C[:, c] == flag], log_weights[C[:, c] == flag], rng, log_weights=True)
                      for flag in (True, False)}
            diff = round(groups["yes"]["xg"] - groups["no"]["xg"], 4) if min(groups["yes"]["n"], groups["no"]["n"]) >= MIN_SIDE_N else None
            entry = {"label": label, **groups, "xg_difference": diff}
            # Closest examples of each choice (for the "Similar situation: ..." sentence).
            entry["closest_yes"] = [self.brief(order[j], dk[j]) for j in np.flatnonzero(C[:, c])[:2]]
            entry["closest_no"] = [self.brief(order[j], dk[j]) for j in np.flatnonzero(~C[:, c])[:2]]
            if flags_now is not None:
                entry["closest_alternative"] = entry["closest_no"] if flags_now[key] else entry["closest_yes"]
            by_choice[key] = entry
        warnings = ["Associative: neighbours share the measured context, not every cause. Do not read differences as the effect of a choice."]
        if k < 20:
            warnings.append(f"Only {k} history moments available; treat as anecdotes.")
        for v in by_choice.values():
            if v["xg_difference"] is None:
                warnings.append(f"{v['label']}: fewer than {MIN_SIDE_N} neighbours on one side; no comparison shown.")
        return {"available": True, "k": k, "pool_size": len(eligible), "excluded_team": exclude_team, "bandwidth": round(h, 3),
                "neighbours": [self.brief(order[j], dk[j]) for j in range(min(5, k))],
                "by_choice": by_choice, "current": self.brief_record(current) if current else None,
                "warnings": warnings, "advice": self.advice(by_choice, current)}

    def summarise(self, rows, weights, rng, boots=200, *, log_weights=False):
        n = len(rows)
        if not n:
            return {"n": 0, "n_eff": 0, "xg": None, "goal_rate": None, "conceded_xg": None, "ci90_xg": None,
                    "final_third_rate": None, "max_xt": None}
        w = np.asarray(weights, float)
        if log_weights:
            # Subtracting a common log weight preserves relative probabilities.
            # Do this per choice group, including groups far from the closest match.
            w = np.exp(w - np.max(w))
        w = w / w.sum();t = self.t
        xg = t["xg"][rows]
        boot = [float(xg[s].mean()) for s in (rng.choice(n, n, p=w) for _ in range(boots))]
        return {"n": n, "n_eff": round(float(1 / np.sum(w ** 2)), 1), "xg": round(float(w @ xg), 4),
                "goal_rate": round(float(w @ t["goal"][rows]), 3), "conceded_xg": round(float(w @ t["conceded"][rows]), 4),
                "final_third_rate": round(float(w @ t["final_third"][rows]), 3), "max_xt": round(float(w @ t["max_xt"][rows]), 4),
                "ci90_xg": [round(float(np.percentile(boot, 5)), 4), round(float(np.percentile(boot, 95)), 4)]}

    def brief(self, row, dist=None):
        return self.brief_record(self.detail(self.t["id"][row]), dist)

    @staticmethod
    def brief_record(p, dist=None):
        if p is None:
            return None
        keys = ("season", "matchweek", "home", "away", "kickoff", "fixture_id", "clock", "actions", "outcome_text", "file")
        return {**{k: p.get(k) for k in keys}, "t_start": p.get("t_start", p.get("t")), "t_end": p.get("t_end"),
                "team": p["team"], "opponent": p["opponent"], "choices": choice_flags(p["choices"]),
                "outcome": {k: p.get(k) for k in OUTCOMES}, "end_reason": p.get("end_reason"),
                "distance": None if dist is None else round(float(dist), 3)}

    @staticmethod
    def advice(by_choice, current):
        out = [{"id": "associative", "text": "Similar past moments show what tended to follow each choice; they are not a replay of what would have happened here."}]
        for key, v in by_choice.items():
            if v["xg_difference"] is None:
                continue
            y, n = v["yes"], v["no"]
            out.append({"id": key, "text": f"{v['label']}: {y['xg']:.3f} xG in the next 15 s (n={y['n']}, 90% CI {y['ci90_xg'][0]:.3f}-{y['ci90_xg'][1]:.3f}), "
                        f"final third reached {100*y['final_third_rate']:.0f}%, versus {n['xg']:.3f} (n={n['n']}, {n['ci90_xg'][0]:.3f}-{n['ci90_xg'][1]:.3f}), {100*n['final_third_rate']:.0f}% when not. "
                        f"xG conceded after losing the ball: {y['conceded_xg']:.3f} vs {n['conceded_xg']:.3f}."})
        if current:
            out.append({"id": "current", "text": f"This moment: {current['xg']:.3f} xG in the next 15 s"
                        + (", goal" if current["goal"] else "") + f", ended by {str(current.get('end_reason', '')).replace('_', ' ')}."})
        return out


class LiveSimilarity:
    """Online wrapper: re-query when a new decision window opens, and look back when a
    possession of THIS match with a shot completes."""

    def __init__(self, index, k=SIMILARITY_K):
        self.index, self.k, self.last = index, k, None

    def update(self, tactics):
        for e in tactics.get("completed", ()):
            if e.get("kind") == "possession" and e.get("shots") and e.get("windows"):
                shot_windows = [w for w in e["windows"] if w["shots"]]
                w = shot_windows[0] if shot_windows else e["windows"][-1]
                cur = {**w, "team": e["team"], "opponent": e["opponent"], "lineup": e["lineup"], "t_start": w["t"],
                       "t_end": e["t_end"], "end_reason": e["end_reason"]}
                self.last = ("retro", e["t_start"])
                return {"mode": "retrospective", "query_t": w["t"], "team": e["team"], "current_choices": choice_flags(w["choices"]),
                        **self.index.query(w["context"], e["lineup"], e["team"], e["opponent"], self.k, current=cur)}
        p = tactics.get("possession")
        if p and p.get("window") and self.last != ("live", p["window"]["t"]):
            self.last = ("live", p["window"]["t"])
            return {"mode": "live", "query_t": p["window"]["t"], "team": p["team"], "current_choices": choice_flags(p["choices"]),
                    **self.index.query(p["window"]["context"], p["lineup"], p["team"], p["opponent"], self.k)}
        return None
