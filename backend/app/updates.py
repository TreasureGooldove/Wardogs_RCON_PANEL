"""Read public GitHub releases without credentials or installation side effects."""

import asyncio
from datetime import UTC, datetime
import re
from time import monotonic

import httpx

APP_VERSION = "0.1.0"
REPOSITORY = "TreasureGooldove/Wardogs_RCON_PANEL"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"


def version_tuple(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value)
    return tuple(map(int, match.groups())) if match else None


class ReleaseChecker:
    def __init__(self, transport=None):
        self.client = httpx.AsyncClient(transport=transport, timeout=8, follow_redirects=False,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": f"Wardogs-RCON-Panel/{APP_VERSION}",
                     "X-GitHub-Api-Version": "2026-03-10"})
        self.lock = asyncio.Lock()
        self.cached = None
        self.checked_at = 0.0
        self.retry_at = 0.0

    async def close(self):
        await self.client.aclose()

    async def check(self, refresh=False):
        async with self.lock:
            now = monotonic()
            ttl = 3600 if self.cached and self.cached["status"] == "ok" else 900
            if self.cached and (now < self.retry_at or now - self.checked_at < (60 if refresh else ttl)):
                return {**self.cached, "cached": True}
            result = {"currentVersion": APP_VERSION, "latestVersion": None,
                      "updateAvailable": False, "releaseUrl": RELEASES_URL,
                      "publishedAt": None, "checkedAt": datetime.now(UTC).isoformat(),
                      "status": "unavailable", "cached": False}
            try:
                response = await self.client.get(LATEST_API)
                if response.status_code == 404:
                    result["status"] = "no_release"
                elif response.status_code in (403, 429):
                    result["status"] = "rate_limited"
                    self.retry_at = now + 900
                    for header in ("retry-after", "x-ratelimit-reset"):
                        try:
                            value = int(response.headers[header])
                            delay = value if header == "retry-after" else value - datetime.now(UTC).timestamp()
                            self.retry_at = max(self.retry_at, now + min(max(delay, 0), 86400))
                        except (KeyError, ValueError):
                            pass
                elif response.status_code == 200:
                    data = response.json()
                    tag = data.get("tag_name") if isinstance(data, dict) else None
                    version = version_tuple(tag) if isinstance(tag, str) else None
                    if version and not data.get("draft") and not data.get("prerelease"):
                        result.update(status="ok", latestVersion=tag,
                            updateAvailable=version > version_tuple(APP_VERSION),
                            releaseUrl=f"{RELEASES_URL}/tag/{tag}",
                            publishedAt=data.get("published_at") if isinstance(data.get("published_at"), str) else None)
            except (httpx.HTTPError, ValueError):
                pass
            self.cached, self.checked_at = result, now
            return dict(result)
