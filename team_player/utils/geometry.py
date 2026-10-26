"""Shared geometry helpers (pure numpy). Coordinates are metres on a 105 x 68 pitch."""
import numpy as np
from team_player.config.league import PITCH_LENGTH, PITCH_WIDTH


def norm(v):
    """Euclidean length along the last axis."""
    return np.linalg.norm(v, axis=-1)


def limit(v, cap):
    """Scale vectors down so their length is at most `cap` (scalar or per-row)."""
    return v * np.minimum(1, np.asarray(cap) / np.maximum(norm(v), 1e-9))[..., None]


def to_local(points, direction):
    """World -> team-local frame (team attacks towards x=105). Both axes mirror, so left stays left.

    The transform is its own inverse, so it also maps local -> world.
    """
    p = np.asarray(points, float)
    return p if direction == 1 else np.stack([PITCH_LENGTH - p[..., 0], PITCH_WIDTH - p[..., 1]], axis=-1)


def in_triangle(p, a, b, c):
    """True if point p lies inside (or on) triangle abc."""
    def sign(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1, d2, d3 = sign(p, a, b), sign(p, b, c), sign(p, c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def arrival_times(positions, velocities, points, config):
    """Estimated seconds for each player to reach each point.

    Capped-speed, constant-acceleration straight-line model that accounts for the
    player's current velocity towards the point. `config` needs speed, acceleration
    and reaction_time. Ignores turning and contacts. Shape: (players, points).
    """
    delta = points[None, :, :] - positions[:, None, :]
    distance = np.linalg.norm(delta, axis=2)
    unit = delta / np.maximum(distance[..., None], 1e-9)
    initial = np.clip(np.sum(velocities[:, None, :] * unit, axis=2), -config.speed, config.speed)
    a = config.acceleration
    reach_cap = (config.speed ** 2 - initial ** 2) / (2 * a)
    accelerate_time = (-initial + np.sqrt(initial ** 2 + 2 * a * distance)) / a
    capped_time = (config.speed - initial) / a + (distance - reach_cap) / config.speed
    result = np.where(distance <= reach_cap, accelerate_time, capped_time)
    return np.where(distance < 1e-6, 0, config.reaction_time + result)
