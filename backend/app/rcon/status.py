"""Project the Wardogs status response onto the public panel contract."""

from typing import Any

from app.errors import PanelError


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def normalize_status(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PanelError("invalid_upstream")
    experiences = raw.get("experiences")
    if not isinstance(experiences, list) or any(
        not isinstance(item, str) for item in experiences
    ):
        experiences = None

    scores = raw.get("factionScores")
    if isinstance(scores, list):
        faction_scores = []
        for item in scores:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            score = item.get("score", item.get("value"))
            if isinstance(score, int) and not isinstance(score, bool):
                faction_scores.append({"name": item["name"], "score": score})
        if scores and not faction_scores:
            faction_scores = None
    else:
        faction_scores = None

    player_group = raw.get("players")
    player_group = player_group if isinstance(player_group, dict) else {}
    player_count = _nonnegative_int(raw.get("playerCount"))
    max_players = _nonnegative_int(raw.get("maxPlayers"))
    if player_count is None:
        player_count = _nonnegative_int(player_group.get("current"))
    if max_players is None:
        max_players = _nonnegative_int(player_group.get("max"))

    return {
        "serverName": _text(raw.get("serverName")),
        "map": _text(raw.get("map")),
        "experiences": experiences,
        "lighting": _text(raw.get("lighting")),
        "alternator": _text(raw.get("alternator")),
        "playerCount": player_count,
        "maxPlayers": max_players,
        "factionScores": faction_scores,
        "scoreTick": (
            {
                "current": _nonnegative_int(raw["scoreTick"].get("current")),
                "min": _nonnegative_int(raw["scoreTick"].get("min")),
                "max": _nonnegative_int(raw["scoreTick"].get("max")),
            }
            if isinstance(raw.get("scoreTick"), dict)
            else None
        ),
        "scoreCap": _nonnegative_int(raw.get("scoreCap")),
        "matchSeconds": _nonnegative_int(raw.get("matchSeconds")),
        "rotation": (
            {
                "nowIndex": _nonnegative_int(raw["rotation"].get("nowIndex")),
                "nextIndex": _nonnegative_int(raw["rotation"].get("nextIndex")),
            }
            if isinstance(raw.get("rotation"), dict)
            else None
        ),
    }
