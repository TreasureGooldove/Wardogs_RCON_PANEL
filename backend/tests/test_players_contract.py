"""Online-player contract, with no real Wardogs connection."""

from collections.abc import Callable

from fastapi import FastAPI, Request
import httpx
import pytest

from app.api.players import router as players_router
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


def make_app(handler: Callable[[httpx.Request], httpx.Response]) -> tuple[FastAPI, RconClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(players_router)
    rcon = RconClient(
        RconTarget(origin="https://wardogs.invalid", bearer_secret="local-canary"),
        transport=httpx.MockTransport(handler),
    )
    app.state.auth_service = FakeAuth()
    app.state.read_service = ReadService(rcon, CapabilityService(rcon))
    return app, rcon


def cap() -> httpx.Response:
    return httpx.Response(200, json={"routes": ["/v1/players"]})


@pytest.mark.asyncio
async def test_duplicate_names_keep_distinct_steam_ids_and_optional_nulls() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path.endswith("capabilities"):
            return cap()
        return httpx.Response(
            200,
            json={
                "players": [
                    {"steamId": "76561198000000001", "name": "same", "kills": 0, "cash": "bad", "secret": "drop"},
                    {"steamId": "76561198000000002", "name": "same", "faction": "blue", "pingMs": 22},
                    {"steamId": None, "name": "无平台 ID", "faction": "GRN"},
                ]
            },
        )

    app, rcon = make_app(handler)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", cookies={"panel_session": "local-session"}) as client:
            response = await client.get("/api/server/players")
        assert response.status_code == 200
        data = response.json()
        assert [item["steamId"] for item in data["players"]] == [
            "76561198000000001",
            "76561198000000002",
            None,
        ]
        assert [item["name"] for item in data["players"]] == ["same", "same", "无平台 ID"]
        assert data["players"][0]["kills"] == 0
        assert data["players"][0]["deaths"] is None
        assert data["players"][0]["cash"] is None
        assert data["players"][1]["pingMs"] == 22
        assert "secret" not in str(data)
        assert data["observedAt"].endswith("Z")
        assert data["stale"] is False
        assert [(request.method, request.url.path) for request in sent] == [
            ("GET", "/v1/capabilities"),
            ("GET", "/v1/players"),
        ]
    finally:
        await rcon.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        {"players": [{"steamId": "06561198000000001", "name": "invalid"}]},
        {},
        {"players": "not-a-list"},
    ],
)
async def test_bad_identity_or_missing_whole_list_is_invalid_upstream(raw: dict) -> None:
    app, rcon = make_app(
        lambda request: cap()
        if request.url.path.endswith("capabilities")
        else httpx.Response(200, json=raw)
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", cookies={"panel_session": "local-session"}) as client:
            response = await client.get("/api/server/players")
        assert response.status_code == 502
        assert response.json()["code"] == "invalid_upstream"
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_explicit_empty_player_list_is_valid() -> None:
    app, rcon = make_app(
        lambda request: cap()
        if request.url.path.endswith("capabilities")
        else httpx.Response(200, json={"players": []})
    )
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test", cookies={"panel_session": "local-session"}) as client:
            response = await client.get("/api/server/players")
        assert response.status_code == 200
        assert response.json()["players"] == []
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_unauthenticated_players_never_contacts_rcon() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return cap()

    app, rcon = make_app(handler)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/api/server/players")
        assert response.status_code == 401
        assert sent == []
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_players_cache_expires_within_five_second_poll_window(monkeypatch) -> None:
    clock = [100.0]
    monkeypatch.setattr("app.rcon.service.monotonic", lambda: clock[0])
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("capabilities"):
            return cap()
        return httpx.Response(200, json={"players": []})

    rcon = RconClient(
        RconTarget(origin="https://wardogs.invalid", bearer_secret="local-canary"),
        transport=httpx.MockTransport(handler),
    )
    service = ReadService(rcon, CapabilityService(rcon))
    try:
        await service.players()
        clock[0] = 104.0
        await service.players()
        clock[0] = 105.0
        await service.players()
        assert paths.count("/v1/players") == 2
    finally:
        await rcon.close()
