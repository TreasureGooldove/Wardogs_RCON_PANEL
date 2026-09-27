"""Minimal projections for three fixed Wardogs diagnostic GET routes."""

from datetime import UTC, datetime
from ipaddress import ip_address
import math
import re
from typing import Any
from urllib.parse import urlsplit

from app.errors import PanelError

from .routes import RouteName


_SERVER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_REPORTED_STATES = frozenset({"ok", "healthy", "degraded", "unhealthy"})
_MAX_SAFE_INTEGER = 9_007_199_254_740_991


def observed_at() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _object(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PanelError("invalid_upstream")
    return raw


def _count(value: Any, *, maximum: int = _MAX_SAFE_INTEGER) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum:
        return value
    return None


def project_health(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    data = _object(raw)
    state_raw = data.get("status", data.get("state"))
    state = state_raw.strip().lower() if isinstance(state_raw, str) else None
    if state not in _REPORTED_STATES:
        state = None
    healthy_raw = data.get("ok", data.get("healthy"))
    healthy = healthy_raw if isinstance(healthy_raw, bool) else None
    uptime_raw = data.get("uptimeSeconds")
    uptime = (
        uptime_raw
        if isinstance(uptime_raw, (int, float))
        and not isinstance(uptime_raw, bool)
        and 0 <= uptime_raw <= 1_000_000_000
        and math.isfinite(uptime_raw)
        else None
    )
    connections = data.get("connections")
    queue = data.get("gameThreadQueue")
    active = _count(connections.get("active"), maximum=1_000_000) if isinstance(connections, dict) else None
    if isinstance(queue, dict):
        raw_in_flight = queue.get("inFlight")
        in_flight = raw_in_flight if isinstance(raw_in_flight, bool) else _count(raw_in_flight, maximum=1_000_000)
        depth = _count(queue.get("depth"), maximum=1_000_000)
        rejected_total = _count(queue.get("rejectedTotal"))
    else:
        in_flight = depth = rejected_total = None
    return {
        "reachable": True,
        "reportedState": state,
        "reportedHealthy": healthy,
        "uptimeSeconds": uptime,
        "connections": {"active": active},
        "gameThreadQueue": {
            "inFlight": in_flight,
            "depth": depth,
            "rejectedTotal": rejected_total,
        },
    }


def project_server_id(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    value = _object(raw).get("serverId")
    if value == "":
        return {"serverId": None}
    if not isinstance(value, str) or _SERVER_ID.fullmatch(value) is None:
        raise PanelError("invalid_upstream")
    return {"serverId": value}


def _safe_image_url(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 2048 or any(
        ord(char) < 33 or ord(char) == 127 for char in value
    ):
        return None
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        return None
    host = url.hostname
    if (
        url.scheme != "https"
        or not host
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or port not in (None, 443)
    ):
        return None
    host = host.lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return None
    try:
        address = ip_address(host)
    except ValueError:
        if "." not in host:
            return None
    else:
        if not address.is_global:
            return None
    return value


def project_sponsor(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    value = _safe_image_url(_object(raw).get("imageUrl"))
    return {"imageUrl": value, "hasImage": value is not None}


PROJECTORS = {
    RouteName.HEALTH: project_health,
    RouteName.SERVER_ID: project_server_id,
    RouteName.SPONSOR: project_sponsor,
}
