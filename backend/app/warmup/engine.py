"""Threshold-triggered reserved-slot rewards with persistent cooldown and audit."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
import logging
from time import monotonic

from app.errors import PanelError
from app.rcon.actions import ActionService
from app.rcon.config_doc import read_config, replace_reserved_ids, reserved_ids_from_text, send_config
from app.rcon.routes import RouteName, WriteName, route_for, write_route_for

from .store import WarmupStore


_LOG = logging.getLogger(__name__)
_LOCAL_ZONE = timezone(timedelta(hours=8))  # Current China Standard Time, no tzdata dependency.


def next_detection(config: dict, last: dict | None) -> datetime | None:
    if last is None:
        return None
    started = datetime.fromisoformat(last["started_at"]).astimezone(UTC)
    if config["interval_mode"] == "daily":
        local = started.astimezone(_LOCAL_ZONE)
        return (local.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1)).astimezone(UTC)
    return started + timedelta(hours=config["interval_hours"])


class WarmupEngine:
    def __init__(self, runtime, database, store: WarmupStore) -> None:
        self.runtime = runtime
        self.database = database
        self.store = store
        self._origin = ""
        self._online: dict[str, str] = {}
        self._count = 0
        self._observed_at = 0.0

    def observe(self, origin: str, players: list[dict]) -> None:
        """Accept only complete fresh rosters from the history collector."""
        if origin != self._origin:
            self._origin = origin
            self._online = {}
            self._observed_at = 0.0
        self._online = {item["steamId"]: item["name"] for item in players
                        if item.get("steamId")}
        self._count = len(players)
        self._observed_at = monotonic()

    def status(self, origin: str) -> dict:
        config = self.store.config()
        last = self.store.latest(origin)
        due = next_detection(config, last)
        return {
            "observedPlayers": self._count if self._origin == origin and self._fresh() else None,
            "lastRun": last,
            "nextDetectionAt": due.isoformat() if due else None,
            "attentionRequired": self.store.blocked(origin),
        }

    def _fresh(self) -> bool:
        return bool(self._observed_at and monotonic() - self._observed_at <= 15)

    async def _send_pending(self, origin: str) -> None:
        run = self.store.latest(origin)
        if run is None or run["outcome"] != "accepted":
            return
        if datetime.now(UTC) - datetime.fromisoformat(run["started_at"]).astimezone(UTC) > timedelta(minutes=10):
            return
        if not self._fresh():
            return
        if run["notification_mode"] == "broadcast":
            if run["notification_status"] != "pending":
                return
            spec = write_route_for(WriteName.BROADCAST)
            try:
                await self.runtime.capabilities.require_advertised(spec.method, spec.path)
            except PanelError as exc:
                self.store.notification(run["id"], "rejected:" + exc.code)
                return
            self.store.notification(run["id"], "attempting")
            message = run["notification_text"].replace("{x}", str(run["gift_days"]))
            try:
                await ActionService(self.runtime.client).send(WriteName.BROADCAST, message=message)
            except PanelError as exc:
                self.store.notification(run["id"], "uncertain" if exc.code == "action_uncertain"
                                        else "rejected:" + exc.code)
            except Exception:
                self.store.notification(run["id"], "uncertain")
                _LOG.exception("Warmup broadcast outcome uncertain")
            else:
                self.store.notification(run["id"], "accepted")
            return
        with self.store.db._connect() as db:
            targets = db.execute("""SELECT steam_id FROM warmup_targets
                WHERE run_id=? AND notification_status='pending' ORDER BY steam_id LIMIT 2""",
                (run["id"],)).fetchall()
        for row in targets:
            steam_id = row["steam_id"]
            if steam_id not in self._online:
                self.store.notification(run["id"], "offline", steam_id)
                continue
            spec = write_route_for(WriteName.MESSAGE)
            try:
                await self.runtime.capabilities.require_advertised(spec.method, spec.path)
            except PanelError as exc:
                self.store.notification(run["id"], "rejected:" + exc.code, steam_id)
                continue
            self.store.notification(run["id"], "attempting", steam_id)
            message = run["notification_text"].replace("{x}", str(run["gift_days"]))
            try:
                await ActionService(self.runtime.client).send(
                    WriteName.MESSAGE, steam_id=steam_id, message=message)
            except PanelError as exc:
                self.store.notification(run["id"], "uncertain" if exc.code == "action_uncertain"
                                        else "rejected:" + exc.code, steam_id)
            except Exception:
                self.store.notification(run["id"], "uncertain", steam_id)
                _LOG.exception("Warmup private message outcome uncertain")
            else:
                self.store.notification(run["id"], "accepted", steam_id)

    async def tick(self) -> None:
        async with self.runtime.lock:
            target = self.runtime.target
            if target is None or target.origin != self._origin or not self._fresh():
                return
            origin = target.origin
            config = self.store.config()
            if not config["enabled"] or config["origin"] != origin:
                return
            await self._send_pending(origin)
            if self._count < config["player_threshold"] or self.store.blocked(origin):
                return
            due = next_detection(config, self.store.latest(origin))
            if due is not None and datetime.now(UTC) < due:
                return
            read_spec = route_for(RouteName.CONFIG)
            await self.runtime.capabilities.require_advertised(read_spec.method, read_spec.path)
            document = await read_config(self.runtime.client)
            if not document["writable"]:
                return
            configured = reserved_ids_from_text(document["text"])
            configured_set = set(configured)
            metadata = self.database.reserved_metadata(origin)
            expiry = datetime.now(UTC) + timedelta(days=config["gift_days"])
            targets: list[dict] = []
            skipped = 0
            for steam_id, name in self._online.items():
                previous = metadata.get(steam_id)
                if steam_id in configured_set:
                    if not previous or previous["status"] != "active" or not previous["expiresAt"]:
                        skipped += 1  # Never turn an unmanaged or permanent slot into an expiring one.
                        continue
                    previous_expiry = datetime.fromisoformat(previous["expiresAt"]).astimezone(UTC)
                    if previous_expiry >= expiry:
                        skipped += 1
                        continue
                    action = "renew"
                else:
                    action = "new"
                targets.append({"steam_id": steam_id, "name": name,
                                "action": action, "expires_at": expiry.isoformat()})
            new_ids = [item["steam_id"] for item in targets if item["action"] == "new"]
            if new_ids:
                write_spec = write_route_for(WriteName.CONFIG_APPLY)
                await self.runtime.capabilities.require_advertised(write_spec.method, write_spec.path)
                updated = replace_reserved_ids(document["text"], [*configured, *new_ids])
            run_id = self.store.begin(origin, self._count, config["gift_days"],
                                      config["notification_mode"],
                                      config["notification_text"], targets, skipped)
            if not targets:
                self.store.finish_grant(run_id, origin, [], outcome="no_eligible")
                return
            if new_ids:
                try:
                    await send_config(self.runtime.client, updated, apply=True,
                                      revision=document["revision"])
                except PanelError as exc:
                    self.store.finish_failure(run_id, uncertain=exc.code == "action_uncertain",
                                              detail=exc.code)
                    return
                except Exception:
                    self.store.finish_failure(run_id, uncertain=True, detail="unexpected")
                    _LOG.exception("Warmup reserved-slot write outcome uncertain")
                    return
            try:
                self.store.finish_grant(run_id, origin, targets)
            except Exception:
                self.store.finish_failure(run_id, uncertain=True, detail="metadata_commit_failed")
                _LOG.exception("Warmup metadata commit needs manual review")

    async def run(self) -> None:
        retry_at = 0.0
        while True:
            if monotonic() >= retry_at:
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except PanelError as exc:
                    _LOG.warning("Warmup cycle skipped: %s", exc.code)
                    retry_at = monotonic() + 30
                except Exception:
                    _LOG.exception("Warmup cycle failed")
                    retry_at = monotonic() + 30
            await asyncio.sleep(1)
