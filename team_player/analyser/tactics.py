"""Online tactical episodes for strikers, fullbacks and wingers.

Causal: every decision uses the current frame and the past only. An episode is
*opened* when a behaviour is detected and *closed* when its consequences have been
observed (e.g. 12 s of counter-attack watch after a turnover). Closed episodes are the
records that `situations.py` pools across hundreds of matches.

Inputs are the observer frames (positions, inferred owner/possession/events) and,
at analysis ticks, the Analyzer's pitch-control fields. No simulator imports.
All thresholds below are defined (and can be changed) in config/analysis.py.

Three behaviours, each measured as BENEFIT and COST rather than "movement":

1. Striker dropping deep (false-9 link play)
   detect : >= 14 m short of the opponents' deepest outfield defender, outside the
            final third, sustained 1 s, team in possession.
   benefit: receptions in the pocket; forward passes measured by length, packing
            (opponents bypassed; Impect "packing"), and Delta-xT; whether a centre-back
            stepped out with him (space left behind the line).
   cost   : at the team's first final-third delivery afterwards (pass/shot released
            beyond 75 m), is the striker in the box and how many box targets exist?
            Arrival time back to the penalty spot is reported.

2. Fullback advancing (extra winger)
   detect : beyond max(halfway, defensive-midfield line + 6 m), sustained 1 s.
   benefit: flank overload (attackers minus defenders in his channel ahead of the
            ball), OBSO leave-one-out contribution, crosses.
   cost   : rest-defence checks every analysis tick: opponent-controlled area and best
            counter value in the vacated channel, recovery-time deficit to his
            defensive slot versus the nearest opponent attacker; and, if the ball is
            lost, the realised 12 s counter (shots, xG against, did it go through
            that flank?).

3. Winger off-ball runs (decoy vs target)
   detect : not on the ball, >= 4.0 m/s (Di Salvo "running" band, 14.4 km/h) for
            1.0 s and >= 7 m covered. Peak >= 5.5 m/s is flagged as high-speed. The
            synthetic wingers rarely exceed 5.5 m/s, so the real-football HSR
            threshold alone would miss almost every run.
   benefit: defenders drawn (movement aligned with the run after removing block
            shift) and the counterfactual OBSO gained by teammates: space value with
            the drawn defenders where they are now, minus with them restored to their
            pre-run positions (Fernandez & Bornn 2018 "space creation" idea). Then
            whether a teammate used it within 4 s (Delta-xT of next action, shots).
"""
from __future__ import annotations

from collections import deque
import math
import numpy as np

from team_player.analyser.models import ValueModels, pv_features, pitch_control, transition_weights
from team_player.config.analysis import (
    AnalysisConfig, BOX_X, BOX_Y, FINAL_THIRD, HALFWAY, FLANK_Y, SNAPSHOT_AFTER_S, WINDOW, HORIZON, COUNTER_WINDOW,
    DROP_GAP_M, DROP_END_GAP_M, DROP_MIN_POSSESSION_S, DROP_SUSTAIN_S, LINE_BREAK_LENGTH_M, LINE_BREAK_PACKING, RETURN_SPOT,
    ROLE_TEMPLATES, ROLE_SWAP_RADIUS_M, ROLE_TEMPLATE_BIN_M, PV_HORIZON_S, MISSED_CHANCE_MIN, BOX_SAMPLE_STEP_M,
    LANE_OPEN_MARGIN_S, PASS_RATE_PER_S,
    LOSS_RATE_PER_S, DEF_SHAPE_RADIUS_M, DEF_TEMPLATES, RECOVERY_SPEED_M_S, THREAT_CALIBRATION, FB_MIN_X, FB_DM_MARGIN_M, FB_END_HYSTERESIS_M, FB_SUSTAIN_S, FB_DEFENSIVE_SLOT,
    RUN_SPEED, HSR_SPEED, RUN_STOP_SPEED, RUN_STOP_S, RUN_SUSTAIN_S, RUN_MAX_S, RUN_MIN_M, TARGET_RUN_RECEIVE_S,
    RUN_FOLLOW_UP_S, DRAWN_MAX_START_M, DRAWN_MIN_MOVE_M, DRAWN_MIN_COS, DRAWN_SLACK_M)
from team_player.utils.fmt import r as _r
from team_player.utils.geometry import arrival_times, to_local


