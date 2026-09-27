"""Bounded Wardogs HTTP client with fixed read and moderation routes."""

import asyncio
import json
import logging
import re
from time import monotonic
from typing import Any

import httpx

from app.config import RconTarget
from app.errors import PanelError

from .routes import RouteName, WriteName, route_for, write_route_for


_LOG = logging.getLogger(__name__)
_STEAM_ID = re.compile(r"^[1-9][0-9]{16}$")
# HTTPX logs full URLs at INFO, including SteamIDs in kick paths.
logging.getLogger("httpx").setLevel(logging.WARNING)


class RconClient:
    def __init__(
        self,
        target: RconTarget | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.target = target
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._semaphore = asyncio.Semaphore(4)

    def _get_client(self) -> httpx.AsyncClient:
        if self.target is None:
            raise PanelError("rcon_unconfigured")
        if self._client is None:
            timeout = httpx.Timeout(
                connect=self.target.connect_timeout,
                read=self.target.read_timeout,
                write=self.target.read_timeout,
                pool=self.target.connect_timeout,
            )
            self._client = httpx.AsyncClient(
                base_url=self.target.origin,
                headers={"Authorization": f"Bearer {self.target.bearer_secret}"},
                timeout=timeout,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
                follow_redirects=False,
                verify=str(self.target.tls_ca_path) if self.target.tls_ca_path else True,
                trust_env=False,
                transport=self._transport,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def request(self, route: RouteName) -> dict[str, Any] | list[Any]:
        """Fetch a named GET route. No caller-selected path, method or query exists."""
        spec = route_for(route)
        client = self._get_client()
        assert self.target is not None
        attempts = self.target.read_retries + 1
        started = monotonic()
        for attempt in range(attempts):
            try:
                async with self._semaphore:
                    async with client.stream("GET", spec.path) as response:
                        decoded = await self._decode(response)
                _LOG.info(
                    "Wardogs RCON query completed: route=%s duration_ms=%d result=ok",
                    spec.name.value,
                    round((monotonic() - started) * 1000),
                )
                return decoded
            except httpx.TimeoutException as exc:
                code = "rcon_timeout"
                last_exc = exc
            except httpx.DecodingError as exc:
                raise PanelError("invalid_upstream") from exc
            except httpx.TransportError as exc:
                code = "rcon_unavailable"
                last_exc = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(min(0.05 * (2**attempt), 0.2))
                continue
            _LOG.warning(
                "Wardogs RCON query failed: route=%s duration_ms=%d result=%s",
                spec.name.value,
                round((monotonic() - started) * 1000),
                code,
            )
            raise PanelError(code) from last_exc
        raise AssertionError("unreachable")

    async def moderate(self, action: WriteName, steam_id: str, reason: str = "") -> None:
        """Submit exactly one fixed POST. An uncertain result must never be retried."""
        if action not in {WriteName.KICK, WriteName.BAN}:
            raise PanelError("action_unsupported")
        spec = write_route_for(action)
        if not isinstance(steam_id, str) or _STEAM_ID.fullmatch(steam_id) is None:
            raise PanelError("invalid_moderation_target")
        if (
            not isinstance(reason, str)
            or len(reason) > 200
            or any(ord(char) < 32 and char not in "\t" for char in reason)
        ):
            raise PanelError("invalid_moderation_reason")
        client = self._get_client()
        path = spec.path.replace("{steamId}", steam_id)
        payload: dict[str, str] = {"reason": reason.strip()} if reason.strip() else {}
        if action is WriteName.BAN:
            payload["steamId"] = steam_id
        started = monotonic()
        try:
            async with self._semaphore:
                async with client.stream(spec.method, path, json=payload) as response:
                    await self._decode_write(response)
        except (httpx.TimeoutException, httpx.TransportError, httpx.DecodingError) as exc:
            _LOG.warning(
                "Wardogs RCON moderation result uncertain: action=%s duration_ms=%d",
                action.value,
                round((monotonic() - started) * 1000),
            )
            raise PanelError("action_uncertain") from exc
        _LOG.info(
            "Wardogs RCON moderation accepted: action=%s duration_ms=%d",
            action.value,
            round((monotonic() - started) * 1000),
        )

    async def _decode_write(self, response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise PanelError("rcon_auth_failed")
        if response.status_code == 404:
            raise PanelError("action_unsupported")
        if response.status_code == 429:
            raise PanelError("rcon_rate_limited")
        if response.status_code >= 500:
            raise PanelError("action_uncertain")
        if not 200 <= response.status_code < 300:
            raise PanelError("action_rejected")

        assert self.target is not None
        limit = self.target.max_response_bytes
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > limit:
                    raise PanelError("action_uncertain")
            except ValueError as exc:
                raise PanelError("action_uncertain") from exc
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            if len(body) + len(chunk) > limit:
                raise PanelError("action_uncertain")
            body.extend(chunk)
        if not body and response.status_code == 204:
            return
        try:
            decoded = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise PanelError("action_uncertain") from exc
        if not isinstance(decoded, dict):
            raise PanelError("action_uncertain")
        if decoded.get("ok") is False or decoded.get("error") not in (None, ""):
            raise PanelError("action_rejected")

    async def _decode(self, response: httpx.Response) -> dict[str, Any] | list[Any]:
        if response.status_code in (401, 403):
            raise PanelError("rcon_auth_failed")
        if response.status_code == 404:
            raise PanelError("route_unsupported")
        if response.status_code == 429:
            raise PanelError("rcon_rate_limited")
        if not 200 <= response.status_code < 300:
            raise PanelError("rcon_unavailable")
        assert self.target is not None
        limit = self.target.max_response_bytes
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > limit:
                    raise PanelError("invalid_upstream")
            except ValueError as exc:
                raise PanelError("invalid_upstream") from exc
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            if len(body) + len(chunk) > limit:
                raise PanelError("invalid_upstream")
            body.extend(chunk)
        try:
            decoded = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise PanelError("invalid_upstream") from exc
        if not isinstance(decoded, (dict, list)):
            raise PanelError("invalid_upstream")
        return decoded
