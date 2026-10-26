"""Analyser parameters and definitions. Public: safe to ship to Azure.

Every threshold the online analysis uses is defined here, grouped by topic, so it can
be tuned without touching the logic. Units: metres, seconds, m/s. Pitch coordinates
are team-local: x = 0 own goal line ... 105 opponent goal line, y = 0 ... 68 (left).
"""
from dataclasses import dataclass
import math

from team_player.config.league import STRIKER_ROLES, WINGER_ROLES, FULLBACK_ROLES


# --------------------------------------------------------------- geometry engine ---
@dataclass
class AnalysisConfig:
    update_hz: float = 2.0            # heavy geometry (pitch control etc.) refresh rate
    grid_step: float = 3.0            # pitch-control grid cell size (m)
    history_seconds: float = 5.0      # trailing window kept for movement evidence
    speed: float = 7.5                # arrival-time model: top speed (m/s)
    acceleration: float = 3.0         # arrival-time model: acceleration (m/s²)
    reaction_time: float = .25        # arrival-time model: reaction delay (s)
    pass_speed: float = 16.0          # ball speed used for passing-lane margins (m/s)
    excursion_seconds: float = .8     # time outside a role band before it counts
    value_models_path: str | None = None  # optional value_models.json fitted on pooled matches

    def validate(self):
        if not .1 <= self.update_hz <= 30:
            raise ValueError("update_hz must be in [0.1, 30]")
        if not .5 <= self.grid_step <= 10:
            raise ValueError("grid_step must be in [0.5, 10] metres")
        if self.history_seconds < 3 or any(x <= 0 or not math.isfinite(x) for x in
                [self.history_seconds, self.speed, self.acceleration, self.pass_speed, self.excursion_seconds]):
            raise ValueError("Positive finite dynamics and at least 3 seconds of history required")
        if not math.isfinite(self.reaction_time) or self.reaction_time < 0:
            raise ValueError("reaction_time must be nonnegative")


SUPPORTED_ROLES = set(STRIKER_ROLES + WINGER_ROLES + FULLBACK_ROLES)
MIN_POSSESSION_S = 5.0                # overlay highlights wait for this much control

# ------------------------------------------------------------------ value models ---
GOAL = (105.0, 34.0)
POST_HALF = 3.66                      # half the goal width (m)
# xG logistic prior: distance + goal-mouth angle + freeze-frame terms. Reproduces
# public benchmarks (6 m central ~0.45, penalty spot ~0.22, 25 m ~0.04).
XG_PRIOR = {"intercept": -0.835, "angle": 1.167, "distance": -0.1073,
            "blockers": -0.55, "pressure": -0.45, "keeper_off_line": 0.04}
PC_SIGMA = 0.45                       # pitch-control logistic scale, s (Spearman et al. 2017)
TRANSITION_SIGMA = 14.0               # ball-transition kernel scale, m (Spearman 2018)
XT_GRID = (16, 12)                    # Expected Threat grid (columns along x, rows along y)
# Possession value (VAEP-style): ridge coefficients for xG FOR / AGAINST in the next
# 15 s, fitted on 32 pooled synthetic matches (demo matches excluded). Refit: models.fit_pv.
PV_HORIZON_S = 15.0
PV_PRIOR = {
    "for": [-0.02337, -0.05254, 0.11286, 0.00317, 0.11779, -0.08168, -0.00954, 0.02638, -0.00509, 0.02175, 0.00776],
    "against": [0.00597, -0.01383, 0.00597, 0.00211, 0.00364, -0.00203, 0.00017, -0.00106, -0.00276, -9e-05, 0.00062],
}

