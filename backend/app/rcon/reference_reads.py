"""Fixed, bounded extra reads used by the Wardogs reference-console views."""

import asyncio
from datetime import UTC, datetime
import logging
import re
from typing import Any
from urllib.parse import quote

import httpx

from app.errors import PanelError

from .client import RconClient
from .routes import RouteName, route_for


_LOG = logging.getLogger(__name__)
_STEAM_ID = re.compile(r"^[1-9][0-9]{16}$")
_MAP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_ .-]{0,95}$")
AUDIT_LIMITS = frozenset({25, 50, 100, 200})
_EXTRA_ROUTES = frozenset({
    RouteName.BANS, RouteName.AUDIT,
    RouteName.MAP_EXPERIENCES, RouteName.MAP_ALTERNATORS,
})


def valid_map_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or _MAP_ID.fullmatch(value) is None
        or value in {".", ".."}
    ):
        raise PanelError("invalid_selection")
    return value


def _text(value: Any, *, max_length: int = 8192) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        raise PanelError("invalid_upstream")
    return value


def _list(raw: dict[str, Any] | list[Any], key: str) -> list[Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get(key), list):
        raise PanelError("invalid_upstream")
    return raw[key]


def project_bans(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    result = []
    for item in _list(raw, "bans"):
        if not isinstance(item, dict):
            raise PanelError("invalid_upstream")
        steam_id = item.get("steamId")
        if not isinstance(steam_id, str) or re.fullmatch(r"[0-9]{17}", steam_id) is None:
            raise PanelError("invalid_upstream")
        result.append({
            "steamId": steam_id,
            "bannedAtUtc": _text(item.get("bannedAtUtc"), max_length=64),
            "bannedBy": _text(item.get("bannedBy"), max_length=128),
            "reason": _text(item.get("reason"), max_length=512),
        })
    return {"bans": result}


def project_audit(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    result = []
    for item in _list(raw, "entries"):
        if not isinstance(item, dict):
            raise PanelError("invalid_upstream")
        result.append({
            "timestampUtc": _text(item.get("timestampUtc"), max_length=64),
            "peer": _text(item.get("peer"), max_length=128),
            "sessionId": _text(item.get("sessionId"), max_length=128),
            "event": _text(item.get("event"), max_length=128),
            "detail": _text(item.get("detail")),
        })
    return {"entries": result}


def project_map_experiences(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    values = _list(raw, "experiences")
    if any(not isinstance(value, str) or not value or len(value) > 128 for value in values):
        raise PanelError("invalid_upstream")
    return {"experiences": values, "items": [{"id": value, "label": value} for value in values]}


def project_map_alternators(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    result = []
    for item in _list(raw, "alternators"):
        if not isinstance(item, dict):
            raise PanelError("invalid_upstream")
        tag = _text(item.get("tag"), max_length=128)
        if not tag:
            raise PanelError("invalid_upstream")
        result.append({"tag": tag, "displayName": _text(item.get("displayName"), max_length=128) or tag})
    return {
        "alternators": result,
        "items": [{"id": item["tag"], "label": item["displayName"]} for item in result],
    }


def observed() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ReferenceReadService:
    def __init__(self, client: RconClient) -> None:
        self.client = client

    async def fetch(
        self, route: RouteName, *, map_id: str | None = None, audit_limit: int | None = None
    ) -> dict[str, Any]:
        if route not in _EXTRA_ROUTES:
            raise PanelError("route_unsupported")
        spec = route_for(route)
        if route in {RouteName.MAP_EXPERIENCES, RouteName.MAP_ALTERNATORS}:
            if map_id is None:
                raise PanelError("invalid_selection")
            path = spec.path.replace("{id}", quote(valid_map_id(map_id), safe=""))
        else:
            if map_id is not None:
                raise PanelError("invalid_selection")
            path = spec.path
        if route is RouteName.AUDIT:
            if audit_limit not in AUDIT_LIMITS:
                raise PanelError("invalid_selection")
            params = {"limit": str(audit_limit)}
        else:
            if audit_limit is not None:
                raise PanelError("invalid_selection")
            params = None
        raw = await self._get(route, path, params)
        projector = {
            RouteName.BANS: project_bans,
            RouteName.AUDIT: project_audit,
            RouteName.MAP_EXPERIENCES: project_map_experiences,
            RouteName.MAP_ALTERNATORS: project_map_alternators,
        }[route]
        return {**projector(raw), "observedAt": observed(), "stale": False}

    async def _get(
        self, route: RouteName, path: str, params: dict[str, str] | None
    ) -> dict[str, Any] | list[Any]:
        self.client._get_client()
        assert self.client.target is not None
        attempts = self.client.target.read_retries + 1
        for attempt in range(attempts):
            try:
                async with self.client.exchange("GET", path, params=params) as response:
                    return await self.client._decode(response)
            except httpx.DecodingError as exc:
                raise PanelError("invalid_upstream") from exc
            except httpx.TimeoutException as exc:
                code = "rcon_timeout"
                last_exc = exc
            except httpx.TransportError as exc:
                code = "rcon_unavailable"
                last_exc = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(min(0.05 * (2**attempt), 0.2))
                continue
            _LOG.warning("Wardogs RCON extra read failed: route=%s result=%s", route.value, code)
            raise PanelError(code) from last_exc
        raise AssertionError("unreachable")
