"""PRIVATE player personalities ("styles"). Read by the simulator only.

The analyser, the agents and the deployed app must never import this file; the Azure
bundle excludes it. Speeds are m/s, accelerations m/s². These are plausible
demonstration settings, not measurements of real footballers.

Each league player gets one of the types below, drawn independently per player (seeded by
LEAGUE_SEED and club), so team-mates in the same role can differ.

Type names say what the player does (striker / fullback / winger):
  drops_and_returns   drops deep to link play, then sprints back up front
  drops_and_stays     drops deep to link play, slow to get back up front
  stays_up_front      rarely drops, stays near the box
  decoy_runner        (striker) runs off the ball to pull defenders, rarely drops
  often_overlaps / sometimes_overlaps / stays_back   fullback pushing forward
  decoy_runner        (winger) many runs off the ball to pull defenders
  fast_finisher       (winger) fastest, few decoy runs, good finisher
  stays_wide          (winger) holds the width, few runs, good passer

Behaviour propensities (0-1, higher = more often; per 0.4 s decision, when the situation allows,
P(start) = 1 - exp(-k * propensity^2 * 0.4), k = 0.35 drop / 0.25 overlap / 0.22 decoy):
  drop           striker drops deep to link play
  return_urgency how quickly he sprints back to the front line afterwards
  overlap        fullback pushes forward as an extra winger
  decoy          off-ball run without expecting the ball
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    name: str = 'balanced'
    top_speed: float = 8.8
    acceleration: float = 3.8
    cruise: float = 4.2
    drop: float = .12
    return_urgency: float = .65
    overlap: float = .15
    decoy: float = .15
    passing: float = .88
    finishing: float = .75


STRIKERS = {
    'drops_and_returns': Style('drops_and_returns', 8.8, 3.7, drop=.92, return_urgency=.78, passing=.97),
    'stays_up_front': Style('stays_up_front', 9.3, 4.0, drop=.08, return_urgency=.96, finishing=.9),
    'decoy_runner': Style('decoy_runner', 9.7, 4.2, drop=.22, return_urgency=.94, decoy=.75, finishing=.85),
    'drops_and_stays': Style('drops_and_stays', 8.5, 3.5, drop=.94, return_urgency=.28, passing=.96),
}
FULLBACKS = {
    'often_overlaps': Style('often_overlaps', 9.4, 4.1, overlap=.90, passing=.91),
    'sometimes_overlaps': Style('sometimes_overlaps', 8.8, 3.8, overlap=.4),
    'stays_back': Style('stays_back', 8.5, 3.6, overlap=.07),
}
WINGERS = {
    'decoy_runner': Style('decoy_runner', 9.4, 4.0, decoy=.9, passing=.92, finishing=.83),
    'fast_finisher': Style('fast_finisher', 9.7, 4.2, decoy=.25, finishing=.88),
    'stays_wide': Style('stays_wide', 8.8, 3.8, decoy=.15, passing=.95),
}
# Roles outside the analysis scope get one fixed style each.
GOALKEEPER = Style('goalkeeper', 6.5, 3.0, cruise=2.5, passing=.95)
CENTRE_BACK = Style('centre_back', 8.4, 3.5, passing=.94)
MIDFIELDER = Style('midfield_link', 8.7, 3.8, passing=.95)

# Each player's top speed is jittered by +/- SPEED_JITTER (capped at MAX_TOP_SPEED).
SPEED_JITTER = .15
MAX_TOP_SPEED = 9.9
