"""Read-only, time-bounded public snapshots for the panel API."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Callable

from app.errors import PanelError

from .capabilities import CapabilityService
from .client import RconClient
from .routes import RouteName


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class _Cached:
    value: dict[str, Any]
    stored_at: float


class ReadService:
    def __init__(
        self,
        client: RconClient,
        capabilities: CapabilityService,
        *,
        ttl_seconds: float = 15.0,
        players_ttl_seconds: float = 4.5,
        stale_seconds: float = 300.0,
    ) -> None:
        if ttl_seconds <= 0 or players_ttl_seconds <= 0 or stale_seconds <= 0:
            raise ValueError("snapshot lifetimes must be positive")
        self.client = client
        self.capabilities = capabilities
        self.ttl_seconds = ttl_seconds
        self.players_ttl_seconds = players_ttl_seconds
        self.stale_seconds = stale_seconds
        self._cache: dict[RouteName, _Cached] = {}
        self._lock = asyncio.Lock()
        self.interest = lambda: None
        self.managed = lambda: False
        self.freshness = lambda route: self._ttl(route)

    def publish(self, route, value):
        value={**value,'observedAt':_now_iso(),'stale':False}
        self._cache[route]=_Cached(deepcopy(value),monotonic())

    def invalidate(self, route: RouteName) -> None:
        """Drop a snapshot after a known server-side change."""
        self._cache.pop(route, None)

    def _ttl(self, route: RouteName) -> float:
        return self.players_ttl_seconds if route is RouteName.PLAYERS else self.ttl_seconds

    async def status(self) -> dict[str, Any]:
        from .status import normalize_status

        return await self._read(RouteName.STATUS, normalize_status)

    async def players(self) -> dict[str, Any]:
        from .players import normalize_players

        return await self._read(RouteName.PLAYERS, normalize_players)

    async def rotation(self) -> dict[str, Any]:
        from .rotation import normalize_rotation

        return await self._read(RouteName.ROTATION, normalize_rotation)

    async def catalog(self, kind: str) -> dict[str, Any]:
        from .catalog import normalize_catalog

        try:
            route = {
                "maps": RouteName.MAPS,
                "experiences": RouteName.EXPERIENCES,
                "lightings": RouteName.LIGHTINGS,
            }[kind]
        except KeyError as exc:
            raise PanelError("route_unsupported") from exc
        return await self._read(route, lambda raw: normalize_catalog(raw, kind))

    async def _read(
        self,
        route: RouteName,
        normalize: Callable[[dict[str, Any] | list[Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        await self.capabilities.require(route)
        if route in (RouteName.PLAYERS,RouteName.STATUS):
            self.interest()
        cached = self._cache.get(route)
        now = monotonic()
        if self.managed() and route in (RouteName.PLAYERS,RouteName.STATUS) and cached is not None:
            if now-cached.stored_at>=self.stale_seconds:
                raise PanelError('rcon_unavailable')
            result=deepcopy(cached.value)
            result['stale']=now-cached.stored_at>self.freshness(route)
            return result
        if cached is not None and now - cached.stored_at < self._ttl(route):
            return deepcopy(cached.value)
        async with self._lock:
            cached = self._cache.get(route)
            now = monotonic()
            if cached is not None and now - cached.stored_at < self._ttl(route):
                return deepcopy(cached.value)
            try:
                raw = await self.client.request(route)
                projected = normalize(raw)
            except PanelError as exc:
                if exc.code == "route_unsupported":
                    self.capabilities.invalidate()
                    raise
                if (
                    cached is not None
                    and now - cached.stored_at < self.stale_seconds
                    and exc.code
                    in {
                        "rcon_unavailable",
                        "rcon_timeout",
                        "rcon_rate_limited",
                        "invalid_upstream",
                    }
                ):
                    result = deepcopy(cached.value)
                    result["stale"] = True
                    return result
                raise
            projected["observedAt"] = _now_iso()
            projected["stale"] = False
            self._cache[route] = _Cached(deepcopy(projected), monotonic())
            return projected
