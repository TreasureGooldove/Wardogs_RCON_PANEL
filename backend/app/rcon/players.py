"""Project display fields while treating a missing SteamID as non-actionable."""

import re
from typing import Any

from app.errors import PanelError


_STEAM_ID = re.compile(r"^[1-9][0-9]{16}$")


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def normalize_players(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(raw, list):
        players = raw
    elif isinstance(raw, dict):
        players = raw.get("players", raw.get("data"))
    else:
        raise PanelError("invalid_upstream")
    if not isinstance(players, list):
        raise PanelError("invalid_upstream")

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in players:
        if not isinstance(item, dict):
            raise PanelError("invalid_upstream")
        steam_id = item.get("steamId")
        name = item.get("name")
        if steam_id in (None, ""):
            steam_id = None
        if (
            (steam_id is not None and (
                not isinstance(steam_id, str)
                or _STEAM_ID.fullmatch(steam_id) is None
                or steam_id in seen
            ))
            or not isinstance(name, str)
            or len(name) > 64
        ):
            raise PanelError("invalid_upstream")
        if steam_id is not None:
            seen.add(steam_id)
        faction = item.get("faction")
        result.append(
            {
                "steamId": steam_id,
                "name": name,
                "faction": faction if isinstance(faction, str) else None,
                "kills": _nonnegative_int(item.get("kills")),
                "deaths": _nonnegative_int(item.get("deaths")),
                "cash": _nonnegative_int(item.get("cash")),
                "pingMs": _nonnegative_int(item.get("pingMs") if item.get("pingMs") is not None else item.get("ping")),
            }
        )
    return {"players": result}
