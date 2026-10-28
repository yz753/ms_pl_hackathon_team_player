"""League, lineup and match definitions. Edit here to change teams, formations or defaults.

Public definitions only: team IDs, roles and formations appear in the public metadata
anyway. Player personalities live in `config/styles.py`, which only the simulator reads.
"""

# Pitch (metres). The public data contract (schema.py) requires these values.
PITCH_LENGTH, PITCH_WIDTH = 105.0, 68.0

# League: 20 clubs, A-T. Player IDs are <team><shirt number>, e.g. "A09" = club A's striker.
TEAMS = tuple("ABCDEFGHIJKLMNOPQRST")
LEAGUE_SEED = 2026                      # fixes every club's private player attributes
LEAGUE_FORMATION = "4231"               # every league squad uses this lineup ...
LEAGUE_ROLES = ("GK", "LB", "LCB", "RCB", "RB", "LDM", "RDM", "LW", "CAM", "RW", "ST")  # ... in this ID order

# Formations: role order = player index 00..10.
FORMATIONS = {
    "4231": ["GK", "LB", "LCB", "RCB", "RB", "LDM", "RDM", "LW", "CAM", "RW", "ST"],
    "433": ["GK", "LB", "LCB", "RCB", "RB", "DM", "LCM", "RCM", "LW", "RW", "ST"],
    "442": ["GK", "LB", "LCB", "RCB", "RB", "LM", "LCM", "RCM", "RM", "LST", "RST"],
}

# Conventional shirt numbers, in the same order as FORMATIONS (unique within a team).
# 4-2-3-1: GK 1, LB 3, LCB 5, RCB 4, RB 2, LDM 6, RDM 8, LW 11, CAM 10, RW 7, ST 9.
SHIRT_NUMBERS = {
    "4231": [1, 3, 5, 4, 2, 6, 8, 11, 10, 7, 9],
    "433": [1, 3, 5, 4, 2, 6, 8, 10, 11, 7, 9],
    "442": [1, 3, 5, 4, 2, 11, 8, 6, 7, 10, 9],
}


def player_id(team, formation, index):
    """ID of the player at position `index` (0-10) of `formation`, e.g. ("A", "4231", 10) -> "A09"."""
    return f"{team}{SHIRT_NUMBERS[formation][index]:02d}"


# Base shape (x towards opponent goal, y across), used by the legacy clip simulator
# and as each role's home position.
ANCHORS = {"GK": (7, 34), "LB": (34, 9), "LCB": (28, 24), "RCB": (28, 44), "RB": (34, 59),
           "LDM": (45, 26), "RDM": (45, 42), "DM": (44, 34), "LCM": (52, 25), "RCM": (52, 43),
           "LM": (62, 12), "RM": (62, 56), "LW": (68, 12), "RW": (68, 56), "CAM": (61, 34),
           "ST": (74, 34), "LST": (73, 27), "RST": (73, 41)}

# Roles covered by the analysis (strikers, wingers, fullbacks).
STRIKER_ROLES = ("ST", "LST", "RST")
WINGER_ROLES = ("LW", "RW", "LM", "RM")
FULLBACK_ROLES = ("LB", "RB")

# Continuous match generator defaults (python -m team_player.simulator.match).
MATCH_DURATION_S = 600.0                # maximum allowed by the simulator
SAMPLE_HZ = 10
TEMPOS = ("mixed", "patient", "direct")

# Seasons (Premier League format): every club plays every other club home and away,
# 38 matchweeks x 10 matches = 380 matches per season. History seasons are simulated and
# analysed offline; the current season is streamed as if live (online analysis only).
HISTORY_SEASONS = ("2019-20", "2020-21", "2021-22", "2022-23", "2023-24")
CURRENT_SEASON = "2026-27"
SEASON_SEED = 20190801                 # master seed: same seed -> identical fixtures and matches
SEASON_START = {"2019-20": "2019-08-10", "2020-21": "2020-09-12", "2021-22": "2021-08-14",
                "2022-23": "2022-08-06", "2023-24": "2023-08-12", "2024-25": "2024-08-17"}
# Assumption: squads and personalities never change across seasons (fixed by LEAGUE_SEED).

# Base simulator scenario options (simulator/engine.py). Continuous matches use "free_play".
SCENARIOS = ("drop_deep", "overlap", "decoy", "ordinary", "free_play")
VARIANTS = ("mixed", "track", "hold", "covered", "exposed")
