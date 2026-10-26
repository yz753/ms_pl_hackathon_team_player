"""Public data contract. No tactical policies or scenario labels live here."""
from __future__ import annotations

import math

from team_player.config.league import PITCH_LENGTH as LENGTH, PITCH_WIDTH as WIDTH

SCHEMA_VERSION = 1


def validate_metadata(meta: dict) -> None:
    if meta.get("kind") != "metadata" or meta.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Expected version-1 metadata as the first record")
    if meta.get("pitch") != {"length": LENGTH, "width": WIDTH}:
        raise ValueError("This prototype expects a 105 x 68 metre pitch")
    teams = meta.get("teams", [])
    if len(teams) != 2 or {t["attack_direction"] for t in teams} != {-1, 1}:
        raise ValueError("Expected two teams with opposing attack directions")
    ids = [p["id"] for p in meta["players"]]
    if len(ids) != 22 or len(set(ids)) != 22:
        raise ValueError("Expected 22 unique player IDs")
    team_ids = {t["id"] for t in teams}
    if len(team_ids) != 2:
        raise ValueError("Team IDs must be unique")
    for team in team_ids:
        squad = [p for p in meta["players"] if p["team"] == team]
        if len(squad) != 11 or sum(p["role"] == "GK" for p in squad) != 1:
            raise ValueError("Each team must have eleven players including one goalkeeper")


def validate_frame(frame: dict, meta: dict, previous_time: float | None = None) -> None:
    if frame.get("kind") != "frame":
        raise ValueError("Expected a frame")
    time = frame["t"]
    if not math.isfinite(time) or time < 0 or (previous_time is not None and time <= previous_time):
        raise ValueError("Frame timestamps must be finite and strictly increasing")
    known = {p["id"] for p in meta["players"]}
    actual = [p["id"] for p in frame["players"]]
    if len(actual) != 22 or set(actual) != known:
        raise ValueError("Frame must contain every player exactly once")
    for item in [*frame["players"], frame["ball"]]:
        if not all(math.isfinite(item[k]) for k in ("x", "y")):
            raise ValueError("Coordinates must be finite")
        if not (0 <= item["x"] <= LENGTH and 0 <= item["y"] <= WIDTH):
            raise ValueError("Coordinates outside pitch")
    owner = frame["ball"].get("owner")
    if owner is not None and owner not in known:
        raise ValueError("Unknown ball owner")
    possession = frame.get("possession")
    if possession is not None and possession not in {t["id"] for t in meta["teams"]}:
        raise ValueError("Unknown possession team")
    if owner is not None:
        team = next(p["team"] for p in meta["players"] if p["id"] == owner)
        if possession != team:
            raise ValueError("Ball owner and possession team disagree")
    state = frame["ball"].get("state")
    if state is not None:
        if state not in {"controlled", "pass", "shot", "loose", "dead"}:
            raise ValueError("Unknown observable ball state")
        if (state == "controlled") != (owner is not None):
            raise ValueError("Only a controlled ball can have an owner")
        if state in {"loose", "dead"} and possession is not None:
            raise ValueError("Loose/dead balls have no established possession")
        if owner is not None:
            player = next(p for p in frame["players"] if p["id"] == owner)
            if math.hypot(player["x"] - frame["ball"]["x"], player["y"] - frame["ball"]["y"]) > 1.1:
                raise ValueError("Controlled ball is too far from its owner")