class TacticalTracker:
    def __init__(self, metadata, analyzer=None, models=None):
        self.meta = metadata
        self.analyzer = analyzer
        self.models = models or (analyzer.models if analyzer else ValueModels())
        self.ids = [p["id"] for p in metadata["players"]]
        self.index = {pid: i for i, pid in enumerate(self.ids)}
        self.roles = [p["role"] for p in metadata["players"]]
        self.team_names = [t["id"] for t in metadata["teams"]]
        self.teams = np.array([self.team_names.index(p["team"]) for p in metadata["players"]])
        self.dirs = [t["attack_direction"] for t in metadata["teams"]]
        self.gk = [int(np.flatnonzero((self.teams == k) & (np.array(self.roles) == "GK"))[0]) for k in (0, 1)]
        self.outfield = [np.flatnonzero((self.teams == k) & (np.array(self.roles) != "GK")) for k in (0, 1)]
        self.strikers = [i for i, r in enumerate(self.roles) if "ST" in r]
        self.fullbacks = [i for i, r in enumerate(self.roles) if r in ("LB", "RB")]
        self.wingers = [i for i, r in enumerate(self.roles) if r in ("LW", "RW", "LM", "RM")]
        self.prev_t = None;self.prev_pos = None;self.vel = np.zeros((22, 2))
        self.score = [0, 0];self.poss = None;self.last_poss_end = None
        self.release = None;self.spell = None;self.last_owner = None
        self.watches = [];self.completed_all = [];self.moves = [];self.shot_log = []
        self.drop = {};self.fb = {};self.run = {};self.pending = [];self.dfb = {};self.threat_last = {0: 0.0, 1: 0.0}
        self.live = {};self.trail = deque();self.pv_hist = deque()

    # --------------------------------------------------------------- helpers ---
    def L(self, pts, team):
        return to_local(pts, self.dirs[team])

    def xt(self, local):
        return float(self.models.threat.value(np.asarray(local)))

    def in_box(self, local):
        return local[0] > BOX_X and BOX_Y[0] < local[1] < BOX_Y[1]

    def flank_of(self, i):
        return "L" if self.roles[i].startswith("L") else "R"

    def in_flank(self, local, side):
        return local[1] < FLANK_Y[0] if side == "L" else local[1] > FLANK_Y[1]

    def opp_lines(self, P, team):
        opp = self.L(P[self.outfield[1 - team]], team)
        return float(opp[:, 0].max()), float(opp[:, 0].min())

    # ------------------------------------------------------------ main update ---
    def update(self, frame, analysis=None):
        t = float(frame["t"])
        lookup = {p["id"]: p for p in frame["players"]}
        P = np.array([[lookup[i]["x"], lookup[i]["y"]] for i in self.ids])
        B = np.array([frame["ball"]["x"], frame["ball"]["y"]])
        if self.prev_t is not None:
            dt = t - self.prev_t
            self.vel += (1 - math.exp(-dt / .3)) * ((P - self.prev_pos) / dt - self.vel)
        self.prev_t, self.prev_pos, self.prev_ball = t, P.copy(), B.copy()
        self.trail.append((t, P.copy()))
        while self.trail and self.trail[0][0] < t - 3:
            self.trail.popleft()
        owner = frame["ball"].get("owner")
        oi = self.index[owner] if owner else None
        completed = []
        for ev in frame.get("events", []):
            self.handle_event(ev, t, P, B, completed)
        if oi is not None:
            team = int(self.teams[oi])
            if self.poss is None or self.poss["team"] != team:
                self.close_possession(t, P, B, completed, new_owner=oi)
                self.open_possession(t, team, oi, P, B)
            self.poss["last_control"] = t
            if self.spell is None or self.spell["player"] != oi:
                self.spell = {"player": oi, "start": B.copy(), "t": t}
            bl = self.L(B, team)
            self.poss["max_xt"] = max(self.poss["max_xt"], self.xt(bl))
            if bl[0] > FINAL_THIRD and self.poss["final_third_at"] is None:
                self.poss["final_third_at"] = t
            if self.in_box(bl):
                self.poss["box_entry"] = True
            if self.poss["snapshot"] is None and t - self.poss["start"] >= SNAPSHOT_AFTER_S:
                self.poss["snapshot"] = self.context(t, P, B, self.poss["team"])
                self.poss["windows"].append(self.poss["snapshot"])
            elif self.poss["windows"] and t - self.poss["windows"][-1]["t"] >= WINDOW:
                self.poss["windows"].append(self.context(t, P, B, self.poss["team"]))
            self.poss["xt_trace"].append((t, self.xt(bl)))
            if analysis is not None:
                self.pv_hist.append((t, team, self.state_value(P, B, team, analysis)))
                self.threat_last[team] = self._threat
                while self.pv_hist and self.pv_hist[0][0] < t - 30:
                    self.pv_hist.popleft()
        self.last_owner = oi
        attacking = self.poss["team"] if self.poss and frame.get("possession") == self.team_names[self.poss["team"]] else None
        self.track_strikers(t, P, B, attacking, completed)
        self.track_fullbacks(t, P, B, attacking, analysis, completed)
        self.track_wingers(t, P, B, attacking, oi, completed)
        self.track_watches(t, P, B, completed)
        self.track_defence(t, P, B, analysis, completed)
        if analysis is not None and oi is not None and self.analyzer is not None:
            self.accumulate_position_value(P, B, int(self.teams[oi]), oi)
        self.completed_all.extend(completed)
        return {"t": t, "score": dict(zip(self.team_names, self.score)), "live": self.live_state(),
                "possession": self.possession_summary(t), "completed": completed}

    # ------------------------------------------------------- possession value ---
    def state_value(self, P, B, team, analysis):
        """PV of the current state for the team on the ball (expected goals, next 15 s, for - against)."""
        bl = self.L(B, team);ol = self.L(P[self.outfield[1 - team]], team);wl = self.L(P[self.outfield[team]], team)
        tm = analysis["teams"][self.team_names[team]]
        f = pv_features(bl[0], abs(bl[1] - 34), self.xt(bl), float(self.models.xg.location(bl)),
                        int((ol[:, 0] > bl[0]).sum()), int((wl[:, 0] > bl[0]).sum()),
                        float(np.min(np.linalg.norm(ol - bl, axis=1))), tm["space_value_obso"], tm["attacking_control_area_m2"])
        self._threat = self.models.pv.threat(f)
        return self.models.pv.value(f)

    def team_value(self, team, at=None):
        """Latest PV from `team`'s perspective at or before `at` (opponent on the ball = minus their PV)."""
        for t, tm, v in reversed(self.pv_hist):
            if at is None or t <= at + 1e-9:
                return v if tm == team else -v
        return 0.0

    def pv_at(self, P, team, B, travel, threat_only=False):
        """PV computed from raw positions/arrival times (used for counterfactual positions)."""
        an = self.analyzer;bl = self.L(B, team)
        ol = self.L(P[self.outfield[1 - team]], team);wl = self.L(P[self.outfield[team]], team)
        pc = pitch_control(travel[self.teams == team].min(0), travel[self.teams != team].min(0))
        space = float(np.sum(pc * transition_weights(an.grid, B) * an.threat_local[team]))
        area = float(np.sum((pc > .73) & (an.grid_local[team][:, 0] > 70)) * an.cell_area)
        f = pv_features(bl[0], abs(bl[1] - 34), self.xt(bl), float(self.models.xg.location(bl)),
                        int((ol[:, 0] > bl[0]).sum()), int((wl[:, 0] > bl[0]).sum()),
                        float(np.min(np.linalg.norm(ol - bl, axis=1))), space, area)
        return self.models.pv.threat(f) if threat_only else self.models.pv.value(f)

    def accumulate_position_value(self, P, B, team, owner):
        """Individual credit, sampled at analysis ticks while an episode is active.

        position value = PV(him where he is) - PV(him at the league-average spot for his role
        at this ball position), everyone else frozen. Converted to expected goals by the tick's
        share of the 15 s PV horizon."""
        active = []
        for s, d in self.drop.items():
            if d.get("episode") and self.teams[s] == team:
                active.append((s, d["episode"]))
        for i, f in self.fb.items():
            if f.get("episode") and self.teams[i] == team:
                active.append((i, f["episode"]))
        for i, w in self.run.items():
            if w.get("active") and self.teams[i] == team:
                active.append((i, w["active"]))
        active = [(i, ep) for i, ep in active if i != owner]
        # Striker's EXPECTED missed-chance cost: while he is dropped or still returning and the
        # ball is near the final third, any clear box chance nobody could take is charged at the
        # rate passes are played. Expected (every tick) rather than realised (only at the actual
        # delivery), so one lucky or unlucky ball does not decide his figure.
        bl_team = self.L(B, team)
        if bl_team[0] > FINAL_THIRD - 10:
            for s, d in self.drop.items():
                ep = d.get("episode") or (d.get("followup") or {}).get("episode")
                if ep is not None and self.teams[s] == team and s != owner and not self.in_box(self.L(P[s], team)):
                    ep["_miss_cost"] = ep.get("_miss_cost", 0.0) + PASS_RATE_PER_S * (1 / self.analyzer.cfg.update_hz) * self.missed_chance(s, team, P, B)
        if not active:
            return
        an = self.analyzer;travel = an.fields["travel"];actual = self.pv_at(P, team, B, travel)
        bl = self.L(B, team);dt = 1 / an.cfg.update_hz;still = np.zeros((1, 2))
        for i, ep in active:
            cf = P.copy();cf[i] = self.template_spot(i, team, B, P)
            tcf = travel.copy();tcf[i] = arrival_times(cf[[i]], still, an.grid, an.cfg)[0]
            ep["_position_value"] = ep.get("_position_value", 0.0) + (actual - self.pv_at(cf, team, B, tcf)) * dt / PV_HORIZON_S
            if ep.get("kind") == "fullback_advance":
                extra = self.counter_value(travel, team, ep["side"]) - self.counter_value(tcf, team, ep["side"])
                ep["_risk_cost"] = ep.get("_risk_cost", 0.0) + LOSS_RATE_PER_S * dt * max(0.0, extra)

    def counter_value(self, travel, team, side):
        """Best opponent counter target in this team's own-half channel: P(opp control) x opponent xT."""
        an = self.analyzer;gl = an.grid_local[team]
        mask = (gl[:, 0] < HALFWAY) & ((gl[:, 1] < FLANK_Y[0]) if side == "L" else (gl[:, 1] > FLANK_Y[1]))
        opp_pc = pitch_control(travel[self.teams != team].min(0), travel[self.teams == team].min(0))
        return float((opp_pc[mask] * an.threat_local[1 - team][mask]).max())

    @staticmethod
    def set_individual(ep, position_value, cost, decoy_space=0.0):
        """The player's own figure: position value (+ decoy space for wingers), minus the cost he caused."""
        net = position_value + decoy_space - cost
        ep["individual"] = {"position_value": _r(position_value, 5), "decoy_space": _r(decoy_space, 5),
                            "cost_xg": _r(cost, 4), "net": _r(net, 5)}

    @staticmethod
    def set_value(ep, start, end, cost=0.0):
        """Behaviour credit: change in possession value over the episode, net of its realised cost."""
        ep["value"] = {"pv_start": _r(start, 4), "pv_end": _r(end, 4), "cost_xg": _r(cost, 4), "net": _r(end - start - cost, 4)}

    # ------------------------------------------------------------- events ---
    def handle_event(self, ev, t, P, B, completed):
        kind = ev["type"]
        if kind in ("pass_attempt", "possible_shot"):
            i = self.index[ev["player"]];team = int(self.teams[i])
            self.record_carry(i, B, True)
            self.release = {"t": t, "player": i, "team": team, "start": B.copy(), "kind": kind,
                            "opp_local": self.L(P[self.outfield[1 - team]], team)}
            if kind == "possible_shot":
                self.register_shot(t, i, team, P, B)
            if self.poss and self.poss["team"] == team and self.L(B, team)[0] > FINAL_THIRD:
                self.on_delivery(t, P, B, team)
        elif kind == "reception" and self.release:
            i = self.index[ev["player"]];r = self.release
            if r["kind"] == "pass_attempt" and int(self.teams[i]) == r["team"]:
                self.on_pass(r, i, P, B, t)
            self.release = None
        elif kind == "possession_change":
            i = self.index[ev["player"]]
            if self.spell and self.spell["player"] != i and int(self.teams[self.spell["player"]]) != int(self.teams[i]):
                self.record_carry(self.spell["player"], B, False)
            if self.release and self.release["kind"] == "pass_attempt" and self.release["team"] != int(self.teams[i]):
                r = self.release;team = r["team"]
                self.moves.append((self.models.threat.cell(self.L(r["start"], team)), self.models.threat.cell(self.L(B, team)), False))
            self.release = None
        elif kind == "possible_goal":
            team = self.team_names.index(ev["team"]);self.score[team] += 1
            recent = [s for s in self.shot_log if s["team"] == team and t - s["t"] < 5]
            if not recent and self.release and self.release["team"] == team:
                recent = [self.register_shot(self.release["t"], self.release["player"], team, P, self.release["start"])]
            if recent:
                recent[-1]["goal"] = True
            if self.poss and self.poss["team"] == team:
                self.poss["goal"] = True
            for w in self.watches:
                if w["opp"] == team and t <= w["until"]:
                    w["goal_against"] = True
                    if t <= w["t"] + COUNTER_WINDOW:
                        w["goal_12s"] = True

    def record_carry(self, i, B, ok):
        if self.spell and self.spell["player"] == i:
            team = int(self.teams[i]);a, b = self.L(self.spell["start"], team), self.L(B, team)
            if np.linalg.norm(b - a) > 3:
                self.moves.append((self.models.threat.cell(a), self.models.threat.cell(b), ok))
        self.spell = None

    def register_shot(self, t, i, team, P, B):
        shooter = self.L(B, team)
        defenders = self.L(P[self.outfield[1 - team]], team)
        model = self.models.xg.shot(shooter, defenders, self.L(P[self.gk[1 - team]], team))
        shot = {"t": t, "player": self.ids[i], "team": team, **model, "goal": False,
                "angle_rad": math.radians(model["angle_deg"]), "cell": self.models.threat.cell(shooter)}
        self.shot_log.append(shot)
        if self.poss and self.poss["team"] == team:
            self.poss["shots"].append(shot)
        for w in self.watches:
            if w["opp"] == team and t <= w["until"]:
                w["xg_against"] += model["xg"];w["shots_against"] += 1
                if t <= w["t"] + COUNTER_WINDOW:
                    w["xg_12s"] += model["xg"];w["shots_12s"] += 1
                shot.setdefault("counter_charged_players", []).extend(ep["player"] for ep in w["subscribers"])
        return shot

    def on_pass(self, r, receiver, P, B, t):
        team = r["team"];a, b = self.L(r["start"], team), self.L(B, team)
        lo, hi = sorted((a[0], b[0]))
        packing = int(np.sum((r["opp_local"][:, 0] > lo) & (r["opp_local"][:, 0] < hi))) if b[0] > a[0] else 0
        rec = {"t": t, "passer": self.ids[r["player"]], "receiver": self.ids[receiver],
               "receiver_role": self.roles[receiver], "length_m": _r(np.linalg.norm(b - a), 1),
               "forward_m": _r(b[0] - a[0], 1), "packing": packing,
               "delta_xt": _r(self.xt(b) - self.xt(a), 4), "end_local": [_r(b[0], 1), _r(b[1], 1)]}
        self.moves.append((self.models.threat.cell(a), self.models.threat.cell(b), True))
        if self.poss and self.poss["team"] == team:
            self.poss["passes"] += 1
        d = self.drop.get(r["player"])
        if d and d["episode"] and rec["forward_m"] > 0:
            ep = d["episode"]
            ep["forward_passes"].append(rec)
            if ep["link_pass"] is None or rec["delta_xt"] > ep["link_pass"]["delta_xt"]:
                ep["link_pass"] = rec
            if self.poss:
                self.poss["choices"]["striker_link_pass"] = True
                self.poss["intervals"].append(["striker_link_pass", t, t, r["player"]])
        for w in self.run.values():
            ep = w.get("after")
            if ep and receiver == ep["runner"] and t - ep["ended"] <= TARGET_RUN_RECEIVE_S and ep["decoy"]:
                ep["received"], ep["decoy"] = True, False  # the run was found: a target run
                iv = ep.get("individual")
                if iv:
                    iv["net"] = _r(iv["net"] - iv["decoy_space"], 5);iv["decoy_space"] = 0.0
                if self.poss:
                    self.poss["choices"]["winger_decoy_runs"] -= 1;self.poss["choices"]["winger_target_runs"] += 1
                    for iv in self.poss["intervals"]:
                        if iv[0] == "winger_decoy" and iv[3] == ep["runner"] and iv[2] == ep["ended"]:
                            iv[0] = "winger_target_run"
            elif ep and t - ep["ended"] <= RUN_FOLLOW_UP_S and ep["next_action"] is None and receiver != ep["runner"]:
                ep["next_action"] = rec
        return rec

    def on_delivery(self, t, P, B, team):
        for s in self.strikers:
            if int(self.teams[s]) != team:
                continue
            d = self.drop.get(s, {})
            if d.get("episode"):
                self.end_drop(s, t, P, [])
            follow = d.get("followup")
            if follow and follow["delivery"] is None:
                own = self.L(P[self.outfield[team]], team)
                sl = self.L(P[s], team)
                follow["delivery"] = {"t": t, "striker_in_box": bool(self.in_box(sl)),
                                      "missed_chance_xg": _r(self.missed_chance(s, team, P, B), 4),
                                      "striker_to_goal_m": _r(np.linalg.norm(np.array([105, 34]) - sl), 1),
                                      "box_targets": int(sum(self.in_box(q) for q in own)),
                                      "best_box_target_xg": _r(max([float(self.models.xg.location(q)) for q in own if self.in_box(q)] or [0]), 3)}

    def template_spot(self, i, team, B, P=None):
        """Where a league-average player in i's role stands for this ball position (world coords).

        Role-swap aware: if a team-mate already occupies i's role spot (e.g. the attacking
        midfielder rotated into the striker's position), i is filling that team-mate's role,
        so he is compared with an average player in THAT role."""
        bl = self.L(B, team);b = lambda tpl: tpl[int(min(len(tpl) - 1, max(0, bl[0] // ROLE_TEMPLATE_BIN_M)))]
        spot = self.world(b(ROLE_TEMPLATES[self.roles[i]]), team)
        if P is not None:
            mates = [k for k in self.outfield[team] if k != i and np.linalg.norm(P[k] - spot) < ROLE_SWAP_RADIUS_M]
            if mates:
                k = min(mates, key=lambda k: np.linalg.norm(P[k] - spot))
                if self.roles[k] in ROLE_TEMPLATES:
                    spot = self.world(b(ROLE_TEMPLATES[self.roles[k]]), team)
        return spot

    def box_points(self, team):
        if not hasattr(self, "_box"):
            xs = np.arange(BOX_X + 1, 104, BOX_SAMPLE_STEP_M);ys = np.arange(BOX_Y[0] + 1, BOX_Y[1], BOX_SAMPLE_STEP_M)
            self._box = np.array([(x, y) for x in xs for y in ys])
        return self._box, self.world(self._box, team)

    def missed_chance(self, s, team, P, B):
        """Best box chance the ball could reach that an average striker would have taken but nobody did.

        For each box point: lane open (ball arrives LANE_OPEN_MARGIN_S before every defender),
        x [P(team controls it with an average striker there) - P(team controls it now)], x location xG.
        Returns 0 unless the best such chance is at least MISSED_CHANCE_MIN (a clear chance)."""
        cfg = self.analyzer.cfg if self.analyzer else AnalysisConfig()
        local, pts = self.box_points(team)
        own = [k for k in range(22) if self.teams[k] == team and self.roles[k] != "GK"];opp = np.flatnonzero(self.teams != team)
        t_opp = arrival_times(P[opp], self.vel[opp], pts, cfg).min(0)
        t_own = arrival_times(P[own], self.vel[own], pts, cfg)
        cf = P.copy();cf[s] = self.template_spot(s, team, B, P)
        t_cf = t_own.copy();t_cf[own.index(s)] = arrival_times(cf[[s]], np.zeros((1, 2)), pts, cfg)[0]
        ball_t = np.linalg.norm(pts - B, axis=1) / cfg.pass_speed
        open_lane = ball_t < t_opp - LANE_OPEN_MARGIN_S
        gain = np.maximum(0, pitch_control(t_cf.min(0), t_opp) - pitch_control(t_own.min(0), t_opp))
        best = float(np.max(open_lane * gain * self.models.xg.location(local))) if len(pts) else 0.0
        return best if best >= MISSED_CHANCE_MIN else 0.0

    # --------------------------------------------------------- possessions ---
    def open_possession(self, t, team, oi, P, B):
        regain = "keeper" if oi == self.gk[team] else ("transition" if self.last_poss_end and t - self.last_poss_end < 3 else "open_play")
        self.poss = {"team": team, "start": t, "start_local": self.L(B, team).round(1).tolist(), "regain": regain,
                     "passes": 0, "shots": [], "goal": False, "max_xt": 0.0, "final_third_at": None,
                     "box_entry": False, "snapshot": None, "last_control": t,
                     "choices": {"striker_dropped": False, "striker_link_pass": False, "fb_advanced_L": False,
                                 "fb_advanced_R": False, "winger_decoy_runs": 0, "winger_target_runs": 0},
                     "score_for": self.score[team], "score_against": self.score[1 - team],
                     "windows": [], "xt_trace": [], "intervals": []}

    def close_possession(self, t, P, B, completed, new_owner=None):
        p = self.poss
        if p is None:
            return
        reason = "goal" if p["goal"] else ("shot" if p["shots"] else "turnover")
        if new_owner is not None and new_owner == self.gk[1 - p["team"]] and reason == "turnover":
            reason = "keeper_claim"
        record = {"kind": "possession", "team": self.team_names[p["team"]],
                  "opponent": self.team_names[1 - p["team"]], "t_start": p["start"], "t_end": t,
                  "duration_s": _r(t - p["start"], 1), "regain": p["regain"], "start_local": p["start_local"],
                  "passes": p["passes"], "shots": len(p["shots"]), "xg": _r(sum(s["xg"] for s in p["shots"]), 4),
                  "goal": p["goal"], "max_xt": _r(p["max_xt"], 4), "final_third": p["final_third_at"] is not None,
                  "time_to_final_third_s": _r(p["final_third_at"] - p["start"], 1) if p["final_third_at"] else None,
                  "box_entry": p["box_entry"], "end_reason": reason, "choices": dict(p["choices"]),
                  "context": p["snapshot"], "lineup": self.lineup(p["team"]),
                  "conceded_xg_12s": 0.0, "conceded_shots_12s": 0, "conceded_goal_12s": False,
                  "windows": self.decision_windows(p, t, reason)}
        self.open_watch(t, record, 1 - p["team"], completed)
        self.last_poss_end = t;self.poss = None
        # Episodes owned by this possession close now (their counter watch continues).
        for s, d in list(self.drop.items()):
            if d.get("episode"):
                self.end_drop(s, t, P, completed)
            if d.get("followup"):
                self.finish_followup(s, record, completed, p["shots"])
        for i, f in list(self.fb.items()):
            if f.get("episode"):
                self.end_fb(i, t, P, B, completed, turnover=reason != "goal")

    def decision_windows(self, p, t_end, reason):
        """Split a possession into decision windows (finer retrieval units than whole possessions)."""
        out = []
        for c in p["windows"]:
            t0 = c["t"];t1 = t0 + WINDOW;h = min(t0 + HORIZON, t_end)
            choices = {k: any(key == k and a < t1 and (b is None or b >= t0) for key, a, b, _ in p["intervals"])
                       for k in ("striker_dropped", "striker_link_pass", "fb_advanced", "winger_decoy")}
            shots = [s for s in p["shots"] if t0 <= s["t"] <= h]
            xt = [v for tt, v in p["xt_trace"] if t0 <= tt <= h]
            lost = reason in ("turnover", "keeper_claim") and t_end <= t0 + HORIZON
            out.append({"t": t0, "context": c, "choices": choices, "xg": _r(sum(s["xg"] for s in shots), 4),
                        "shots": len(shots), "goal": any(s["goal"] for s in shots),
                        "max_xt": _r(max(xt) if xt else 0, 4), "xt_gain": _r((max(xt) - xt[0]) if xt else 0, 4),
                        "final_third": bool(p["final_third_at"] and t0 <= p["final_third_at"] <= h) or
                                       bool(xt and max(xt) >= self.xt((FINAL_THIRD + 1, 34))),
                        "lost_in_horizon": lost, "conceded_xg_12s": 0.0, "conceded_goal_12s": False})
        return out

    def lineup(self, team):
        return {self.roles[i]: self.ids[i] for i in range(22) if self.teams[i] == team and self.roles[i] in ("ST", "LB", "RB", "LW", "RW")}

    def context(self, t, P, B, team):
        """Pre-decision state used for similarity search. Deliberately excludes the
        player choices themselves (striker depth etc.) to avoid matching on outcomes."""
        bl = self.L(B, team);own = self.L(P[self.outfield[team]], team);opp = self.L(P[self.outfield[1 - team]], team)
        line, front = float(opp[:, 0].max()), float(opp[:, 0].min())
        return {"t": t, "minute": _r(t / 60, 2), "ball_x": _r(bl[0], 1), "ball_y_abs": _r(abs(bl[1] - 34), 1),
                "opp_line_x": _r(line, 1), "opp_front_x": _r(front, 1), "opp_compactness_m": _r(line - front, 1),
                "opp_width_m": _r(opp[:, 1].max() - opp[:, 1].min(), 1),
                "opp_goal_side": int(np.sum(opp[:, 0] > bl[0])), "own_ahead": int(np.sum(own[:, 0] > bl[0])),
                "regain_keeper": int(self.poss["regain"] == "keeper"), "regain_transition": int(self.poss["regain"] == "transition"),
                "score_diff": self.score[team] - self.score[1 - team], "ball_xt": _r(self.xt(bl), 4)}

    def possession_summary(self, t):
        p = self.poss
        if not p:
            return None
        return {"team": self.team_names[p["team"]], "since": p["start"], "regain": p["regain"],
                "shots": len(p["shots"]), "xg": _r(sum(s["xg"] for s in p["shots"]), 3), "max_xt": _r(p["max_xt"], 3),
                "choices": dict(p["choices"]), "context": p["snapshot"], "lineup": self.lineup(p["team"]),
                "opponent": self.team_names[1 - p["team"]],
                "window": {"t": p["windows"][-1]["t"], "context": p["windows"][-1]} if p["windows"] else None}

    # -------------------------------------------------------- counter watch ---
    def open_watch(self, t, record, opp, completed):
        w = {"t": t, "until": t + COUNTER_WINDOW, "opp": opp, "record": record, "xg_against": 0.0,
             "shots_against": 0, "goal_against": False, "xg_12s": 0.0, "shots_12s": 0, "goal_12s": False, "flank_entry": {"L": False, "R": False},
             "max_progress": 0.0, "subscribers": []}
        self.watches.append(w)
        return w

    def track_watches(self, t, P, B, completed):
        for w in list(self.watches):
            team = 1 - w["opp"];bl = self.L(B, team)
            if bl[0] < HALFWAY:
                for side in ("L", "R"):
                    if self.in_flank(bl, side):
                        w["flank_entry"][side] = True
            w["max_progress"] = max(w["max_progress"], 105 - bl[0])
            connected_ended = bool(w["subscribers"]) and (w["goal_against"] or
                (self.poss is not None and self.poss["team"] != w["opp"] and t > w["t"]) or
                B[0] < 0 or B[0] > 105 or B[1] < 0 or B[1] > 68)
            if t >= w["until"] or connected_ended:
                w["ended_at"] = t
                w["truncated"] = t >= w["until"] and not connected_ended
                self.finish_watch(w, completed)

    def finish_watch(self, w, completed):
        r = w["record"]
        r.update(conceded_xg_12s=_r(w["xg_12s"], 4), conceded_shots_12s=w["shots_12s"],
                 conceded_goal_12s=w["goal_12s"])
        for win in r.get("windows", []):
            if win["lost_in_horizon"]:
                win.update(conceded_xg_12s=_r(w["xg_12s"], 4), conceded_goal_12s=w["goal_12s"])
        completed.append(r)
        for ep in w["subscribers"]:
            side = ep["side"]
            ep["counter"] = {"xg_against": _r(w["xg_against"], 4), "shots_against": w["shots_against"],
                             "goal_against": w["goal_against"], "through_vacated_flank": w["flank_entry"][side],
                             "opp_progress_m": _r(w["max_progress"], 1),
                             "observed_until": w.get("ended_at", w["until"]), "truncated": w.get("truncated", False)}
            v = ep["value"];self.set_value(ep, v["pv_start"], v["pv_end"], w["xg_against"])
            completed.append(ep)
        self.watches.remove(w)

    def finish(self, t=None):
        """End of stream: close everything with what has been observed."""
        completed = []
        if self.prev_t is not None:
            t = self.prev_t if t is None else t
            self.close_possession(t, self.prev_pos, self.prev_ball, completed)
            for s in list(self.drop):
                if self.drop[s].get("followup"):
                    self.finish_followup(s, None, completed)
            for w in list(self.watches):
                self.finish_watch(w, completed)
            for i in list(self.dfb):
                self.end_defence(i, t, completed, regained=False)
            for i in list(self.run):
                if self.run[i].get("after"):
                    completed.append(self.close_after(i))
        self.completed_all.extend(completed)
        return completed

    # -------------------------------------------------------------- strikers ---
    def track_strikers(self, t, P, B, attacking, completed):
        for s in self.strikers:
            team = int(self.teams[s]);d = self.drop.setdefault(s, {"since": None, "episode": None, "followup": None})
            sl = self.L(P[s], team)
            if attacking != team:
                d["since"] = None
                continue
            line, _ = self.opp_lines(P, team);gap = line - sl[0]
            # Possession must be >= 2 s old: positions left over from defending are not a choice.
            dropping = (gap >= DROP_GAP_M and sl[0] < FINAL_THIRD and self.poss is not None
                        and t - self.poss["start"] >= DROP_MIN_POSSESSION_S)
            if d["episode"]:
                ep = d["episode"];ep["max_depth_m"] = max(ep["max_depth_m"], gap)
                if self.last_owner == s and (not ep["receptions"] or ep["receptions"][-1] < t - 1):
                    ep["receptions"].append(t)
                cb = self.nearest_cb(P, team, ep)
                if cb is not None:
                    ep["cb_step_m"] = max(ep["cb_step_m"], cb)
                if gap < DROP_END_GAP_M:
                    self.end_drop(s, t, P, completed)
                continue
            if dropping:
                d["since"] = d["since"] or t
                # Sustained for 1 s, or immediately if he receives while dropped.
                if t - d["since"] >= DROP_SUSTAIN_S or self.last_owner == s:
                    opp_cb = [i for i in self.outfield[1 - team] if "CB" in self.roles[i]]
                    cbi = min(opp_cb, key=lambda i: np.linalg.norm(P[i] - P[s])) if opp_cb else None
                    d["episode"] = {"kind": "striker_drop", "player": self.ids[s], "team": self.team_names[team],
                                    "t_start": d["since"], "start_local": sl.round(1).tolist(), "max_depth_m": gap,
                                    "receptions": [], "forward_passes": [], "link_pass": None,
                                    "cb": cbi, "cb_start_x": float(self.L(P[cbi], team)[0]) if cbi is not None else None,
                                    "cb_step_m": 0.0}
                    if self.last_owner == s:
                        d["episode"]["receptions"].append(t)
                    if self.poss:
                        self.poss["choices"]["striker_dropped"] = True
                        self.poss["intervals"].append(["striker_dropped", d["since"], None, s])
            else:
                d["since"] = None

    def nearest_cb(self, P, team, ep):
        if ep["cb"] is None:
            return None
        # Attacker-local x of the CB decreasing = CB stepping out towards halfway.
        return ep["cb_start_x"] - float(self.L(P[ep["cb"]], team)[0])

    def close_interval(self, key, player, t):
        for iv in (self.poss or {}).get("intervals", []):
            if iv[0] == key and iv[3] == player and iv[2] is None:
                iv[2] = t

    def end_drop(self, s, t, P, completed):
        self.close_interval("striker_dropped", s, t)
        d = self.drop[s];ep = d["episode"];d["episode"] = None;d["since"] = None
        team = int(self.teams[s])
        ep["_pv"] = (self.team_value(team, ep["t_start"]), self.team_value(team))
        ep.update(t_end=t, duration_s=_r(t - ep["t_start"], 1), max_depth_m=_r(ep["max_depth_m"], 1),
                  received=len(ep["receptions"]), cb_step_m=_r(ep["cb_step_m"], 1), receptions=len(ep["receptions"]),
                  return_time_to_spot_s=_r(arrival_times(P[[s]], self.vel[[s]], np.array([self.world(RETURN_SPOT, team)]),
                                                         self.analyzer.cfg if self.analyzer else AnalysisConfig())[0, 0], 2))
        ep.pop("cb");ep.pop("cb_start_x")
        ep["forward_passes"] = ep["forward_passes"][-4:]
        # Wait for the consequence: next delivery into the final third or possession end.
        d["followup"] = {"episode": ep, "delivery": None}

    def world(self, local, team):
        return to_local(np.asarray(local, float), self.dirs[team])

    def finish_followup(self, s, possession_record, completed, shots=()):
        d = self.drop[s];f = d["followup"];d["followup"] = None
        ep = f["episode"];ep["delivery"] = f["delivery"]
        if possession_record:
            # Only what happened after the striker started dropping counts as its outcome.
            post = [x for x in shots if x["t"] >= ep["t_start"]]
            ep["possession_outcome"] = {"shots": len(post), "xg": _r(sum(x["xg"] for x in post), 4),
                                        "goal": any(x["goal"] for x in post), "max_xt": possession_record["max_xt"],
                                        "end_reason": possession_record["end_reason"]}
        lp = ep["link_pass"]
        ep["link_type"] = None if not lp else ("long_line_break" if lp["length_m"] >= LINE_BREAK_LENGTH_M and lp["packing"] >= LINE_BREAK_PACKING else
                                               "to_winger" if lp["receiver_role"] in ("LW", "RW") else "forward")
        dl = f["delivery"]
        ep["cost_flag"] = bool(dl and not dl["striker_in_box"] and dl["box_targets"] <= 1)
        # Team outcome: the realised clear chance missed at the actual delivery.
        cost = (dl.get("missed_chance_xg") or 0.0) if dl and not dl["striker_in_box"] else 0.0
        start, end = ep.pop("_pv", (0.0, 0.0))
        self.set_value(ep, start, end, cost)
        # His own figure: the expected missed-chance cost accumulated tick by tick.
        self.set_individual(ep, ep.pop("_position_value", 0.0), ep.pop("_miss_cost", 0.0))
        completed.append(ep)

    # ------------------------------------------------------------ fullbacks ---
    def track_fullbacks(self, t, P, B, attacking, analysis, completed):
        for i in self.fullbacks:
            team = int(self.teams[i]);f = self.fb.setdefault(i, {"since": None, "episode": None})
            fl = self.L(P[i], team);side = self.flank_of(i)
            if attacking != team:
                f["since"] = None
                continue
            own = self.L(P, team)
            dm = [k for k in self.outfield[team] if "DM" in self.roles[k]]
            threshold = max(FB_MIN_X, float(np.mean(own[dm, 0])) + FB_DM_MARGIN_M if dm else FB_MIN_X)
            ep = f["episode"]
            if ep:
                bl = self.L(B, team)
                att = [k for k in self.outfield[team] if self.in_flank(own[k], side) and own[k, 0] > bl[0] - 5]
                oppl = self.L(P[self.outfield[1 - team]], team)
                dfn = [q for q in oppl if self.in_flank(q, side) and q[0] > bl[0] - 5]
                ep["max_overload"] = max(ep["max_overload"], len(att) - len(dfn))
                ep["max_x"] = max(ep["max_x"], float(fl[0]))
                wing = [k for k in self.wingers if self.teams[k] == team and self.flank_of(k) == side]
                if wing and fl[0] >= own[wing[0], 0] - 2:
                    ep["overlap_type"] = "overlap" if abs(fl[1] - 34) >= abs(own[wing[0], 1] - 34) else "underlap"
                if self.last_owner == i and (not ep["receptions"] or ep["receptions"][-1] < t - 1):
                    ep["receptions"].append(t)
                    if fl[0] > FINAL_THIRD:
                        ep["final_third_receptions"] += 1
                if analysis is not None and self.analyzer is not None and self.analyzer.fields is not None:
                    self.fb_risk(i, team, side, P, B, ep, analysis)
                if fl[0] < threshold - FB_END_HYSTERESIS_M:
                    self.end_fb(i, t, P, B, completed, turnover=False)
                continue
            if fl[0] >= threshold:
                f["since"] = f["since"] or t
                if t - f["since"] >= FB_SUSTAIN_S:
                    f["episode"] = {"kind": "fullback_advance", "player": self.ids[i], "team": self.team_names[team],
                                    "side": side, "t_start": f["since"], "max_x": float(fl[0]), "overlap_type": None,
                                    "max_overload": -11, "receptions": [], "final_third_receptions": 0,
                                    "risk_samples": [], "obso_samples": []}
                    if self.poss:
                        self.poss["choices"]["fb_advanced_" + side] = True
                        self.poss["intervals"].append(["fb_advanced", f["since"], None, i])
            else:
                f["since"] = None

    def fb_risk(self, i, team, side, P, B, ep, analysis):
        an = self.analyzer;fld = an.fields
        gl = an.grid_local[team]
        mask = (gl[:, 0] < HALFWAY) & ((gl[:, 1] < FLANK_Y[0]) if side == "L" else (gl[:, 1] > FLANK_Y[1]))
        opp_pc = fld["control"][1 - team][mask];opp_xt = an.threat_local[1 - team][mask]
        slot = self.world(FB_DEFENSIVE_SLOT[side], team)
        cfg = an.cfg
        fb_time = arrival_times(P[[i]], self.vel[[i]], slot[None], cfg)[0, 0]
        opp_att = [k for k in self.outfield[1 - team]]
        opp_time = arrival_times(P[opp_att], self.vel[opp_att], slot[None], cfg)[:, 0].min()
        bl = self.L(B, team);own = self.L(P[self.outfield[team]], team)
        ep["risk_samples"].append({"opp_area_m2": float((opp_pc > .5).sum() * an.cell_area),
                                   "counter_value": float((opp_pc * opp_xt).max()) if mask.any() else 0.0,
                                   "recovery_deficit_s": float(fb_time - opp_time),
                                   "rest_defenders": int(np.sum(own[:, 0] < bl[0] - 5))})
        me = next((p for p in analysis["players"] if p["player"] == self.ids[i]), None)
        if me and "obso_contribution" in me:
            ep["obso_samples"].append(me["obso_contribution"])

    def end_fb(self, i, t, P, B, completed, turnover):
        self.close_interval("fb_advanced", i, t)
        f = self.fb[i];ep = f["episode"];f["episode"] = None;f["since"] = None
        rs = ep.pop("risk_samples");obs = ep.pop("obso_samples")
        mean = lambda key: _r(np.mean([r[key] for r in rs]), 3) if rs else None
        ep.update(t_end=t, duration_s=_r(t - ep["t_start"], 1), max_x=_r(ep["max_x"], 1),
                  receptions=len(ep["receptions"]), mean_opp_area_vacated_m2=mean("opp_area_m2"),
                  max_counter_value=_r(max([r["counter_value"] for r in rs]), 4) if rs else None,
                  mean_recovery_deficit_s=mean("recovery_deficit_s"), mean_rest_defenders=mean("rest_defenders"),
                  mean_obso_contribution=_r(np.mean(obs), 5) if obs else None, turnover_while_advanced=turnover,
                  counter=None)
        team = int(self.teams[i]);self.set_value(ep, self.team_value(team, ep["t_start"]), self.team_value(team))
        # Individual cost is the EXPECTED counter threat his position added (realised counters are
        # shared with team-mates and noisy, so they stay in the team value only).
        self.set_individual(ep, ep.pop("_position_value", 0.0), ep.pop("_risk_cost", 0.0))
        if turnover and self.watches:
            ep["risk_at_loss"] = rs[-1] if rs else None
            self.watches[-1]["subscribers"].append(ep)
            # Follow this connected counter until it ends, bounded at 30 seconds.
            self.watches[-1]["until"] = self.watches[-1]["t"] + 30.0
        else:
            completed.append(ep)


    # ------------------------------------------------------ fullbacks without the ball ---
    def def_spot(self, i, team, B):
        """Where an average fullback in i's role stands when defending, for this ball position."""
        bl = self.L(B, team);tpl = DEF_TEMPLATES[self.roles[i]]
        return self.world(np.array(tpl[int(min(len(tpl) - 1, max(0, bl[0] // ROLE_TEMPLATE_BIN_M)))]), team)

    def track_defence(self, t, P, B, analysis, completed):
        """Phase B (recovery) and phase C (settled defence) while the opponents have the ball.

        Recovery starts when the opponents win the ball and ends once he is within
        DEF_SHAPE_RADIUS_M of his defensive spot (or after COUNTER_WINDOW s); settled defence then
        lasts until his team wins the ball back.
        Own value per tick = (opponents' threat with him at the comparison spot - with him where he
        is) x dt / 15 s, everyone else frozen: the same counterfactual as his attacking position
        value. Recovery is compared with an average fullback recovering from the SAME starting
        spot, so the gap he left when he pushed forward (already charged as expected counter risk)
        is not charged again.
        Team outcome (shared), in 15 s windows (the possession-value horizon): their threat at the
        window start - xG of their shots in the window. A window closes after 15 s, when they lose
        the ball (nothing remains) or at the end of the phase (their remaining threat is prorated)."""
        holder = self.poss["team"] if self.poss else None
        for i in self.fullbacks:
            team = int(self.teams[i]);d = self.dfb.get(i)
            if holder is None or holder == team:
                if d:
                    self.end_defence(i, t, completed, regained=True)   # their possession is over (won back, goal or dead ball)
                continue
            spot = self.def_spot(i, team, B)
            if d is None:
                d = self.dfb[i] = self.new_defence(i, team, t, P, "recovery")
            elif d["phase"] == "recovery" and (np.linalg.norm(P[i] - spot) < DEF_SHAPE_RADIUS_M or t - d["t0"] >= COUNTER_WINDOW):
                self.end_defence(i, t, completed, regained=False)
                d = self.dfb[i] = self.new_defence(i, team, t, P, "settled")
            if d["window"] is not None and t - d["window"][0] >= PV_HORIZON_S:
                self.close_window(d, team, t, 1.0)
            if analysis is None or self.analyzer is None or self.analyzer.fields is None:
                continue
            if d["window"] is None:
                d["window"] = (t, THREAT_CALIBRATION * self.threat_last[1 - team])
            an = self.analyzer;dt = 1 / an.cfg.update_hz;vel = np.zeros((1, 2))
            if d["phase"] == "recovery":
                base, vel = self.recovery_baseline(d["p0"], spot, t - d["t0"])
            else:
                base = spot
            travel = an.fields["travel"];cf = P.copy();cf[i] = base;tcf = travel.copy()
            tcf[i] = arrival_times(base[None], vel, an.grid, an.cfg)[0]
            opp = 1 - team
            d["own"] += (self.pv_at(cf, opp, B, tcf, threat_only=True) - self.pv_at(P, opp, B, travel, threat_only=True)) * dt / PV_HORIZON_S

    @staticmethod
    def recovery_baseline(p0, spot, elapsed):
        """Where an average fullback would be, recovering from p0 towards his defensive spot, after
        `elapsed` s (and his velocity). At the moment the ball is lost this is exactly where the
        player is, so how far forward he was costs nothing here: that was charged in phase A."""
        gap = spot - p0;dist = float(np.linalg.norm(gap))
        frac = 1.0 if dist < 1e-6 else min(1.0, RECOVERY_SPEED_M_S * elapsed / dist)
        vel = (RECOVERY_SPEED_M_S * gap / dist)[None] if frac < 1.0 else np.zeros((1, 2))
        return p0 + frac * gap, vel

    def new_defence(self, i, team, t, P, phase):
        return {"player": self.ids[i], "phase": phase, "t0": t, "p0": P[i].copy(), "own": 0.0, "team": 0.0, "windows": 0, "window": None,
                "threat_start": None, "shots_xg": 0.0}

    def close_window(self, d, team, t, share):
        """share = fraction of the window's expected threat that had the chance to happen."""
        t0, threat = d["window"]
        window_shots = [s for s in self.shot_log if s["team"] != team and t0 - 1e-9 <= s["t"] < t + 1e-9]
        shots = sum(s["xg"] for s in window_shots if d.get("player") not in s.get("counter_charged_players", []))
        d["team"] += threat * share - shots;d["shots_xg"] += sum(s["xg"] for s in window_shots);d["windows"] += 1
        d["counter_cost_elsewhere"] = d.get("counter_cost_elsewhere", 0.0) + sum(s["xg"] for s in window_shots) - shots
        d["threat_start"] = threat if d["threat_start"] is None else d["threat_start"]
        d["window"] = None

    def end_defence(self, i, t, completed, regained):
        d = self.dfb.pop(i, None)
        if d is None:
            return
        team = int(self.teams[i])
        if d["window"] is not None:
            # Lost the ball: the forecast was fully played out. Phase change / end of stream: only the
            # elapsed share of the 15 s forecast is compared (the rest carries into the next window).
            self.close_window(d, team, t, 1.0 if regained else min(1.0, (t - d["window"][0]) / PV_HORIZON_S))
        if not d["windows"]:
            return
        ep = {"kind": "fullback_recovery" if d["phase"] == "recovery" else "fullback_defence", "player": self.ids[i],
              "team": self.team_names[team], "side": self.flank_of(i), "t_start": d["t0"], "t_end": t,
              "duration_s": _r(t - d["t0"], 1), "regained": regained,
              "individual": {"net": _r(d["own"], 5)},
              "value": {"threat_start": _r(d["threat_start"], 4), "shots_xg": _r(d["shots_xg"], 4), "counter_cost_in_advance": _r(d.get("counter_cost_elsewhere", 0.0), 4), "windows": d["windows"], "net": _r(d["team"], 4)}}
        completed.append(ep)

    # -------------------------------------------------------------- wingers ---
    def track_wingers(self, t, P, B, attacking, oi, completed):
        for i in self.wingers:
            team = int(self.teams[i]);w = self.run.setdefault(i, {"fast_since": None, "slow_since": None, "active": None, "after": None})
            if w["after"] and (t - w["after"]["ended"] > RUN_FOLLOW_UP_S or attacking != team):
                completed.append(self.close_after(i))
            speed = float(np.linalg.norm(self.vel[i]))
            if attacking != team or oi == i:
                if w["active"]:
                    self.end_run(i, t, P, B, completed, received=oi == i)
                w["fast_since"] = None
                continue
            if w["active"]:
                a = w["active"];a["peak_speed"] = max(a["peak_speed"], speed)
                if speed < RUN_STOP_SPEED:
                    w["slow_since"] = w["slow_since"] or t
                    if t - w["slow_since"] >= RUN_STOP_S:
                        self.end_run(i, t, P, B, completed, received=False)
                else:
                    w["slow_since"] = None
                if w["active"] and t - a["t_start"] > RUN_MAX_S:
                    self.end_run(i, t, P, B, completed, received=False)
                continue
            if speed >= RUN_SPEED:
                w["fast_since"] = w["fast_since"] or t
                if t - w["fast_since"] >= RUN_SUSTAIN_S:
                    w["active"] = {"t_start": w["fast_since"], "start_pos": self.history_pos(w["fast_since"], P),
                                   "peak_speed": speed}
            else:
                w["fast_since"] = None

    def history_pos(self, t0, P):
        # Positions when the run actually began (trailing buffer; past frames only).
        return min(self.trail, key=lambda h: abs(h[0] - t0))[1].copy() if self.trail else P.copy()

    def end_run(self, i, t, P, B, completed, received):
        w = self.run[i];a = w["active"];w["active"] = None;w["slow_since"] = None;w["fast_since"] = None
        team = int(self.teams[i]);P0 = a["start_pos"]
        run = P[i] - P0[i];length = float(np.linalg.norm(run))
        if length < RUN_MIN_M:
            return
        l0, l1 = self.L(P0[i], team), self.L(P[i], team)
        line, _ = self.opp_lines(P, team)
        run_type = ("in_behind" if l1[0] - l0[0] > 4 and l1[0] > line - 2 else
                    "inside" if abs(l1[1] - 34) < abs(l0[1] - 34) - 4 else
                    "outside" if abs(l1[1] - 34) > abs(l0[1] - 34) + 4 else "forward" if l1[0] > l0[0] else "check")
        defenders = self.outfield[1 - team]
        shift = np.median(P[defenders] - P0[defenders], axis=0)
        drawn = []
        for k in defenders:
            mv = P[k] - P0[k] - shift;ml = float(np.linalg.norm(mv))
            cos = float(np.dot(run, mv) / max(length * ml, 1e-9))
            d0, d1 = np.linalg.norm(P0[k] - P0[i]), np.linalg.norm(P[k] - P[i])
            if d0 < DRAWN_MAX_START_M and ml > DRAWN_MIN_MOVE_M and cos > DRAWN_MIN_COS and d1 <= d0 + DRAWN_SLACK_M:
                drawn.append(k)
        space = None;area = None;beneficiary = None
        if self.analyzer is not None:
            an = self.analyzer
            actual = an.space_terms(P, self.vel, team, B, exclude=[i])
            cf = P.copy()
            for k in drawn:
                cf[k] = P0[k] + shift
            vel = self.vel.copy();vel[drawn] = 0
            counter = an.space_terms(cf, vel, team, B, exclude=[i]) if drawn else actual
            space = actual[0] - counter[0];area = actual[1] - counter[1]
            best = None
            for m in self.outfield[team]:
                if m == i:
                    continue
                opp = self.outfield[1 - team]
                now = np.min(np.linalg.norm(P[opp] - P[m], axis=1));before = np.min(np.linalg.norm(cf[opp] - P[m], axis=1))
                if now - before > 1 and (best is None or now - before > best[1]):
                    best = (m, now - before)
            if best:
                beneficiary = {"player": self.ids[best[0]], "separation_gain_m": _r(best[1], 1)}
        ep = {"kind": "winger_run", "player": self.ids[i], "team": self.team_names[team], "t_start": a["t_start"],
              "t_end": t, "duration_s": _r(t - a["t_start"], 1), "length_m": _r(length, 1), "run_type": run_type,
              "peak_speed_m_s": _r(a["peak_speed"], 1), "high_speed": a["peak_speed"] >= HSR_SPEED, "received": received, "decoy": not received,
              "drawn_defenders": [self.ids[k] for k in drawn], "space_created_obso": _r(space, 5),
              "space_created_m2": _r(area, 1),
              "beneficiary": beneficiary, "ended": t, "runner": i, "next_action": None,
              "possession_xg_before": _r(sum(s["xg"] for s in self.poss["shots"]), 4) if self.poss else 0}
        self.set_value(ep, self.team_value(team, a["t_start"]), self.team_value(team))
        # A decoy's credit also includes the team-mates' space it opened (drawn defenders restored).
        decoy_space = max(0.0, space or 0.0) if not received and drawn else 0.0
        self.set_individual(ep, a.get("_position_value", 0.0), 0.0, decoy_space)
        if self.poss:
            self.poss["choices"]["winger_target_runs" if received else "winger_decoy_runs"] += 1
            self.poss["intervals"].append(["winger_decoy" if not received else "winger_target_run", a["t_start"], t, i])
        w["after"] = ep

    def close_after(self, i):
        ep = self.run[i]["after"];self.run[i]["after"] = None
        ep.pop("runner", None);ep.pop("ended", None)
        team = self.team_names.index(ep["team"])
        shots = [s for s in self.shot_log if s["team"] == team and ep["t_end"] - .5 <= s["t"] <= ep["t_end"] + 4]
        ep["shots_within_4s"] = len(shots);ep["xg_within_4s"] = _r(sum(s["xg"] for s in shots), 4)
        ep["goal_within_4s"] = any(s["goal"] for s in shots)
        na = ep["next_action"]
        ep["space_used"] = bool(ep["decoy"] and ((na and na["delta_xt"] > .01) or shots))
        return ep

    # ----------------------------------------------------------- live view ---
    def live_state(self):
        out = {}
        for s, d in self.drop.items():
            ep = d.get("episode")
            out[self.ids[s]] = {"state": "dropping_deep", "depth_m": _r(ep["max_depth_m"], 1),
                                "receptions": len(ep["receptions"]), "cb_step_m": _r(ep["cb_step_m"], 1),
                                "best_forward_pass": ep["link_pass"]} if ep else (
                {"state": "returning", "awaiting_delivery": True} if d.get("followup") else {"state": "normal"})
        for i, f in self.fb.items():
            ep = f.get("episode")
            if ep:
                r = ep["risk_samples"][-1] if ep["risk_samples"] else {}
                out[self.ids[i]] = {"state": "advanced", "overlap_type": ep["overlap_type"], "max_overload": ep["max_overload"],
                                    "recovery_deficit_s": _r(r.get("recovery_deficit_s"), 2),
                                    "opp_area_vacated_m2": _r(r.get("opp_area_m2"), 0), "rest_defenders": r.get("rest_defenders")}
            else:
                out[self.ids[i]] = {"state": "normal"}
        for i, w in self.run.items():
            if w.get("active"):
                out[self.ids[i]] = {"state": "running", "peak_speed_m_s": _r(w["active"]["peak_speed"], 1)}
            elif w.get("after"):
                a = w["after"]
                out[self.ids[i]] = {"state": "run_completed", **{k: a[k] for k in ("run_type", "decoy", "drawn_defenders", "space_created_obso", "space_created_m2", "beneficiary")}}
            else:
                out[self.ids[i]] = {"state": "normal"}
        return out

