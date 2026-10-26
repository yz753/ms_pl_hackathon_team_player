"""Causal streaming analysis using ONLY public tracking observations.

All metrics are geometric heuristics, not calibrated probabilities. No import of
the simulator, scenario metadata, or future frames is permitted here.
"""
from __future__ import annotations

from collections import deque
import math
import os
import numpy as np

from team_player.schema import LENGTH, WIDTH, validate_metadata, validate_frame
from team_player.config.analysis import AnalysisConfig
from team_player.analyser.models import ValueModels, pitch_control, transition_weights, obso
from team_player.utils.geometry import arrival_times, to_local


def role_region(role, attacking, ball_x):
    """Independent broad role bands, NOT the generator's formation anchors.

    Returns [xmin, xmax, ymin, ymax] in team-relative coordinates. Deliberately
    tolerant: tactical excursion detection is a reference-template heuristic.
    """
    if role == "GK":
        return np.array([0, 18, 20, 48], float)
    if "CB" in role:
        x = [12, 48] if attacking else [8, 42]
    elif role in ("LB", "RB"):
        x = [18, 62] if attacking else [8, 46]
    elif "DM" in role:
        x = [25, 65] if attacking else [20, 55]
    elif "ST" in role:
        x = [57, 103] if attacking else [37, 88]
    elif role in ("LW", "RW", "LM", "RM"):
        x = [43, 96] if attacking else [22, 75]
    else:
        x = [35, 83] if attacking else [24, 67]
    shift = .18 * (ball_x - 52.5)
    y = [0, 68]
    if role in ("LB", "LW", "LM"):
        y = [0, 38]
    elif role in ("RB", "RW", "RM"):
        y = [30, 68]
    return np.array([max(0, x[0] + shift), min(105, x[1] + shift), *y])


