"""One server target, atomically replaceable after an authenticated settings update."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import secrets
from time import monotonic
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
import httpx

from app.config import PanelSettings, RconTarget, _is_private_http_host, _valid_origin
from app.errors import PanelError
from app.storage.db import Database, ServerSettingsRecord

from .capabilities import CapabilityService
from .client import RconClient
from .routes import RouteName
from .service import ReadService


_LOG = logging.getLogger(__name__)


def _fernet(key: str | None) -> Fernet | None:
    if not key:
        return None
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return None


class RconRuntime:
    """All RCON work and target replacement share one lock.

    The public route facades below hold the lock for the whole upstream call.
    A settings update cannot therefore leave an in-flight route using a closed
    client, and a new route cannot see an old cache after the target changes.
    """

    def __init__(
        self,
        settings: PanelSettings,
        database: Database,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.lock = asyncio.Lock()
        self.watch_until = 0.0
        self.collector_enabled = False
        self.cadence = (30.0,30.0)
        self._transport = transport
        self._cipher = _fernet(settings.config_key)
        self._record = database.get_server_settings()
        self._connection_state = database.connection_control()
        self.paused = self._connection_state['paused']
        self._stop_generation = 0
        target = self._target_from_record(self._record) if self._record else settings.rcon_target
        self.client, self.capabilities, self.read_service = self._services(target)
        self.target_revision = secrets.token_urlsafe(24)

    def _services(
        self, target: RconTarget | None
    ) -> tuple[RconClient, CapabilityService, ReadService]:
        client = RconClient(target, transport=self._transport)
        client.connection_guard = self.ensure_running
        capabilities = CapabilityService(client, ttl_seconds=3600)
        reads = ReadService(client, capabilities)
        reads.interest = self.touch_interest
        reads.managed = lambda: self.collector_enabled
        reads.freshness = lambda route: self.cadence[0 if route is RouteName.PLAYERS else 1]*2+1
        return client, capabilities, reads

    def touch_interest(self):
        self.watch_until = monotonic()+15

    def ensure_running(self):
        if self.paused:
            raise PanelError('rcon_stopped')

    def connection_state(self):
        return {**self._connection_state, 'paused': self.paused,
                'configured': self.client.target is not None,
                'targetRevision': self.target_revision}

    async def stop_connection(self):
        # Never wait for the RCON lock: an automation may hold it during I/O.
        self.paused = True
        self._stop_generation += 1
        self.watch_until = 0.
        self.target_revision = secrets.token_urlsafe(24)
        self._connection_state = self.database.set_connection_paused(True)
        try:
            await asyncio.wait_for(self.client.close(), timeout=1.)
        except Exception:
            _LOG.warning('RCON stop latch is active; connection cleanup incomplete')
        return self.connection_state()

    async def resume_connection(self):
        generation = self._stop_generation
        async with self.lock:
            if generation != self._stop_generation:
                raise PanelError('stale_server_target')
            if not self.paused:
                return self.connection_state()
            old_client = self.client
            await old_client.close()
            if generation != self._stop_generation:
                raise PanelError('stale_server_target')
            services = self._services(old_client.target)
            self._connection_state = self.database.set_connection_paused(False)
            self.client, self.capabilities, self.read_service = services
            self.target_revision = secrets.token_urlsafe(24)
            self.paused = False
            return self.connection_state()

    def _target_from_record(self, record: ServerSettingsRecord) -> RconTarget | None:
        if self._cipher is None:
            return None
        try:
            bearer = self._cipher.decrypt(record.bearer_ciphertext.encode("ascii")).decode("utf-8")
            scheme, host = _valid_origin(record.origin, allow_http=True)
            public_http = scheme == "http" and not _is_private_http_host(host)
            if public_http and (
                not record.allow_public_http or not self.settings.allow_public_http_rcon
            ):
                return None
            return RconTarget(
                origin=record.origin,
                bearer_secret=bearer,
                allow_private_http=True,
                allow_public_http=public_http,
            )
        except (InvalidToken, UnicodeError, ValueError):
            # A lost/changed key or invalid stored target must never cause a call.
            return None

    def view_unlocked(self) -> dict[str, Any]:
        """Call while holding lock when reading alongside a settings update."""
        if self._record is not None:
            return {
                "name": self._record.name,
                "origin": self._record.origin,
                "hasBearer": bool(self._record.bearer_ciphertext),
                "allowPublicHttp": self._record.allow_public_http,
                "publicHttpRconAllowed": self.settings.allow_public_http_rcon,
                "configured": self.client.target is not None,
                "updatedAt": self._record.updated_at.isoformat().replace("+00:00", "Z"),
            }
        target = self.settings.rcon_target
        return {
            "name": "Wardogs 服务器",
            "origin": target.origin if target else "",
            "hasBearer": target is not None,
            "allowPublicHttp": target.allow_public_http if target else False,
            "publicHttpRconAllowed": self.settings.allow_public_http_rcon,
            "configured": self.client.target is not None,
            "updatedAt": None,
        }

    async def view(self) -> dict[str, Any]:
        async with self.lock:
            return self.view_unlocked()

    async def save(
        self,
        *,
        name: str,
        origin: str,
        bearer: str | None,
        allow_public_http: bool,
    ) -> dict[str, Any]:
        async with self.lock:
            if self._cipher is None:
                raise PanelError("settings_key_unavailable")
            name = name.strip()
            if not name or len(name) > 64 or not origin or len(origin) > 2048:
                raise PanelError("invalid_settings")
            try:
                scheme, host = _valid_origin(origin, allow_http=True)
            except ValueError as exc:
                raise PanelError("invalid_settings") from exc
            is_public_http = scheme == "http" and not _is_private_http_host(host)
            if is_public_http and not allow_public_http:
                raise PanelError("invalid_settings")
            if is_public_http and not self.settings.allow_public_http_rcon:
                raise PanelError("settings_public_http_disabled")
            if allow_public_http and not is_public_http:
                raise PanelError("invalid_settings")
            current_origin = self._record.origin if self._record else (
                self.settings.rcon_target.origin if self.settings.rcon_target else ""
            )
            new_bearer = bearer if bearer else None
            if origin != current_origin and new_bearer is None:
                raise PanelError("settings_bearer_required")
            secret = new_bearer or (
                self.client.target.bearer_secret if self.client.target else None
            )
            if secret is None:
                raise PanelError("settings_bearer_required")
            try:
                target = RconTarget(
                    origin=origin,
                    bearer_secret=secret,
                    allow_private_http=True,
                    allow_public_http=is_public_http,
                )
            except ValueError as exc:
                raise PanelError("invalid_settings") from exc
            new_client, new_capabilities, new_reads = self._services(target)
            ciphertext = self._cipher.encrypt(secret.encode("utf-8")).decode("ascii")
            record = self.database.put_server_settings(
                name,
                origin,
                ciphertext,
                is_public_http,
                datetime.now(timezone.utc),
            )
            old_client = self.client
            self.client = new_client
            self.capabilities = new_capabilities
            self.read_service = new_reads
            self._record = record
            self.target_revision = secrets.token_urlsafe(24)
            try:
                await old_client.close()
            except Exception:
                # The replacement is already persisted and active. Do not make
                # callers believe the save failed and encourage a blind retry.
                _LOG.error("Previous RCON client cleanup failed")
            return self.view_unlocked()

    async def close(self) -> None:
        async with self.lock:
            await self.client.close()

    @property
    def target(self) -> RconTarget | None:
        return None if self.paused else self.client.target

    async def request(self, route: RouteName) -> dict[str, Any] | list[Any]:
        async with self.lock:
            return await self.client.request(route)

    def invalidate_players_unlocked(self) -> None:
        """Call after a successful moderation action while holding lock."""
        self.read_service.invalidate(RouteName.PLAYERS)


class RuntimeReadService:
    """Stable app-state facade for existing GET routes."""

    def __init__(self, runtime: RconRuntime) -> None:
        self.runtime = runtime

    async def status(self) -> dict[str, Any]:
        async with self.runtime.lock:
            self.runtime.ensure_running()
            return await self.runtime.read_service.status()

    async def players(self) -> dict[str, Any]:
        async with self.runtime.lock:
            self.runtime.ensure_running()
            snapshot = await self.runtime.read_service.players()
            snapshot["targetRevision"] = self.runtime.target_revision
            return snapshot

    async def rotation(self) -> dict[str, Any]:
        async with self.runtime.lock:
            return await self.runtime.read_service.rotation()

    async def catalog(self, kind: str) -> dict[str, Any]:
        async with self.runtime.lock:
            self.runtime.ensure_running()
            return await self.runtime.read_service.catalog(kind)


class RuntimeCapabilityService:
    def __init__(self, runtime: RconRuntime) -> None:
        self.runtime = runtime

    async def get(self) -> dict[str, Any]:
        async with self.runtime.lock:
            self.runtime.ensure_running()
            return await self.runtime.capabilities.get()
