"""Capability discovery from actual advertised read-only routes."""

import asyncio
from datetime import UTC, datetime
import re
from time import monotonic
from typing import Any

from app.errors import PanelError

from .client import RconClient
from .routes import ROUTES, WRITE_ROUTES, RouteName, WriteName


FEATURE_ROUTES = {
    "status": RouteName.STATUS,
    "players": RouteName.PLAYERS,
    "rotation": RouteName.ROTATION,
    "maps": RouteName.MAPS,
    "experiences": RouteName.EXPERIENCES,
    "lightings": RouteName.LIGHTINGS,
}
WRITE_FEATURE_ROUTES = {
    "kick": WriteName.KICK,
    "ban": WriteName.BAN,
}
_PARAMETER = re.compile(r"\{[^/{}]+\}")
_COLON_PARAMETER = re.compile(r":[^/\s]+")
_ADVERTISED_METHODS = {"GET", "POST", "PATCH", "PUT", "DELETE"}
_KNOWN_ROUTES = {
    (spec.method, spec.path) for spec in (*ROUTES.values(), *WRITE_ROUTES.values())
}
_ADVERTISED_ACTIONS = {
    "config": ("GET", ROUTES[RouteName.CONFIG].path),
    "reservedSlots": ("GET", ROUTES[RouteName.RESERVED_SLOTS].path),
    "bans": ("GET", ROUTES[RouteName.BANS].path),
    "audit": ("GET", ROUTES[RouteName.AUDIT].path),
    "sponsor": ("GET", ROUTES[RouteName.SPONSOR].path),
    "serverId": ("GET", ROUTES[RouteName.SERVER_ID].path),
    "health": ("GET", ROUTES[RouteName.HEALTH].path),
    "mapExperiences": ("GET", ROUTES[RouteName.MAP_EXPERIENCES].path),
    "mapAlternators": ("GET", ROUTES[RouteName.MAP_ALTERNATORS].path),
    **{
        name.value: (WRITE_ROUTES[name].method, WRITE_ROUTES[name].path)
        for name in WriteName
        if name not in {WriteName.KICK, WriteName.BAN}
    },
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _capability_key(value: str) -> str | None:
    parts = value.strip().split(maxsplit=1)
    if len(parts) == 1:
        method, path = "GET", parts[0]
    elif len(parts) == 2:
        method, path = parts[0].upper(), parts[1].strip()
    else:
        return None
    if method not in _ADVERTISED_METHODS or not path.startswith("/") or "?" in path:
        return None
    return f"{method} {_COLON_PARAMETER.sub('*', _PARAMETER.sub('*', path))}"


def _features(raw: dict[str, Any] | list[Any]) -> dict[str, bool]:
    if not isinstance(raw, dict) or not isinstance(raw.get("routes"), list):
        raise PanelError("invalid_upstream")
    routes = raw["routes"]
    if any(not isinstance(value, str) for value in routes):
        raise PanelError("invalid_upstream")
    advertised = {_capability_key(value) for value in routes}
    return {
        **{
            feature: f"GET {ROUTES[route].path}" in advertised
            for feature, route in FEATURE_ROUTES.items()
        },
        **{
            feature: _capability_key(WRITE_ROUTES[route].capability_key) in advertised
            for feature, route in WRITE_FEATURE_ROUTES.items()
        },
    }


class CapabilityService:
    def __init__(self, client: RconClient, *, ttl_seconds: float = 30.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.client = client
        self.ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._features: dict[str, bool] | None = None
        self._observed_at: str | None = None
        self._expires_at = 0.0
        self._retry_at = 0.0
        self._last_error: PanelError | None = None
        self._advertised: set[str] = set()
        self._config_writable = False

    async def get(self) -> dict[str, Any]:
        now = monotonic()
        if self._features is not None and now < self._expires_at:
            return self._snapshot("available")
        if now < self._retry_at:
            return self._snapshot("stale" if self._features is not None else "unavailable")
        async with self._lock:
            now = monotonic()
            if self._features is not None and now < self._expires_at:
                return self._snapshot("available")
            if now < self._retry_at:
                return self._snapshot("stale" if self._features is not None else "unavailable")
            try:
                raw = await self.client.request(RouteName.CAPABILITIES)
                features = _features(raw)
            except PanelError as exc:
                self._last_error = exc
                self._retry_at = monotonic() + 5.0
                return self._snapshot(
                    "stale" if self._features is not None else "unavailable"
                )
            self._features = features
            assert isinstance(raw, dict)
            self._advertised = {
                key for value in raw["routes"] if (key := _capability_key(value)) is not None
            }
            config = raw.get("config") if isinstance(raw, dict) else None
            self._config_writable = bool(
                isinstance(config, dict) and config.get("writable") is True
            )
            self._observed_at = _now_iso()
            self._expires_at = monotonic() + self.ttl_seconds
            self._retry_at = 0.0
            self._last_error = None
            return self._snapshot("available")

    async def require(self, route: RouteName) -> None:
        if route not in FEATURE_ROUTES.values():
            raise PanelError("route_unsupported")
        snapshot = await self.get()
        feature = route.value
        value = snapshot["features"][feature]
        if value is False:
            raise PanelError("route_unsupported")
        if value is None:
            if self._last_error is not None and self._last_error.code != "route_unsupported":
                raise PanelError(self._last_error.code)
            raise PanelError("rcon_unavailable")

    async def require_write(self, action: WriteName) -> None:
        if action not in WRITE_FEATURE_ROUTES.values():
            raise PanelError("action_unsupported")
        snapshot = await self.get()
        if snapshot["state"] != "available":
            if self._last_error is not None and self._last_error.code != "route_unsupported":
                raise PanelError(self._last_error.code)
            raise PanelError("rcon_unavailable")
        if snapshot["features"][action.value] is not True:
            raise PanelError("action_unsupported")

    async def require_advertised(self, method: str, path_template: str) -> None:
        """Require a fresh capability for one code-owned fixed route.

        Callers must pass the template from routes.py, never an HTTP parameter or
        an expanded player/map path. This also prevents a stale capability
        snapshot from authorizing a write after the server build changes.
        """
        method = method.upper()
        if (method, path_template) not in _KNOWN_ROUTES:
            raise PanelError("route_unsupported" if method == "GET" else "action_unsupported")
        snapshot = await self.get()
        if snapshot["state"] != "available":
            if self._last_error is not None and self._last_error.code != "route_unsupported":
                raise PanelError(self._last_error.code)
            raise PanelError("rcon_unavailable")
        key = _capability_key(f"{method} {path_template}")
        if key not in self._advertised:
            raise PanelError("route_unsupported" if method == "GET" else "action_unsupported")
        if (method, path_template) == ("PUT", "/v1/config") and not self._config_writable:
            raise PanelError("write_disabled")

    def invalidate(self) -> None:
        """Force the next check to refresh an outdated advertised route list."""
        self._expires_at = 0.0
        self._retry_at = 0.0

    def _snapshot(self, state: str) -> dict[str, Any]:
        features: dict[str, bool | None] = (
            dict(self._features)
            if self._features is not None
            else {name: None for name in (*FEATURE_ROUTES, *WRITE_FEATURE_ROUTES)}
        )
        snapshot: dict[str, Any] = {
            "features": features,
            "observedAt": self._observed_at,
            "state": state,
        }
        if self._features is not None:
            snapshot["advertisedActions"] = {
                name: (
                    None
                    if state != "available"
                    else _capability_key(f"{method} {path}") in self._advertised
                    and (
                        (method, path) != ("PUT", "/v1/config")
                        or self._config_writable
                    )
                )
                for name, (method, path) in _ADVERTISED_ACTIONS.items()
            }
        return snapshot
