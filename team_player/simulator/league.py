"""Private persistent squads: which personality each club's players have. Simulator only."""
from dataclasses import replace
import numpy as np
from team_player.config.league import TEAMS, LEAGUE_ROLES, LEAGUE_SEED, LEAGUE_FORMATION, player_id
from team_player.config.styles import (STRIKERS, FULLBACKS, WINGERS, GOALKEEPER, CENTRE_BACK, MIDFIELDER,
                                       SPEED_JITTER, MAX_TOP_SPEED)

ROLES = LEAGUE_ROLES


def squad_styles(roles, striker, fullback, winger, rng):
    """One Style per role; striker/fullback/winger personalities are chosen by name."""
    styles = []
    for role in roles:
        if 'ST' in role:
            base = STRIKERS[striker]
        elif role in ('LB', 'RB'):
            base = FULLBACKS[fullback]
        elif role in ('LW', 'RW', 'LM', 'RM'):
            base = WINGERS[winger]
        elif role == 'GK':
            base = GOALKEEPER
        elif 'CB' in role:
            base = CENTRE_BACK
        else:
            base = MIDFIELDER
        styles.append(replace(base, top_speed=float(min(MAX_TOP_SPEED, base.top_speed + rng.uniform(-SPEED_JITTER, SPEED_JITTER)))))
    return styles


def team_styles(team, roles=ROLES, league_seed=LEAGUE_SEED):
    """Each player's personality is drawn independently (two fullbacks or two wingers of the same
    club need not share a type), seeded by (league_seed, club) so it persists across matches and seasons."""
    rng = np.random.default_rng(np.random.SeedSequence([league_seed, TEAMS.index(team), 1]))
    styles = []
    for role in roles:
        if 'ST' in role:
            base = STRIKERS[str(rng.choice(list(STRIKERS)))]
        elif role in ('LB', 'RB'):
            base = FULLBACKS[str(rng.choice(list(FULLBACKS)))]
        elif role in ('LW', 'RW', 'LM', 'RM'):
            base = WINGERS[str(rng.choice(list(WINGERS)))]
        elif role == 'GK':
            base = GOALKEEPER
        elif 'CB' in role:
            base = CENTRE_BACK
        else:
            base = MIDFIELDER
        styles.append(replace(base, top_speed=float(min(MAX_TOP_SPEED, base.top_speed + rng.uniform(-SPEED_JITTER, SPEED_JITTER)))))
    return styles


def draw_teams(rng):
    return tuple(str(t) for t in rng.choice(TEAMS, size=2, replace=False))


def public_roster():
    return [{'id': player_id(team, LEAGUE_FORMATION, i), 'team': team, 'role': role} for team in TEAMS for i, role in enumerate(ROLES)]
