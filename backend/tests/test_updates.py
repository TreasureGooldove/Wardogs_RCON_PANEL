import asyncio

import httpx
from fastapi.testclient import TestClient

from app.config import PanelSettings
from app.main import create_app
from app.updates import APP_VERSION, LATEST_API, ReleaseChecker


def test_release_comparison_cache_and_safe_link():
    async def scenario():
        calls = []
        version = ["v0.10.0"]
        def handler(request):
            calls.append(request)
            if str(request.url) != LATEST_API:
                return httpx.Response(404)
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"tag_name": version[0], "draft": False,
                "prerelease": False, "html_url": "https://evil.invalid/", "published_at": None})
        checker = ReleaseChecker(httpx.MockTransport(handler))
        result = await checker.check(source="github")
        assert result["updateAvailable"] is True
        assert result["releaseUrl"].endswith("/Wardogs_RCON_PANEL/releases/tag/v0.10.0")
        assert (await checker.check(source="github"))["cached"] is True
        assert len(calls) == 2
        version[0] = "v0.11.0"
        refreshed = await checker.check(refresh=True, source="github")
        assert refreshed["cached"] is False
        assert refreshed["latestVersion"] == "v0.11.0"
        assert len(calls) == 4
        await checker.close()
    asyncio.run(scenario())


def test_failed_or_unpublished_release_never_claims_current_is_latest():
    async def scenario():
        for response, expected in [
            (httpx.Response(404), "no_release"),
            (httpx.Response(429, headers={"retry-after": "3600"}), "rate_limited"),
            (httpx.Response(200, json={"tag_name": "v9.0.0", "prerelease": True}), "unavailable"),
            (httpx.Response(200, json={"tag_name": "garbage"}), "unavailable"),
            (httpx.Response(503), "unavailable"),
        ]:
            checker = ReleaseChecker(httpx.MockTransport(lambda request: response))
            result = await checker.check(source="github")
            assert result["status"] == expected
            assert result["updateAvailable"] is False
            assert result["latestVersion"] is None
            await checker.close()
    asyncio.run(scenario())


def test_update_endpoint_requires_login(tmp_path):
    app = create_app(PanelSettings(db_path=tmp_path / "panel.sqlite3",
                                   public_origin="https://panel.example.invalid", session_secure=True))
    app.state.auth_service.create_admin("owner", "test-owner-password")
    app.state.release_checker = ReleaseChecker(httpx.MockTransport(lambda request:
        httpx.Response(200, json={"tag_name": f"v{APP_VERSION}", "draft": False, "prerelease": False})))
    with TestClient(app, base_url="https://panel.example.invalid") as client:
        assert client.get("/api/panel/updates").status_code == 401
        assert client.post("/api/auth/login", json={"username": "owner", "password": "test-owner-password"},
            headers={"Origin": "https://panel.example.invalid"}).status_code == 200
        response = client.get("/api/panel/updates")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["currentVersion"] == APP_VERSION
        assert response.json()["updateAvailable"] is False
    asyncio.run(app.state.release_checker.close())
