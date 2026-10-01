"""Normalize the read-only map rotation without inventing missing fields."""

from typing import Any

from app.errors import PanelError


def normalize_rotation(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if isinstance(raw, list):
        items = raw
        mode = "unknown"
        enabled = None
    elif isinstance(raw, dict):
        items = raw.get("entries", raw.get("items"))
        candidate_mode = raw.get("mode")
        if isinstance(candidate_mode,str):candidate_mode=candidate_mode.lower()
        mode = candidate_mode if candidate_mode in ("ordered", "random") else "unknown"
        enabled = raw.get("enabled") if isinstance(raw.get("enabled"), bool) else None
    else:
        raise PanelError("invalid_upstream")
    if not isinstance(items, list):
        raise PanelError("invalid_upstream")

    projected: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise PanelError("invalid_upstream")
        order = item.get("order", item.get("index", index))
        map_name = item.get("map")
        if (
            not isinstance(order, int)
            or isinstance(order, bool)
            or order < 0
            or not isinstance(map_name, str)
            or not map_name
        ):
            raise PanelError("invalid_upstream")
        experiences = item.get("experiences")
        if experiences is not None and (
            not isinstance(experiences, list)
            or any(not isinstance(value, str) for value in experiences)
        ):
            raise PanelError("invalid_upstream")
        lighting = item.get("lighting")
        if lighting is not None and not isinstance(lighting, str):
            raise PanelError("invalid_upstream")
        alternator = item.get("zoneAlternator")
        if alternator is not None and not isinstance(alternator, str):
            raise PanelError("invalid_upstream")
        status = item.get("status")
        if status is not None and not isinstance(status, str):
            raise PanelError("invalid_upstream")
        denied = item.get("denied")
        if denied is not None and not isinstance(denied, bool):
            raise PanelError("invalid_upstream")
        projected.append(
            {
                "order": order,
                "map": map_name,
                "experiences": experiences,
                "lighting": lighting,
                "zoneAlternator": alternator,
                "status": status,
                "denied": denied,
            }
        )
    return {"mode": mode, "enabled": enabled, "items": projected}
