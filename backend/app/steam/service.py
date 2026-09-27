"""Bounded, cached reads of public Steam profile summaries."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import json
import os
import re
from time import monotonic
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from app.errors import PanelError


# The public host accepts standard user keys. Steam's partner host requires a
# publisher key and must not be selected from client input.
STEAM_SUMMARIES_URL = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
STEAM_ID_RE = re.compile(r"^[1-9][0-9]{16}$", re.ASCII)
PROFILE_TTL_SECONDS = 600.0
ERROR_TTL_SECONDS = 30.0
MAX_CACHE_ITEMS = 1000
MAX_INFLIGHT_IDS = 1000
MAX_RESPONSE_BYTES = 262_144
_AVATAR_HOSTS = frozenset({"avatars.steamstatic.com", "steamcdn-a.akamaihd.net"})


@dataclass(frozen=True)
class _Entry:
    profile: dict[str, str | None] | None
    expires_at: float
    error: str | None = None


def _safe_avatar_url(value: Any) -> str | None:
    if not isinstance(value, str) or not 0 < len(value) <= 512:
        return None
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _AVATAR_HOSTS
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or not parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    ):
        return None
    return value


def _project_profile(raw: dict[str, Any], steam_id: str) -> dict[str, str | None]:
    name = raw.get("personaname")
    if not isinstance(name, str) or not name or len(name) > 128:
        name = None
    elif any(ord(char) < 32 or ord(char) == 127 for char in name):
        name = None
    return {
        "steamId": steam_id,
        "personaName": name,
        "avatarUrl": _safe_avatar_url(raw.get("avatarfull"))
        or _safe_avatar_url(raw.get("avatarmedium"))
        or _safe_avatar_url(raw.get("avatar")),
        # Construct this ourselves: a Steam response never controls the link target.
        "profileUrl": f"https://steamcommunity.com/profiles/{steam_id}/",
    }


class SteamProfileService:
    """One application worker's short-lived profile and failure cache."""

    def __init__(
        self,
        *,
        key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._key = key
        self._clock = clock
        self._cache: OrderedDict[str, _Entry] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[_Entry]] = {}
        self._lock = asyncio.Lock()
        self._next_upstream_at = 0.0
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0),
            follow_redirects=False,
            trust_env=False,
        )

    @classmethod
    def from_environment(cls) -> SteamProfileService:
        return cls(key=os.environ.get("STEAM_WEB_API_KEY"))

    def _usable_key(self) -> str | None:
        key = self._key
        if (
            not isinstance(key, str)
            or not 0 < len(key) <= 256
            or key != key.strip()
            or any(ord(char) < 33 or ord(char) > 126 for char in key)
        ):
            return None
        return key

    async def close(self) -> None:
        await self._client.aclose()

    async def profiles(self, steam_ids: list[str]) -> list[dict[str, str | None]]:
        if not 1 <= len(steam_ids) <= 100 or any(
            not isinstance(steam_id, str) or STEAM_ID_RE.fullmatch(steam_id) is None
            for steam_id in steam_ids
        ):
            raise PanelError("invalid_steam_ids")
        if self._usable_key() is None:
            raise PanelError("steam_unconfigured")

        unique_ids = list(dict.fromkeys(steam_ids))
        to_fetch: list[str] = []
        futures: dict[str, asyncio.Future[_Entry]] = {}
        async with self._lock:
            now = self._clock()
            needed = [
                steam_id for steam_id in unique_ids
                if not ((entry := self._cache.get(steam_id)) and entry.expires_at > now)
                and steam_id not in self._inflight
            ]
            if len(self._inflight) + len(needed) > MAX_INFLIGHT_IDS:
                raise PanelError("steam_rate_limited")
            # Reserve the next upstream slot while holding the same lock used
            # for per-ID coalescing. Cache hits and in-flight joins bypass it.
            if needed and now < self._next_upstream_at:
                raise PanelError("steam_rate_limited")
            if needed:
                self._next_upstream_at = now + 1.0
            for steam_id in unique_ids:
                entry = self._cache.get(steam_id)
                if entry is not None and entry.expires_at > now:
                    self._cache.move_to_end(steam_id)
                    future: asyncio.Future[_Entry] = asyncio.get_running_loop().create_future()
                    future.set_result(entry)
                else:
                    future = self._inflight.get(steam_id)
                    if future is None:
                        future = asyncio.get_running_loop().create_future()
                        self._inflight[steam_id] = future
                        to_fetch.append(steam_id)
                futures[steam_id] = future

        if to_fetch:
            try:
                fetched = await self._fetch_batch(to_fetch)
                expires_at = self._clock() + PROFILE_TTL_SECONDS
                entries = {
                    steam_id: _Entry(fetched.get(steam_id), expires_at)
                    for steam_id in to_fetch
                }
            except PanelError as exc:
                expires_at = self._clock() + ERROR_TTL_SECONDS
                entries = {steam_id: _Entry(None, expires_at, exc.code) for steam_id in to_fetch}
            except asyncio.CancelledError:
                await asyncio.shield(self._finish_batch(
                    {steam_id: _Entry(None, self._clock() + ERROR_TTL_SECONDS, "steam_unavailable")
                     for steam_id in to_fetch}
                ))
                raise
            await asyncio.shield(self._finish_batch(entries))

        resolved = await asyncio.gather(*(asyncio.shield(futures[steam_id]) for steam_id in unique_ids))
        by_id = dict(zip(unique_ids, resolved, strict=True))
        for entry in resolved:
            if entry.error is not None:
                raise PanelError(entry.error)
        return [
            by_id[steam_id].profile or {
                "steamId": steam_id,
                "personaName": None,
                "avatarUrl": None,
                "profileUrl": f"https://steamcommunity.com/profiles/{steam_id}/",
            }
            for steam_id in steam_ids
        ]

    async def _finish_batch(self, entries: dict[str, _Entry]) -> None:
        async with self._lock:
            for steam_id, entry in entries.items():
                self._cache[steam_id] = entry
                self._cache.move_to_end(steam_id)
                while len(self._cache) > MAX_CACHE_ITEMS:
                    self._cache.popitem(last=False)
                future = self._inflight.pop(steam_id)
                future.set_result(entry)

    async def _fetch_batch(self, steam_ids: list[str]) -> dict[str, dict[str, str | None]]:
        key = self._usable_key()
        if key is None:
            raise PanelError("steam_unconfigured")
        try:
            async with self._client.stream(
                "GET",
                STEAM_SUMMARIES_URL,
                params={"steamids": ",".join(steam_ids)},
                headers={"x-webapi-key": key, "accept": "application/json"},
            ) as response:
                if response.status_code in (401, 403):
                    raise PanelError("steam_auth_failed")
                if response.status_code == 429:
                    raise PanelError("steam_rate_limited")
                if response.status_code != 200:
                    raise PanelError("steam_unavailable" if response.status_code >= 500 else "steam_bad_response")
                parts: list[bytes] = []
                size = 0
                async for part in response.aiter_bytes(chunk_size=16_384):
                    size += len(part)
                    if size > MAX_RESPONSE_BYTES:
                        raise PanelError("steam_bad_response")
                    parts.append(part)
        except httpx.TimeoutException as exc:
            raise PanelError("steam_timeout") from exc
        except httpx.RequestError as exc:
            raise PanelError("steam_unavailable") from exc

        try:
            body = json.loads(b"".join(parts).decode("utf-8"))
            raw_players = body["response"]["players"]
        except (UnicodeError, ValueError, RecursionError, KeyError, TypeError) as exc:
            raise PanelError("steam_bad_response") from exc
        if not isinstance(raw_players, list) or len(raw_players) > 100:
            raise PanelError("steam_bad_response")
        requested = set(steam_ids)
        projected: dict[str, dict[str, str | None]] = {}
        for raw in raw_players:
            if not isinstance(raw, dict):
                raise PanelError("steam_bad_response")
            steam_id = raw.get("steamid")
            if not isinstance(steam_id, str) or STEAM_ID_RE.fullmatch(steam_id) is None:
                raise PanelError("steam_bad_response")
            if steam_id in requested and steam_id not in projected:
                projected[steam_id] = _project_profile(raw, steam_id)
        return projected