# Individual credit: a player's own POSITION VALUE is PV with him where he is, minus PV
# with him moved to where a league-average player in his role stands at that moment
# (team-mates and opponents frozen). Average positions by role and ball-x bin (7.5 m),
# measured as medians from public tracking of 32 pooled matches (all clubs pooled).
ROLE_TEMPLATE_BIN_M = 7.5
ROLE_SWAP_RADIUS_M = 6.0              # a team-mate this close to his role's spot means the roles were swapped
ROLE_TEMPLATES = {
    "CAM": [(31.1, 34.0), (43.0, 34.0), (46.9, 34.0), (50.8, 34.0), (53.1, 34.0), (56.3, 34.0), (60.7, 34.0), (62.9, 34.0), (66.5, 34.0), (69.3, 34.0), (72.8, 34.0), (76.8, 34.0), (80.8, 34.0), (83.0, 34.0)],
    "LB": [(19.8, 12.5), (27.0, 8.0), (31.1, 8.0), (36.0, 8.0), (37.2, 8.0), (40.7, 8.0), (45.5, 8.1), (48.4, 8.0), (53.2, 8.0), (54.9, 8.0), (58.0, 8.0), (61.7, 8.0), (65.7, 8.0), (85.5, 8.7)],
    "LCB": [(17.8, 25.3), (18.0, 21.0), (20.0, 21.0), (23.6, 21.0), (24.9, 21.0), (26.4, 21.0), (28.9, 21.0), (30.4, 21.0), (32.7, 21.0), (34.6, 21.0), (36.6, 21.0), (38.9, 21.0), (41.2, 21.0), (43.3, 21.0)],
    "LDM": [(28.7, 24.5), (31.0, 24.0), (34.9, 24.0), (38.3, 24.0), (40.8, 24.0), (43.4, 24.0), (47.8, 24.0), (50.8, 24.0), (53.8, 24.0), (56.4, 24.0), (59.4, 24.0), (62.7, 24.0), (66.3, 24.0), (69.2, 24.0)],
    "LW": [(35.2, 15.8), (48.0, 9.0), (52.0, 9.0), (56.3, 9.0), (59.0, 9.0), (61.9, 9.0), (66.6, 9.0), (69.0, 9.0), (72.9, 9.0), (73.5, 9.3), (78.5, 9.7), (83.8, 10.2), (88.7, 9.3), (86.2, 9.9)],
    "RB": [(19.4, 55.9), (27.0, 60.0), (31.1, 60.0), (36.0, 60.0), (36.9, 59.8), (40.7, 59.9), (45.4, 60.0), (48.3, 60.0), (52.9, 59.9), (54.9, 59.9), (58.1, 60.0), (62.1, 60.0), (65.8, 60.0), (86.6, 58.5)],
    "RCB": [(17.7, 42.9), (18.0, 47.0), (19.7, 47.0), (24.4, 47.0), (24.8, 47.0), (26.4, 47.0), (28.8, 47.0), (30.4, 47.0), (32.7, 47.0), (34.6, 47.0), (36.6, 47.0), (38.8, 47.0), (41.2, 47.0), (43.3, 47.0)],
    "RDM": [(28.5, 43.7), (31.0, 44.0), (34.9, 44.0), (38.3, 44.0), (40.7, 44.0), (43.3, 44.0), (47.9, 44.0), (50.9, 44.0), (54.0, 44.0), (56.2, 44.0), (59.3, 44.0), (62.7, 44.0), (66.4, 44.0), (69.1, 44.0)],
    "RW": [(35.8, 53.2), (48.0, 59.0), (52.0, 59.0), (56.3, 59.0), (58.9, 59.0), (61.9, 59.0), (66.6, 59.0), (69.0, 59.0), (72.8, 59.0), (74.2, 58.9), (78.5, 58.6), (83.9, 58.5), (89.5, 58.1), (84.5, 55.8)],
    "ST": [(43.7, 34.1), (57.1, 34.0), (60.1, 34.0), (63.0, 34.0), (63.4, 34.0), (63.3, 34.0), (68.3, 34.0), (72.8, 34.0), (76.8, 34.0), (75.4, 34.0), (81.4, 34.0), (87.8, 34.0), (93.0, 34.0), (91.3, 33.8)],
}

# ------------------------------------------------------------- pitch zones (local) ---
BOX_X, BOX_Y = 88.5, (13.84, 54.16)   # penalty area
FINAL_THIRD = 75.0                    # a pass/shot released beyond this is a "delivery"
HALFWAY = 52.5
FLANK_Y = (22.7, 45.3)                # left flank y < 22.7, right flank y > 45.3

# ---------------------------------------------------------------- episode timing ---
SNAPSHOT_AFTER_S = 1.5                # first decision context after this much control
WINDOW = 8.0                          # decision window: choices read over the next 8 s ...
HORIZON = 15.0                        # ... outcomes over the next 15 s (or until the ball is lost)
COUNTER_WINDOW = 12.0                 # watch for an opponent counter after a loss

