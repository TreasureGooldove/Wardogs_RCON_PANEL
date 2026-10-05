"""Pinned release sources, bounded downloads and manifest fallback."""
import asyncio
from datetime import UTC, datetime
import json
import re
from time import monotonic
from urllib.parse import urlsplit
import httpx

APP_VERSION = "0.3.6"
REPOSITORY = "TreasureGooldove/Wardogs_RCON_PANEL"
GITEE_REPOSITORY = "gooldove/Wardogs_RCON_PANEL"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
SOURCES = {
    "github": {"api": LATEST_API, "page": RELEASES_URL,
               "manifest": f"https://raw.githubusercontent.com/{REPOSITORY}/updates/latest.json"},
    "gitee": {"api": f"https://gitee.com/api/v5/repos/{GITEE_REPOSITORY}/releases/latest",
              "page": f"https://gitee.com/{GITEE_REPOSITORY}/releases",
              "manifest": f"https://gitee.com/{GITEE_REPOSITORY}/raw/updates/latest.json"},
}
MAX_PACKAGE = 40 * 1024 * 1024


def version_tuple(value):
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value) if isinstance(value, str) else None
    return tuple(map(int, match.groups())) if match else None


def trusted_url(url, source, *, redirect=False):
    if not isinstance(url, str) or len(url) > 8192:
        return False
    try:
        p = urlsplit(url)
        if p.scheme != "https" or p.username or p.password or p.port not in (None, 443) or p.fragment:
            return False
    except ValueError:
        return False
    if source == "github":
        return ((p.hostname == "github.com" and p.path.startswith(f"/{REPOSITORY}/releases/download/") and not p.query)
                or (redirect and p.hostname == "release-assets.githubusercontent.com"))
    if source == "gitee":
        return ((p.hostname == "gitee.com" and p.path.startswith(f"/{GITEE_REPOSITORY}/") and not p.query)
                or (redirect and p.hostname in {"gitee.com", "giteeusercontent.com", "files.gitee.com", "foruda.gitee.com", "raw.giteeusercontent.com"}))
    return False


class ReleaseChecker:
    def __init__(self, transport=None):
        self.client = httpx.AsyncClient(transport=transport, timeout=15, follow_redirects=False,
            headers={"Accept": "application/json", "User-Agent": f"Wardogs-RCON-Panel/{APP_VERSION}"})
        self.lock = asyncio.Lock()
        self.cached = {}

    async def close(self):
        await self.client.aclose()

    async def fetch(self, url, limit, *, source=None):
        for _ in range(5):
            async with self.client.stream("GET", url) as response:
                if response.is_redirect and source:
                    target = str(response.url.join(response.headers.get("location", "")))
                    if not trusted_url(target, source, redirect=True):
                        raise ValueError("untrusted_download")
                    url = target
                    continue
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes(65536):
                    if len(data) + len(chunk) > limit:
                        raise ValueError("download_too_large")
                    data.extend(chunk)
                return bytes(data)
        raise ValueError("too_many_redirects")

    async def _source(self, source):
        config = SOURCES[source]
        candidate = None
        status = "unavailable"
        try:
            response = await self.client.get(config["api"])
            if response.status_code == 404:
                status = "no_release"
            elif response.status_code in (403, 429):
                status = "rate_limited"
            elif response.status_code == 200:
                if len(response.content) > 1024 * 1024:
                    raise ValueError("response_too_large")
                data = response.json()
                tag = data.get("tag_name") if isinstance(data, dict) else None
                if version_tuple(tag) and not data.get("draft") and not data.get("prerelease"):
                    candidate = {"version": tag, "source": source, "kind": "release",
                                 "releaseUrl": f'{config["page"]}/tag/{tag}',
                                 "publishedAt": data.get("published_at") or data.get("created_at"),
                                 "url": None, "sha256": None}
                    assets = data.get("assets", [])
                    if isinstance(assets, dict):
                        assets = assets.get("links", [])
                    if isinstance(assets, list):
                        name = f"Wardogs_RCON_PANEL-{tag}.zip"
                        package = next((x for x in assets if isinstance(x, dict) and x.get("name") == name), None)
                        sums = next((x for x in assets if isinstance(x, dict) and x.get("name") == "SHA256SUMS.txt"), None)
                        url = package.get("browser_download_url") if package else None
                        digest = package.get("digest", "") if package else ""
                        if trusted_url(url, source):
                            candidate["url"] = url
                            if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                                candidate["sha256"] = digest[7:]
                            elif sums and trusted_url(sums.get("browser_download_url"), source):
                                content = (await self.fetch(sums["browser_download_url"], 32768, source=source)).decode()
                                match = re.search(r"(?m)^([0-9a-f]{64})\s+\*?" + re.escape(name) + r"\s*$", content)
                                if match:
                                    candidate["sha256"] = match[1]
                    status = "ok"
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        if candidate is None or not candidate.get("sha256"):
            try:
                manifest = json.loads(await self.fetch(config["manifest"], 32768, source=source))
                version = manifest.get("version")
                if (version_tuple(version) and trusted_url(manifest.get("url"), source)
                        and re.fullmatch(r"[0-9a-f]{64}", manifest.get("sha256", ""))
                        and (candidate is None or version_tuple(version) >= version_tuple(candidate["version"]))):
                    candidate = {"version": version, "source": source, "kind": "manifest",
                                 "releaseUrl": config["page"], "publishedAt": manifest.get("publishedAt"),
                                 "url": manifest["url"], "sha256": manifest["sha256"]}
                    status = "ok"
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                pass
        return candidate, status

    async def check(self, refresh=False, source="auto"):
        if source not in ("auto", *SOURCES):
            raise ValueError("invalid_source")
        async with self.lock:
            now = monotonic()
            saved = self.cached.get(source)
            if saved and not refresh and now - saved[0] < 300:
                return {**saved[1], "cached": True}
            names = ("gitee", "github") if source == "auto" else (source,)
            results = await asyncio.gather(*(self._source(name) for name in names))
            candidates = [item for item, _ in results if item]
            latest = max(candidates, key=lambda x: (version_tuple(x["version"]), bool(x["sha256"]), x["source"] == "gitee")) if candidates else None
            statuses = {name: status for name, (_, status) in zip(names, results)}
            result = {"currentVersion": APP_VERSION, "latestVersion": latest["version"] if latest else None,
                      "updateAvailable": bool(latest and version_tuple(latest["version"]) > version_tuple(APP_VERSION)),
                      "releaseUrl": latest["releaseUrl"] if latest else SOURCES[names[0]]["page"],
                      "publishedAt": latest["publishedAt"] if latest else None,
                      "checkedAt": datetime.now(UTC).isoformat(), "cached": False,
                      "status": "ok" if latest else ("no_release" if all(x == "no_release" for x in statuses.values()) else "rate_limited" if "rate_limited" in statuses.values() else "unavailable"),
                      "source": latest["source"] if latest else None, "sources": statuses,
                      "fallback": bool(latest and latest["kind"] == "manifest"),
                      "installable": bool(latest and latest["url"] and latest["sha256"]),
                      "packageUrl": latest["url"] if latest else None,
                      "sha256": latest["sha256"] if latest else None}
            self.cached[source] = (now, result)
            return result