class Analyzer:
    def __init__(self, metadata, config=None):
        validate_metadata(metadata)
        self.meta = metadata
        self.cfg = config or AnalysisConfig()
        self.cfg.validate()
        self.ids = [p["id"] for p in metadata["players"]]
        self.index = {pid: i for i, pid in enumerate(self.ids)}
        self.team_names = [t["id"] for t in metadata["teams"]]
        self.teams = np.array([self.team_names.index(p["team"]) for p in metadata["players"]])
        self.roles = [p["role"] for p in metadata["players"]]
        self.directions = np.array([t["attack_direction"] for t in metadata["teams"]])
        nx, ny = math.ceil(LENGTH / self.cfg.grid_step), math.ceil(WIDTH / self.cfg.grid_step)
        xs = (np.arange(nx) + .5) * LENGTH / nx
        ys = (np.arange(ny) + .5) * WIDTH / ny
        xx, yy = np.meshgrid(xs, ys)
        self.grid = np.c_[xx.ravel(), yy.ravel()]
        self.cell_area = LENGTH * WIDTH / len(self.grid)
        self.models = ValueModels.load(self.cfg.value_models_path or os.environ.get("TEAM_PLAYER_VALUE_MODELS"))
        # Each team's view of the grid (attacking towards x=105) and its xT value.
        self.grid_local = [to_local(self.grid, self.directions[t]) for t in (0, 1)]
        self.threat_local = [self.models.threat.value(self.grid_local[t]) for t in (0, 1)]
        self.fields = None
        self.history = deque()
        self.metric_history = deque()
        self.previous_time = None
        self.previous_positions = None
        self.velocities = np.zeros((22, 2))
        self.last_update = -np.inf
        self.excursion_since = {}
        self.follow_since = {}
        self.last_possession = None
        self.phase_since = None

    def space_value(self, positions, velocities, team, ball, exclude=(), travel=None):
        """OBSO-style value of the space `team` owns: sum P(ball goes there) * P(control) * xT.

        `exclude` removes players from the attacking side (leave-one-out / teammates-only
        views). Positions may be counterfactual (e.g. defenders restored to where they
        were before a decoy run). Pure geometry on the supplied frame.
        """
        if travel is None:
            travel = arrival_times(positions, velocities, self.grid, self.cfg)
        return self.space_terms(positions, velocities, team, ball, exclude, travel)[0]

    def space_terms(self, positions, velocities, team, ball, exclude=(), travel=None):
        """(OBSO value, m² of the attacking half the team controls with P > 0.5)."""
        if travel is None:
            travel = arrival_times(positions, velocities, self.grid, self.cfg)
        own = [i for i in np.flatnonzero(self.teams == team) if i not in set(exclude)]
        opp = np.flatnonzero(self.teams != team)
        pc = pitch_control(travel[own].min(axis=0), travel[opp].min(axis=0))
        half = self.grid_local[team][:, 0] > 52.5
        return (obso(pc, transition_weights(self.grid, ball), self.threat_local[team]),
                float(np.sum((pc > .5) & half) * self.cell_area))

    def local_x(self, x, team):
        return x if self.directions[team] == 1 else LENGTH - x

    def pass_options(self, positions, velocity, ball, team):
        """Lane estimates exclude offside receivers. Margins are seconds, not probabilities."""
        defenders = np.flatnonzero(self.teams != team)
        direction = self.directions[team]
        defensive_line = np.sort(positions[defenders, 0] * direction)[-2]
        options = {}
        for i in np.flatnonzero(self.teams == team):
            vector = positions[i] - ball
            distance = np.linalg.norm(vector)
            if distance < 3 or distance > 45:
                continue
            local_x = self.local_x(positions[i, 0], team)
            offside = local_x > 52.5 and positions[i, 0] * direction > max(defensive_line, ball[0] * direction)
            fractions = np.linspace(.12, 1, max(5, math.ceil(distance / 2)))
            points = ball + fractions[:, None] * vector
            opposition = arrival_times(positions[defenders], velocity[defenders], points, self.cfg).min(axis=0)
            travel = distance * fractions / self.cfg.pass_speed
            margin = float(np.min(opposition - travel))
            options[self.ids[i]] = {"interception_margin_s": round(margin, 3),
                                   "forward_m": round(float(direction * vector[0]), 3),
                                   "offside": bool(offside), "accessible": bool(margin > .15 and not offside)}
        return options

    def following_evidence(self, positions, ball, owner, possession, time):
        if not self.history or time - self.history[0][0] < 2:
            return []
        # Follow relationships need an unbroken possession window.
        if self.phase_since is None or time - self.phase_since < 2:
            return []
        old = min(self.history, key=lambda h: abs(h[0] - (time - 2)))
        middle = min(self.history, key=lambda h: abs(h[0] - (time - 1)))
        before, mid = old[1], middle[1]
        results = []
        eligible = set()
        for team in (0, 1):
            if possession != self.team_names[team]:
                continue
            attackers = np.flatnonzero((self.teams == team) & (np.array(self.roles) != "GK"))
            defenders = np.flatnonzero((self.teams != team) & (np.array(self.roles) != "GK"))
            shared = np.median(positions[defenders] - before[defenders], axis=0)
            for runner in attackers:
                # Exclude anyone who possessed the ball in the entire trailing window.
                if any(h[3] == self.ids[runner] for h in self.history if h[0] >= old[0]):
                    continue
                run = positions[runner] - before[runner]
                run_length = np.linalg.norm(run)
                if run_length < 5:
                    continue
                for defender in defenders:
                    movement = positions[defender] - before[defender] - shared
                    length = np.linalg.norm(movement)
                    cosine = float(np.dot(run, movement) / max(run_length * length, 1e-9))
                    d0 = np.linalg.norm(before[runner] - before[defender])
                    d1 = np.linalg.norm(positions[runner] - positions[defender])
                    lag_run = mid[runner] - before[runner]
                    response = positions[defender] - mid[defender] - shared / 2
                    lag_cos = float(np.dot(lag_run, response) / max(np.linalg.norm(lag_run) * np.linalg.norm(response), 1e-9))
                    ball_move = ball - old[2]
                    ball_cos = float(np.dot(movement, ball_move) / max(length * np.linalg.norm(ball_move), 1e-9))
                    ball_confounded = np.linalg.norm(ball_move) > 3 and ball_cos > .85
                    evidence = length > 1.2 and cosine > .6 and lag_cos > .3 and d1 < 14 and d1 <= d0 + 3
                    key = (runner, defender)
                    if evidence:
                        eligible.add(key)
                        self.follow_since.setdefault(key, time)
                        if time - self.follow_since[key] >= .5:
                            results.append({"runner": self.ids[runner], "defender": self.ids[defender],
                                            "residual_movement_m": round(float(length), 2),
                                            "alignment": round(cosine, 3),
                                            "ball_motion_confounded": bool(ball_confounded),
                                            "interpretation": "possible_following" if ball_confounded else "following_evidence"})
        self.follow_since = {k: v for k, v in self.follow_since.items() if k in eligible}
        return results

    def update(self, frame):
        validate_frame(frame, self.meta, self.previous_time)
        time = frame["t"]
        lookup = {p["id"]: p for p in frame["players"]}
        positions = np.array([[lookup[pid]["x"], lookup[pid]["y"]] for pid in self.ids])
        ball = np.array([frame["ball"]["x"], frame["ball"]["y"]])
        if self.previous_time is not None:
            dt = time - self.previous_time
            raw = (positions - self.previous_positions) / dt
            # Trailing exponential filter, never a centred/future-dependent smoother.
            alpha = 1 - math.exp(-dt / .3)
            self.velocities += alpha * (raw - self.velocities)
        if frame["possession"] != self.last_possession or self.phase_since is None:
            self.phase_since = time
            self.last_possession = frame["possession"]
            self.follow_since.clear()
        self.history.append((time, positions.copy(), ball.copy(), frame["ball"].get("owner")))
        while self.history and self.history[0][0] < time - self.cfg.history_seconds:
            self.history.popleft()
        self.previous_time, self.previous_positions = time, positions.copy()
        if time - self.last_update < 1 / self.cfg.update_hz - 1e-6:
            return None
        self.last_update = time
        travel = arrival_times(positions, self.velocities, self.grid, self.cfg)
        minima = [travel[self.teams == team].min(axis=0) for team in (0, 1)]
        # Probabilistic pitch control (Spearman et al. 2017) replaces the hard threshold.
        control = [pitch_control(minima[team], minima[1 - team]) for team in (0, 1)]
        transition = transition_weights(self.grid, ball)
        self.fields = {"t": time, "travel": travel, "minima": minima, "control": control,
                       "transition": transition, "positions": positions.copy(),
                       "velocities": self.velocities.copy(), "ball": ball.copy()}
        team_metrics = {}
        options_by_team = {}
        player_metrics = []
        for team in (0, 1):
            local_grid_x = self.local_x(self.grid[:, 0], team)
            # Only an advantage > 0.25 seconds counts; ties/uncontested uncertainty stay unassigned.
            advantage = minima[1 - team] - minima[team]
            # P(control) > 0.73 is equivalent to the previous 0.25 s arrival advantage.
            controlled = control[team] > .73
            team_value = obso(control[team], transition, self.threat_local[team])
            attacking_zone = local_grid_x > 70
            own_channel = (local_grid_x < 45) & ((self.grid[:, 1] < 18) | (self.grid[:, 1] > 50))
            options = self.pass_options(positions, self.velocities, ball, team)
            options_by_team[team] = options
            squad = np.flatnonzero(self.teams == team)
            lx = self.local_x(positions[squad, 0], team)
            bx = self.local_x(ball[0], team)
            team_metrics[self.team_names[team]] = {
                "attacking_control_area_m2": round(float(np.sum(controlled & attacking_zone) * self.cell_area), 2),
                "exposed_wide_area_m2": round(float(np.sum((advantage < -.25) & own_channel) * self.cell_area), 2),
                "accessible_receivers": sum(v["accessible"] for v in options.values()),
                "accessible_forward_receivers": sum(v["accessible"] and v["forward_m"] > 5 for v in options.values()),
                "advanced_central_players": int(np.sum((lx > 72) & (positions[squad, 1] > 20) & (positions[squad, 1] < 48))),
                "outfield_players_behind_ball": int(sum(lx[k] < bx and self.roles[i] != "GK" for k, i in enumerate(squad))),
                "ball_progress_m": round(float(bx), 2),
                "in_possession": frame["possession"] == self.team_names[team],
                "space_value_obso": round(team_value, 5),
                "pitch_control_share": round(float(control[team].mean()), 3),
            }
            # Same frozen frame, with this player omitted from arrival competition.
            # This measures direct geometric coverage, NOT the causal effect of their movement.
            for i in squad:
                others = squad[squad != i]
                without = travel[others].min(axis=0)
                unique = controlled & ((minima[1 - team] - without) <= .25) & attacking_zone
                without_value = obso(pitch_control(without, minima[1 - team]), transition, self.threat_local[team])
                region = role_region(self.roles[i], frame["possession"] == self.team_names[team], bx)
                local = np.array([self.local_x(positions[i, 0], team), positions[i, 1] if self.directions[team] == 1 else WIDTH - positions[i, 1]])
                nearest = np.clip(local, region[[0, 2]], region[[1, 3]])
                departure = float(np.linalg.norm(local - nearest))
                if departure > 4:
                    self.excursion_since.setdefault(i, time)
                else:
                    self.excursion_since.pop(i, None)
                sustained = i in self.excursion_since and time - self.excursion_since[i] >= self.cfg.excursion_seconds
                label = None
                if sustained:
                    label = "role_excursion"
                    if "ST" in self.roles[i] and local[0] < region[0]:
                        label = "striker_dropping_deep"
                    elif self.roles[i] in ("LB", "RB") and local[0] > region[1]:
                        label = "fullback_advanced"
                opponent = np.flatnonzero(self.teams != team)
                separation = np.min(np.linalg.norm(positions[opponent] - positions[i], axis=1))
                neighbourhood = np.linalg.norm(self.grid - positions[i], axis=1) < 8
                player_metrics.append({"player": self.ids[i], "role": self.roles[i], "team": self.team_names[team],
                                       "excursion": label, "outside_role_region_m": round(departure, 2),
                                       "nearest_opponent_m": round(float(separation), 2),
                                       "nearby_attacking_control_m2": round(float(np.sum(neighbourhood & controlled & attacking_zone) * self.cell_area), 2),
                                       "direct_attacking_coverage_m2": round(float(unique.sum() * self.cell_area), 2),
                                       "obso_contribution": round(team_value - without_value, 5),
                                       "pass_option": options.get(self.ids[i])})
        follows = self.following_evidence(positions, ball, frame["ball"].get("owner"), frame["possession"], time)
        changes = {}
        # Temporal changes are descriptive; only within uninterrupted possession.
        prior = [h for h in self.metric_history if h[0] >= self.phase_since and h[0] <= time - 2]
        if prior:
            baseline = prior[-1]
            for name in self.team_names:
                changes[name] = {k: round(team_metrics[name][k] - baseline[1][name][k], 2)
                                 for k in ("attacking_control_area_m2", "exposed_wide_area_m2",
                                           "accessible_forward_receivers", "advanced_central_players")}
            old_players = {p["player"]: p for p in baseline[2]}
            for follow in follows:
                team = self.teams[self.index[follow["runner"]]]
                beneficiaries = []
                for p in player_metrics:
                    if p["team"] != self.team_names[team] or p["player"] == follow["runner"]:
                        continue
                    old_p = old_players[p["player"]]
                    option, old_option = p["pass_option"], old_p["pass_option"]
                    opened = bool(option and option["accessible"] and not (old_option and old_option["accessible"]))
                    gain = p["nearby_attacking_control_m2"] - old_p["nearby_attacking_control_m2"]
                    if opened or gain > 15:
                        beneficiaries.append({"player": p["player"], "newly_accessible_pass": opened,
                                              "nearby_control_change_m2": round(gain, 2),
                                              "separation_change_m": round(p["nearest_opponent_m"] - old_p["nearest_opponent_m"], 2)})
                follow["concurrent_beneficiaries"] = beneficiaries
                follow["attribution"] = "Concurrent changes, not space causally credited to the runner."
        self.metric_history.append((time, team_metrics, player_metrics))
        while self.metric_history and self.metric_history[0][0] < time - self.cfg.history_seconds:
            self.metric_history.popleft()
        insights = []
        for p in player_metrics:
            if p["excursion"]:
                in_possession = frame["possession"] == p["team"]
                accessible = in_possession and p["pass_option"] and p["pass_option"]["accessible"]
                contribution = "Available passing option under the lane model." if accessible else "No accessible passing option detected at this instant."
                if not in_possession:
                    contribution = "Team is out of possession; this is a recovery/shape observation, not an attacking passing option."
                insights.append({"player": p["player"], "type": p["excursion"],
                                 "contribution": contribution,
                                 "exposure": f"Team has {team_metrics[p['team']]['advanced_central_players']} advanced central players; "
                                             f"{team_metrics[p['team']]['exposed_wide_area_m2']:.0f} m² of own wide channels have opponent arrival advantage."})
        for f in follows:
            beneficiaries = f.get("concurrent_beneficiaries", [])
            benefit = "Defender movement is consistent with following this run."
            if beneficiaries:
                opened = [b["player"] for b in beneficiaries if b["newly_accessible_pass"]]
                if opened:
                    benefit += " Concurrently, passing lanes became accessible to " + ", ".join(opened) + "."
                else:
                    benefit += " Concurrently, nearby attacking control increased around " + ", ".join(b["player"] for b in beneficiaries) + "."
            insights.append({"player": f["runner"], "type": "off_ball_run", "defender": f["defender"],
                             "contribution": benefit,
                             "exposure": "Ball movement is an alternative explanation." if f["ball_motion_confounded"] else
                                         "Movement association alone does not establish causation."})
        return {"kind": "analysis", "t": time, "teams": team_metrics, "players": player_metrics,
                "following": follows, "changes_over_2s": changes, "insights": insights,
                "interpretation": "Uncalibrated geometric indicators; temporal association is not causal attribution."}