# ------------------------------------------------------------ striker drop-deep ---
DROP_GAP_M = 14.0                     # start: this far short of the deepest opponent outfielder
DROP_END_GAP_M = 9.0                  # end: back within this distance
DROP_MIN_POSSESSION_S = 2.0           # ignore positions left over from defending
DROP_SUSTAIN_S = 1.0
LINE_BREAK_LENGTH_M, LINE_BREAK_PACKING = 30.0, 3   # "long_line_break" link pass
RETURN_SPOT = (94.0, 34.0)            # where a returning striker should be for the finish
# Harsher "empty box": the striker is only charged for a MISSED CHANCE -- a box spot the ball
# could reach (open lane), worth >= MISSED_CHANCE_MIN xG, that an average striker would have
# reached first but nobody did. If wingers/AMs/anyone covered it, there is no cost.
MISSED_CHANCE_MIN = 0.15
BOX_SAMPLE_STEP_M = 2.0               # spacing of the box points checked at a delivery
LANE_OPEN_MARGIN_S = 0.15             # ball must arrive this much before any defender
# Expected missed-chance cost rate: a clear box chance nobody can take is charged at the
# rate passes are played (the next pass could have found it).
PASS_RATE_PER_S = 0.24                # passes per second of possession (pooled data)

# ------------------------------------------------------------- fullback advance ---
FB_MIN_X = HALFWAY                    # start: beyond max(halfway, DM line + FB_DM_MARGIN_M)
FB_DM_MARGIN_M = 6.0
FB_END_HYSTERESIS_M = 5.0             # end: dropped this far back behind the threshold
FB_SUSTAIN_S = 1.0
FB_DEFENSIVE_SLOT = {"L": (25.0, 8.0), "R": (25.0, 60.0)}   # recovery target
# Fullback individual cost = expected counter threat his position adds:
# LOSS_RATE x dt x max(0, counter value with him there - with an average fullback there),
# counter value = best opponent P(control) x opponent xT in his own-half channel.
LOSS_RATE_PER_S = 0.0185              # open-play ball losses per second of possession (pooled data)

# ------------------------------------------------------- fullback without the ball ---
# Three phases, each charging something different (no double count):
#   A. his team has the ball  -> attacking gain minus EXPECTED counter risk (above)
#   B. recovery: from the moment the opponents win the ball until he is back within
#      DEF_SHAPE_RADIUS_M of his defensive spot (at most COUNTER_WINDOW s). Compared with an
#      average fullback recovering from the SAME starting spot at RECOVERY_SPEED_M_S, so the gap
#      he left (already charged in A) cancels out; only better/worse recovery counts.
#   C. settled defence: compared with an average fullback's defensive spot (DEF_TEMPLATES).
# Own value per tick = (opponents' threat with him at the comparison spot - with him where he is)
# x dt / 15 s, everyone else frozen (threat = attacking part of possession value).
# Team outcome (shared), per 15 s window: their threat at the window start - xG of their shots in
# it. If they lose the ball first, nothing remains; at a phase change only the elapsed share of the
# forecast is compared.
DEF_SHAPE_RADIUS_M = 5.0
DEF_TEMPLATE_AFTER_S = 8.0            # positions used for DEF_TEMPLATES: this long into their possession
# Medians by ball-x bin (ROLE_TEMPLATE_BIN_M, ball in the fullback's team frame), from
# `python -m team_player.analyser.history fit-defence --input data/history/2019-20` (30 matches,
# 291,106 samples; positions only, all clubs pooled).
DEF_TEMPLATES = {
    "LB": [(16.7, 15.3), (12.5, 15.6), (17.7, 14.9), (24.3, 14.5), (27.5, 14.7), (29.4, 13.4), (33.8, 12.7), (37.1, 12.1), (42.4, 10.9), (44.3, 11.2), (44.5, 11.2), (47.0, 13.0), (44.4, 15.7), (44.4, 15.7)],
    "RB": [(17.3, 52.6), (13.0, 52.7), (17.3, 52.9), (23.1, 52.7), (27.6, 52.9), (29.7, 53.7), (34.1, 55.3), (37.1, 55.4), (42.4, 57.2), (44.2, 56.8), (44.8, 56.8), (46.4, 54.6), (44.3, 52.2), (44.3, 52.2)],
}
# The 15 s threat forecast over-predicts the shots the opponents actually take in these windows
# (32.7 forecast vs 24.3 real xG over the 30 history matches), so the team figure uses it scaled:
# `history fit-defence --calibrate` (fitted with this set to 1.0).
THREAT_CALIBRATION = 0.742
RECOVERY_SPEED_M_S = 2.04             # mean speed towards that spot in the first 12 s, while > 5 m away

