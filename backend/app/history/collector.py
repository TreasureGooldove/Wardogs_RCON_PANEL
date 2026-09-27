"""One background collector; upstream reads only, without stale cache reuse."""

from __future__ import annotations

import asyncio
import logging

from app.rcon.players import normalize_players
from app.rcon.routes import RouteName
from app.rcon.runtime import RconRuntime
from app.rcon.status import normalize_status
from app.rules.engine import RulesEngine

from .store import HistoryStore


_LOG = logging.getLogger(__name__)


class HistoryCollector:
    def __init__(self, runtime: RconRuntime, store: HistoryStore,
                 rules: RulesEngine | None = None, interval: float = 5.0) -> None:
        self.runtime = runtime
        self.store = store
        self.interval = interval
        self.rules = rules
        self.last_error: str | None = None

    async def sample(self) -> bool:
        async with self.runtime.lock:
            if self.runtime.target is None:
                return False
            origin = self.runtime.target.origin
            await self.runtime.capabilities.require(RouteName.STATUS)
            await self.runtime.capabilities.require(RouteName.PLAYERS)
            status = normalize_status(await self.runtime.client.request(RouteName.STATUS))
            players = normalize_players(await self.runtime.client.request(RouteName.PLAYERS))
            after = normalize_status(await self.runtime.client.request(RouteName.STATUS))
            if (
                status.get("map") != after.get("map")
                or status.get("experiences") != after.get("experiences")
                or (status.get("rotation") or {}).get("nowIndex") != (after.get("rotation") or {}).get("nowIndex")
            ):
                # The roster may belong to either match; wait for the next full pair.
                return False
            # Keep target replacement ordered with the matching database sample.
            await asyncio.to_thread(self.store.record, origin, after, players)
            if self.rules is not None:
                self.rules.observe(origin, players["players"])
        return True

    async def run(self) -> None:
        delay = self.interval
        while True:
            try:
                await self.sample()
                self.last_error = None
                delay = self.interval
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = type(exc).__name__
                _LOG.warning("History collection failed: %s", self.last_error)
                delay = min(max(delay * 2, self.interval), 30.0)
            await asyncio.sleep(delay)
