"""Status API contract against a local HTTPX MockTransport only."""

import asyncio
from collections.abc import Callable

from fastapi import FastAPI, Request
import httpx
import pytest

from app.api.status import router as status_router
from app.config import RconTarget
from app.errors import PanelError, install_error_handlers
from app.rcon.capabilities import CapabilityService
from app.rcon.client import RconClient
from app.rcon.service import ReadService


class FakeAuth:
    def require_admin(self, request: Request) -> object:
        if request.cookies.get("panel_session") != "local-session":
            raise PanelError("not_authenticated")
        return object()


def make_app(
    handler: Callable[[httpx.Request], httpx.Response], *, ttl_seconds: float = 15.0
) -> tuple[FastAPI, RconClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(status_router)
    client = RconClient(
        RconTarget(origin="https://wardogs.invalid", bearer_secret="local-canary"),
        transport=httpx.MockTransport(handler),
    )
    app.state.auth_service = FakeAuth()
    app.state.rcon_client = client
    app.state.capability_service = CapabilityService(client)
    app.state.read_service = ReadService(
        client, app.state.capability_service, ttl_seconds=ttl_seconds
    )
    return app, client


def capabilities(*, supports_status: bool = True) -> httpx.Response:
    return httpx.Response(
        200,
        json={"routes": ["GET /v1/status"] if supports_status else []},
    )


@pytest.mark.asyncio
async def test_status_requires_login_before_any_rcon_request() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return capabilities()

    app, rcon = make_app(handler)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/api/server/status")
        assert response.status_code == 401
        assert response.json()["code"] == "not_authenticated"
        assert sent == []
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_status_filters_fields_and_preserves_nullable_vs_empty() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path == "/v1/capabilities":
            return capabilities()
        return httpx.Response(
            200,
            json={
                "map": "MapAlpha",
                "experiences": [],
                "players": {"current": 4, "max": 80},
                "factionScores": [{"name": "A", "value": 20, "secret": "drop"}],
                "adminPassword": "never-expose",
            },
        )

    app, rcon = make_app(handler)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", cookies={"panel_session": "local-session"}) as client:
            response = await client.get("/api/server/status")
        assert response.status_code == 200
        data = response.json()
        assert data["map"] == "MapAlpha"
        assert data["experiences"] == []
        assert data["lighting"] is None
        assert data["playerCount"] == 4
        assert data["maxPlayers"] == 80
        assert data["factionScores"] == [{"name": "A", "score": 20}]
        assert data["stale"] is False
        assert data["observedAt"].endswith("Z")
        assert "adminPassword" not in data
        assert [(request.method, request.url.path) for request in sent] == [
            ("GET", "/v1/capabilities"),
            ("GET", "/v1/status"),
        ]
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_status_missing_optional_fields_are_null() -> None:
    app, rcon = make_app(
        lambda request: capabilities()
        if request.url.path.endswith("capabilities")
        else httpx.Response(200, json={})
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", cookies={"panel_session": "local-session"}) as client:
            response = await client.get("/api/server/status")
        assert response.status_code == 200
        data = response.json()
        assert data["experiences"] is None
        assert data["factionScores"] is None
        assert data["playerCount"] is None
    finally:
        await rcon.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "http_status"),
    [(404, "route_unsupported", 501), (429, "rcon_rate_limited", 429)],
)
async def test_status_maps_upstream_failures(status: int, code: str, http_status: int) -> None:
    app, rcon = make_app(
        lambda request: capabilities()
        if request.url.path.endswith("capabilities")
        else httpx.Response(status)
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", cookies={"panel_session": "local-session"}) as client:
            response = await client.get("/api/server/status")
        assert response.status_code == http_status
        assert response.json()["code"] == code
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_status_unknown_or_unsupported_capability_stops_upstream_status() -> None:
    for cap_response, expected in (
        (capabilities(supports_status=False), "route_unsupported"),
        (httpx.Response(503), "rcon_unavailable"),
    ):
        sent: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request.url.path)
            return cap_response

        app, rcon = make_app(handler)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", cookies={"panel_session": "local-session"}) as client:
                response = await client.get("/api/server/status")
            assert response.json()["code"] == expected
            assert sent == ["/v1/capabilities"]
        finally:
            await rcon.close()


@pytest.mark.asyncio
async def test_expired_status_returns_explicit_stale_snapshot_after_timeout() -> None:
    status_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_attempts
        if request.url.path.endswith("capabilities"):
            return capabilities()
        status_attempts += 1
        if status_attempts > 1:
            raise httpx.ReadTimeout("local timeout", request=request)
        return httpx.Response(200, json={"map": "OldMap"})

    app, rcon = make_app(handler, ttl_seconds=0.01)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", cookies={"panel_session": "local-session"}) as client:
            first = await client.get("/api/server/status")
            await asyncio.sleep(0.02)
            second = await client.get("/api/server/status")
        assert first.status_code == 200
        assert first.json()["stale"] is False
        assert second.status_code == 200
        assert second.json()["stale"] is True
        assert second.json()["observedAt"] == first.json()["observedAt"]
        assert second.json()["map"] == "OldMap"
    finally:
        await rcon.close()
