"""Value models used by the online analyser. Public tracking only; no simulator imports.

Everything here works in TEAM-LOCAL coordinates: the attacking team shoots at x=105,
goal centre (105, 34). Use `to_local` to convert.

Three ingredients, each chosen because it is the standard, explainable answer to a
question the tactical analyses need to ask:

* xG  -- "how good is this shot / this position to shoot from?"
  Logistic model on distance and goal-mouth angle (Pollard & Reep 1997; the same two
  features dominate every public xG model, e.g. Caley 2015, StatsBomb, Opta). Freeze-
  frame adjustments for defenders in the shot cone and close pressure follow the
  StatsBomb 360 idea. Coefficients are a literature-shaped PRIOR that reproduces
  common public benchmarks (6 m central ~0.45, penalty spot open play ~0.22, 25 m
  ~0.04). `fit_xg` can recalibrate them on pooled simulator shots.

* xT  -- "how much closer to a goal is the team when the ball is here?"
  Expected Threat (Singh 2018) values zones by the probability that possession from
  there ends in a goal within the next few actions. We ship a smooth parametric
  surface with the same shape as Singh's published 12x8 grid; `fit_xt` re-estimates
  it by value iteration from pooled matches (Singh's Markov method).

* Pitch control -- "who would reach this patch of grass first?"
  Spearman et al. (2017) logistic time-to-intercept model: P(team controls cell) =
  sigmoid(pi/sqrt(3)/sigma * (T_opp - T_team)), sigma = 0.45 s. Combined with xT and
  a ball-transition kernel it gives an Off-Ball Scoring Opportunity proxy
  (OBSO, Spearman 2018) -- the value of the space a team currently owns.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import numpy as np

from team_player.config.league import PITCH_LENGTH as LENGTH, PITCH_WIDTH as WIDTH
from team_player.config.analysis import (GOAL as _GOAL, POST_HALF, XG_PRIOR, PC_SIGMA, TRANSITION_SIGMA,
                                         PV_PRIOR, XT_GRID)
from team_player.utils.geometry import to_local, in_triangle  # noqa: F401  (to_local re-exported)

GOAL = np.array(_GOAL)


def goal_angle(local):
    """Angle (radians) subtended by the goal mouth. Vectorised over [...,2]."""
    p = np.asarray(local, float)
    dx = np.maximum(GOAL[0] - p[..., 0], 1e-3)
    dy = p[..., 1] - GOAL[1]
    a = np.arctan2(POST_HALF - dy, dx) + np.arctan2(POST_HALF + dy, dx)
    return np.clip(a, 0, math.pi)


class XGModel:
    def __init__(self, coefficients=None):
        self.c = {**XG_PRIOR, **(coefficients or {})}

    def location(self, local):
        """Location-only xG for a shot from local point(s) (no defenders)."""
        p = np.asarray(local, float)
        d = np.linalg.norm(GOAL - p, axis=-1)
        z = self.c["intercept"] + self.c["angle"] * goal_angle(p) + self.c["distance"] * d
        return 1 / (1 + np.exp(-z))

    def shot(self, shooter_local, defenders_local, keeper_local=None):
        """Freeze-frame xG: location + defenders blocking the shot triangle + pressure."""
        s = np.asarray(shooter_local, float)
        d = float(np.linalg.norm(GOAL - s))
        blockers, pressure = 0, 0
        posts = np.array([[105.0, 34 - POST_HALF], [105.0, 34 + POST_HALF]])
        for q in np.asarray(defenders_local, float).reshape(-1, 2):
            if in_triangle(q, s, posts[0], posts[1]):
                blockers += 1
            if np.linalg.norm(q - s) < 1.6:
                pressure = 1
        off_line = 0.0
        if keeper_local is not None:
            off_line = min(8.0, max(0.0, 105.0 - float(keeper_local[0])))
        z = (self.c["intercept"] + self.c["angle"] * float(goal_angle(s)) + self.c["distance"] * d
             + self.c["blockers"] * min(blockers, 3) + self.c["pressure"] * pressure
             + self.c["keeper_off_line"] * off_line)
        return {"xg": round(float(1 / (1 + math.exp(-z))), 4), "distance_m": round(d, 2),
                "angle_deg": round(math.degrees(float(goal_angle(s))), 1),
                "blockers": blockers, "under_pressure": bool(pressure)}


class ThreatModel:
    """Expected Threat surface on a regular grid in team-local coordinates."""

    def __init__(self, grid=None, nx=XT_GRID[0], ny=XT_GRID[1], xg=None):
        self.nx, self.ny = (len(grid[0]), len(grid)) if grid is not None else (nx, ny)
        self.grid = np.asarray(grid, float) if grid is not None else self.prior(self.nx, self.ny, xg or XGModel())

    @staticmethod
    def prior(nx, ny, xg):
        """Parametric approximation of Singh's (2018) xT grid.

        A slow exponential rise with pitch length (progress value, ~0.004 in own corner,
        ~0.02 at halfway), damped towards the touchlines, plus a share of location xG
        close to goal (value of being able to shoot). Max ~0.3 in the six-yard box.
        """
        xs = (np.arange(nx) + .5) * LENGTH / nx
        ys = (np.arange(ny) + .5) * WIDTH / ny
        xx, yy = np.meshgrid(xs, ys)
        base = 0.004 * np.exp(xx / 36.0) * (1 - 0.35 * ((yy - 34) / 34) ** 2)
        shoot = 0.6 * xg.location(np.stack([xx, yy], -1)) * (xx > 70)
        return base + shoot

    def value(self, local):
        p = np.asarray(local, float)
        ix = np.clip((p[..., 0] / LENGTH * self.nx).astype(int), 0, self.nx - 1)
        iy = np.clip((p[..., 1] / WIDTH * self.ny).astype(int), 0, self.ny - 1)
        return self.grid[iy, ix]

    def cell(self, local):
        p = np.asarray(local, float)
        return (int(np.clip(p[1] / WIDTH * self.ny, 0, self.ny - 1)),
                int(np.clip(p[0] / LENGTH * self.nx, 0, self.nx - 1)))


def pitch_control(team_times, opp_times, sigma=PC_SIGMA):
    """Spearman-style logistic control probability from minimum arrival times."""
    return 1 / (1 + np.exp(-math.pi / math.sqrt(3) / sigma * (opp_times - team_times)))


def transition_weights(grid_world, ball_world, sigma=TRANSITION_SIGMA):
    """Where the ball is likely to go next (Gaussian around the ball, normalised)."""
    d2 = np.sum((grid_world - ball_world) ** 2, axis=1)
    w = np.exp(-d2 / (2 * sigma ** 2))
    return w / w.sum()


def obso(control, transition, threat):
    """Off-ball scoring opportunity proxy: sum over cells of P(reach)*P(control)*xT."""
    return float(np.sum(control * transition * threat))


# ------------------------------------------------------------ possession value ---
# PV_PRIOR (config/analysis.py): ridge coefficients fitted offline on 32 pooled
# synthetic matches (25,759 states; the four shipped demo matches were excluded).
# Target: xG the team in possession produces (FOR) and concedes (AGAINST) in the next
# 15 s. Refit with `fit_pv`.


def pv_features(ball_x, ball_y_abs, xt, xg_loc, opp_goal_side, own_ahead, nearest_opp_m, obso_value, attacking_area_m2):
    return np.array([1, ball_x / 105, (ball_x / 105) ** 2, ball_y_abs / 34, xt * 10, xg_loc * 10, opp_goal_side / 10,
                     own_ahead / 10, min(nearest_opp_m, 15) / 15, obso_value * 100, attacking_area_m2 / 1000])


class PossessionValueModel:
    """VAEP-style state value (Decroos et al. 2019), in expected-goal units.

    PV(state) = E[xG for, next 15 s] - E[xG against, next 15 s] for the team on the ball.
    Credit for an action or a behaviour episode is the change in PV it coincides with.
    xG-smoothed targets (rather than 0/1 goals) cut label noise, which matters with tens,
    not thousands, of goals. In an offline test against the simulator's hidden player
    profiles, episode-level PV change (net of realised cost) separated profiles best
    among the value-based credit measures tried (xG-after, xGChain, xGBuildup, xT,
    off-ball OBSO, per-action PV).
    """

    def __init__(self, coefficients=None):
        c = coefficients or PV_PRIOR
        self.b_for, self.b_against = np.array(c["for"]), np.array(c["against"])

    def value(self, features):
        f = np.asarray(features, float)
        return float(max(0.0, f @ self.b_for) - max(0.0, f @ self.b_against))

    def threat(self, features):
        """Attacking part only: expected xG for the team on the ball in the next 15 s."""
        return float(max(0.0, np.asarray(features, float) @ self.b_for))


def fit_pv(rows, lam=1.0):
    """rows: (features, xg_for_next_15s, xg_against_next_15s). Ridge regression."""
    X = np.array([r[0] for r in rows], float)
    fit = lambda y: np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ np.asarray(y, float)).tolist()
    return {"for": fit([r[1] for r in rows]), "against": fit([r[2] for r in rows]), "fitted_on": len(rows)}


# ----------------------------------------------------------------- calibration ---

def fit_xg(shots, prior=None, l2=4.0, iterations=30):
    """Penalised logistic regression (IRLS) towards the literature prior.

    shots: iterable of dicts with angle_rad, distance_m, blockers, under_pressure, goal.
    The L2 penalty shrinks towards the prior, so a few hundred simulated shots adjust
    rather than replace published shapes.
    """
    prior = {**XG_PRIOR, **(prior or {})}
    keys = ["intercept", "angle", "distance", "blockers", "pressure"]
    rows = [[1, s["angle_rad"], s["distance_m"], min(s["blockers"], 3), float(s["under_pressure"])] for s in shots]
    if len(rows) < 30:
        return {**prior, "fitted_on": len(rows), "note": "too few shots; prior kept"}
    X, y = np.array(rows, float), np.array([float(s["goal"]) for s in shots])
    b0 = np.array([prior[k] for k in keys]);b = b0.copy()
    for _ in range(iterations):
        p = 1 / (1 + np.exp(-X @ b));W = p * (1 - p)
        H = X.T @ (X * W[:, None]) + l2 * np.eye(len(b))
        g = X.T @ (y - p) - l2 * (b - b0)
        step = np.linalg.solve(H, g);b += step
        if np.max(np.abs(step)) < 1e-6:
            break
    return {**prior, **{k: float(v) for k, v in zip(keys, b)}, "fitted_on": len(rows)}


def fit_xt(moves, shots, nx=XT_GRID[0], ny=XT_GRID[1], prior=None, pseudo=5.0, iterations=40):
    """Singh (2018) Markov xT by value iteration, smoothed with prior pseudo-counts.

    moves: list of ((row,col) start, (row,col) end, success) for passes/carries.
    shots: list of ((row,col), goal).  Returns an ny x nx grid.
    """
    prior = np.asarray(prior if prior is not None else ThreatModel(nx=nx, ny=ny).grid, float)
    n = ny * nx
    n_shot = np.zeros(n);n_goal = np.zeros(n);n_move = np.zeros(n);T = np.zeros((n, n))
    flat = lambda c: c[0] * nx + c[1]
    for start, end, ok in moves:
        n_move[flat(start)] += 1
        if ok:
            T[flat(start), flat(end)] += 1  # failed moves contribute zero onward value
    for cell, goal in shots:
        n_shot[flat(cell)] += 1;n_goal[flat(cell)] += goal
    actions = n_shot + n_move
    shoot_p = np.divide(n_shot, actions, out=np.zeros(n), where=actions > 0)
    goal_p = np.divide(n_goal, n_shot, out=np.zeros(n), where=n_shot > 0)
    move_T = np.divide(T, actions[:, None], out=np.zeros_like(T), where=actions[:, None] > 0)
    xt = np.zeros(n)
    for _ in range(iterations):
        xt = shoot_p * goal_p + move_T @ xt
    # Empirical-Bayes shrinkage towards the prior where data are sparse.
    blended = (actions * xt + pseudo * prior.ravel()) / (actions + pseudo)
    return blended.reshape(ny, nx)


class ValueModels:
    """Bundle loaded by the analyser. Optional `value_models.json` overrides priors."""

    def __init__(self, xg=None, threat=None, source="literature_prior", pv=None):
        self.xg = xg or XGModel()
        self.threat = threat or ThreatModel(xg=self.xg)
        self.pv = pv or PossessionValueModel()
        self.source = source

    @classmethod
    def load(cls, path=None):
        if path and Path(path).is_file():
            data = json.loads(Path(path).read_text())
            xg = XGModel({k: v for k, v in data.get("xg", {}).items() if k in XG_PRIOR})
            threat = ThreatModel(grid=data["xt"]) if data.get("xt") else ThreatModel(xg=xg)
            pv = PossessionValueModel(data["pv"]) if data.get("pv") else None
            return cls(xg, threat, source=f"fitted on pooled matches ({Path(path).name})", pv=pv)
        return cls()
