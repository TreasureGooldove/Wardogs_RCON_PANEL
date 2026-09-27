"""Cold-start-safe automatic private rules messages for newly observed players."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from string import Formatter
from time import monotonic
from uuid import uuid4

from app.errors import PanelError
from app.rcon.actions import ActionService
from app.rcon.players import normalize_players
from app.rcon.routes import RouteName, WriteName, write_route_for
from app.rcon.runtime import RconRuntime

from .store import RulesStore


_LOG = logging.getLogger(__name__)


def validate_template(value: str, *, required: bool) -> str:
    value = value.strip()
    if len(value) > 2000 or (required and not value) or any(
        (ord(char) < 32 and char not in "\r\n\t") or ord(char) == 127 for char in value
    ):
        raise ValueError("invalid rules template")
    for _, field, format_spec, conversion in Formatter().parse(value):
        if field is not None and (field not in {"name", "steamid", "count"} or format_spec or conversion):
            raise ValueError("invalid rules placeholder")
    return value


def render_parts(template: str, name: str, steam_id: str, count: int) -> list[str]:
    rendered = template.format(name=name, steamid=steam_id, count=count)
    rendered = " ".join(rendered.split())
    if len(rendered) > 2400:
        raise ValueError("rendered rules too long")
    return [rendered[index:index + 200] for index in range(0, len(rendered), 200)]


@dataclass
class Pending:
    name: str
    due: float
    stage: int
    count: int
    batch_id: str


class RulesEngine:
    def __init__(self, runtime: RconRuntime, store: RulesStore) -> None:
        self.runtime = runtime
        self.store = store
        self._origin = ""
        self._baseline: set[str] | None = None
        self._baseline_at = 0.0
        self._online: dict[str, str] = {}
        self._online_at = 0.0
        self._pending: dict[str, Pending] = {}
        self._limit_at = 0.0
        self._limit_count = 0

    def reset(self) -> None:
        self._origin = ""
        self._baseline = None
        self._baseline_at = 0.0
        self._online = {}
        self._online_at = 0.0
        self._pending.clear()
        self._limit_count = 0

    def observe(self, origin: str, players: list[dict]) -> None:
        """Called only with a fresh complete player response under runtime.lock."""
        config = self.store.config()
        if not config["enabled"] or config["origin"] != origin:
            self.reset()
            return
        now = monotonic()
        current = {p["steamId"]: p["name"] for p in players if p.get("steamId")}
        if self._origin != origin:
            self.reset()
            self._origin = origin
        self._online = current
        self._online_at = now
        for sid in tuple(self._pending):
            if sid not in current:
                del self._pending[sid]
        if self._baseline is None or now - self._baseline_at > 90:
            self._baseline = set(current)
            self._baseline_at = now
            self._pending.clear()
            return
        fresh = set(current) - self._baseline
        self._baseline = set(current)
        self._baseline_at = now
        for sid in fresh:
            if sid in self._pending:
                continue
            last_attempt = self.store.recent_first_attempt(origin, sid)
            if last_attempt is not None and (
                datetime.now(UTC) - last_attempt.astimezone(UTC)
            ).total_seconds() < config["cooldown_minutes"] * 60:
                continue
            self._pending[sid] = Pending(
                current[sid], now + config["delay_seconds"], 1,
                len(current), str(uuid4()),
            )

    def status(self) -> dict:
        return {"pending": len(self._pending), "baselineReady": self._baseline is not None,
                "onlineObserved": len(self._online), "lastPlayerSnapshotFresh":
                bool(self._online_at and monotonic() - self._online_at <= 15)}

    async def _send_stage(self, origin: str, sid: str, pending: Pending, template: str) -> bool:
        parts = render_parts(template, pending.name, sid, pending.count)
        if not parts:
            return True
        spec = write_route_for(WriteName.MESSAGE)
        await self.runtime.capabilities.require_advertised(spec.method, spec.path)
        for index, message in enumerate(parts, 1):
            if monotonic() - self._online_at > 15 or sid not in self._online:
                return False
            # Reserve the attempt before the write. A crash or timeout never auto-retries it.
            delivery_id = await asyncio.to_thread(
                self.store.begin, origin, sid, pending.name, pending.batch_id, pending.stage, index,
            )
            try:
                await ActionService(self.runtime.client).send(
                    WriteName.MESSAGE, steam_id=sid, message=message,
                )
            except PanelError as exc:
                await asyncio.to_thread(
                    self.store.finish, delivery_id,
                    "uncertain" if exc.code == "action_uncertain" else "rejected", exc.code,
                )
                return False
            except Exception:
                await asyncio.to_thread(self.store.finish, delivery_id, "uncertain", "unexpected")
                _LOG.exception("Rules message outcome uncertain")
                return False
            await asyncio.to_thread(self.store.finish, delivery_id, "accepted")
            if index < len(parts):
                await asyncio.sleep(0.3)
        return True

    async def send_manual(self, sid: str, revision: str) -> dict:
        async with self.runtime.lock:
            target = self.runtime.target
            config = self.store.config()
            if target is None or revision != self.runtime.target_revision:
                raise PanelError("stale_server_target")
            if config["origin"] != target.origin:
                raise PanelError("stale_server_target")
            await self.runtime.capabilities.require(RouteName.PLAYERS)
            roster = normalize_players(await self.runtime.client.request(RouteName.PLAYERS))["players"]
            online = {player["steamId"]: player["name"] for player in roster if player.get("steamId")}
            if sid not in online:
                raise PanelError("player_not_online")
            self._online = online
            self._online_at = monotonic()
            self._pending.pop(sid, None)
            pending = Pending(online[sid], monotonic(), 1, len(online), str(uuid4()))
            first = await self._send_stage(target.origin, sid, pending, config["first_text"])
            if not first:
                return {"firstSent": False, "secondSent": None}
            if first and config["second_text"]:
                await asyncio.sleep(config["gap_seconds"])
                roster = normalize_players(await self.runtime.client.request(RouteName.PLAYERS))["players"]
                self._online = {
                    player["steamId"]: player["name"] for player in roster if player.get("steamId")
                }
                self._online_at = monotonic()
                if sid not in self._online:
                    return {"firstSent": True, "secondSent": False}
                second = Pending(pending.name, monotonic(), 2, pending.count, pending.batch_id)
                return {"firstSent": True, "secondSent": await self._send_stage(
                    target.origin, sid, second, config["second_text"],
                )}
            return {"firstSent": True, "secondSent": None}

    async def tick(self) -> None:
        async with self.runtime.lock:
            target = self.runtime.target
            config = self.store.config()
            if target is None or not config["enabled"] or config["origin"] != target.origin:
                return
            now = monotonic()
            if not self._online_at or now - self._online_at > 15:
                return
            if now - self._limit_at >= 5:
                self._limit_at = now
                self._limit_count = 0
            due = sorted(((sid, item) for sid, item in self._pending.items() if item.due <= now),
                         key=lambda entry: entry[1].due)
            for sid, pending in due:
                if sid not in self._online:
                    self._pending.pop(sid, None)
                    continue
                if pending.stage == 1 and self._limit_count >= config["max_per_round"]:
                    continue
                self._pending.pop(sid, None)
                if pending.stage == 1:
                    self._limit_count += 1
                template = config["first_text"] if pending.stage == 1 else config["second_text"]
                try:
                    accepted = await self._send_stage(target.origin, sid, pending, template)
                except PanelError as exc:
                    _LOG.warning("Rules message skipped: %s", exc.code)
                    accepted = False
                if accepted and pending.stage == 1 and config["second_text"]:
                    self._pending[sid] = Pending(pending.name, monotonic() + config["gap_seconds"],
                                                 2, pending.count, pending.batch_id)

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOG.exception("Rules sender cycle failed")
            await asyncio.sleep(0.5)