# ----------------------------------------------------------- winger off-ball run ---
RUN_SPEED = 4.0                       # m/s, 14.4 km/h: "running" band (Di Salvo et al. 2007)
HSR_SPEED = 5.5                       # m/s, 19.8 km/h: high-speed flag (Bradley et al. 2009)
RUN_STOP_SPEED, RUN_STOP_S = 3.0, .5  # run ends after this long below this speed
RUN_SUSTAIN_S, RUN_MAX_S, RUN_MIN_M = 1.0, 6.0, 7.0
TARGET_RUN_RECEIVE_S = 1.5            # received this soon after the run => target run, not decoy
RUN_FOLLOW_UP_S = 4.0                 # did a team-mate use the space within this time?
DRAWN_MAX_START_M = 15.0              # defender "drawn": started within 15 m of the runner,
DRAWN_MIN_MOVE_M = 2.0                #   moved > 2 m beyond the block's shift,
DRAWN_MIN_COS = .5                    #   in the run's direction,
DRAWN_SLACK_M = 3.0                   #   and did not lose touch by more than 3 m

# ----------------------------------------------------- similar-situation search ---
# (feature, weight). Context only -- never the choices being compared.
SIMILARITY_FEATURES = (("ball_x", 1.2), ("ball_y_abs", .5), ("opp_line_x", 1.0), ("opp_front_x", .6),
                       ("opp_compactness_m", .7), ("opp_width_m", .3), ("opp_goal_side", 1.0), ("own_ahead", .7),
                       ("regain_keeper", .8), ("regain_transition", .9), ("score_diff", .4), ("minute", .15))
SIMILARITY_K = 40
SAME_TEAM_BONUS, SAME_OPPONENT_BONUS = .35, .25
MIN_SIDE_N = 5                        # hide comparisons with fewer neighbours on one side
CHOICES = {"striker_dropped": "Striker dropped deep", "fb_advanced": "A fullback advanced",
           "winger_decoy": "A winger made a decoy run"}

# ------------------------------------------------------------ player comparison ---
MIN_COMPARISON_POSSESSION_S = 120.0   # below this, warn "limited evidence"

# Reading the two figures together (per 10 min of team possession). Below these sizes a
# figure counts as "about zero". Own value is individual; team outcome is shared.
BALANCE_OWN_EPS = 0.003
BALANCE_TEAM_EPS = 0.03

# ------------------------------------------------------------- plain English text ---
AUDIENCES = ("newcomer", "fan", "analyst")
WORD_LIMIT = {"newcomer": 70, "fan": 55, "analyst": 110}
GLOSSARY = {
    "xG": "expected goals: the chance a shot goes in, from 0 to 1 (0.10 means about 1 in 10)",
    "xT": "expected threat: how much closer to a goal the ball is after a pass or carry",
    "possession value": "the expected goals a team is likely to create minus concede in the next 15 seconds from where the ball is",
    "own value": "the threat of where he stood minus that of an average player in his role there, per 10 min of his team's possession, counted only during his move (striker dropping deep or returning, winger run, fullback pushed forward). Strikers lose the expected value of open box chances nobody took; fullbacks lose the expected counter-attack risk behind them; wingers gain the space a decoy run opened. A fullback's defending without the ball is a separate figure (defending value)",
    "team outcome": "how much his team's possession value changed during his moves, per 10 min of possession, minus chances it actually cost (strikers: a clear box chance missed; fullbacks: a counter after losing the ball); shared with team-mates",
    "defending value": "a fullback's own figure without the ball: the opponents' threat with an average fullback in his place minus with him there (while recovering, an average fullback recovering from the same spot), per 10 min of the opponents' possession",
    "xG prevented": "the opponents' expected threat minus the xG they actually shot while he defended, per 10 min of their possession; shared with team-mates",
    "net value": "how much the team's possession value changed during the move, minus any goal chances it cost (shared with team-mates)",
    "position value": "possession value with the player where he stood, minus with an average player in his role standing there instead",
    "pitch control": "the probability that a team would reach a patch of grass first",
    "packing": "the number of opponents a forward pass goes past",
    "overload": "attackers minus defenders on one side of the pitch, ahead of the ball",
    "recovery deficit": "how many seconds later than the nearest opponent the defender would get back to his position",
    "decoy run": "a run without the ball that is not passed to; its job is to pull defenders away",
    "missed chance": "a spot in the box the ball could reach, worth at least 0.15 xG, that nobody was there to take",
    "final third": "the attacking third of the pitch, beyond 75 m",
    "pp": "percentage points of goal probability",
    "n": "how many similar past moments the figure is based on",
    "CI": "the range the true figure probably lies in",
}
