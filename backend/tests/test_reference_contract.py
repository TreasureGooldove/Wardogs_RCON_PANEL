"""Capability, catalog and rotation contracts using local HTTPX transport."""

import asyncio
from collections.abc import Callable

from fastapi import FastAPI, Request
import httpx
import pytest

from app.api.capabilities import router as capability_router
from app.api.catalog import router as catalog_router
from app.api.rotation import router as rotation_router
from app.config import RconTarget
from app.errors import PanelError, install_error_handlers
from app.rcon.capabilities import CapabilityService
from app.rcon.client import RconClient
from app.rcon.service import ReadService


ALL_ROUTES = [
    "/v1/status",
    "/v1/players",
    "/v1/rotation",
    "/v1/catalog/maps",
    "/v1/catalog/experiences",
    "/v1/catalog/lightings",
]


class FakeAuth:
    def require_admin(self, request: Request) -> object:
        if request.cookies.get("panel_session") != "local-session":
            raise PanelError("not_authenticated")
        return object()


def make_app(
    handler: Callable[[httpx.Request], httpx.Response], *, cap_ttl: float = 30.0
) -> tuple[FastAPI, RconClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(capability_router)
    app.include_router(catalog_router)
    app.include_router(rotation_router)
    rcon = RconClient(
        RconTarget(origin="https://wardogs.invalid", bearer_secret="local-canary"),
        transport=httpx.MockTransport(handler),
    )
    caps = CapabilityService(rcon, ttl_seconds=cap_ttl)
    app.state.auth_service = FakeAuth()
    app.state.capability_service = caps
    app.state.read_service = ReadService(rcon, caps)
    return app, rcon


def cap(routes: list[str] | None = None) -> httpx.Response:
    return httpx.Response(200, json={"routes": ALL_ROUTES if routes is None else routes, "config": {"writable": True}, "secret": "do-not-return"})


def panel_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url="http://test",
        cookies={"panel_session": "local-session"},
    )


@pytest.mark.asyncio
async def test_capabilities_only_derive_six_features_from_advertised_routes() -> None:
    app, rcon = make_app(lambda _: cap(["/v1/status", "GET /v1/catalog/maps", "/v1/bans"]))
    try:
        async with panel_client(app) as client:
            response = await client.get("/api/server/capabilities")
        assert response.status_code == 200
        data = response.json()
        assert data["features"] == {
            "status": True,
            "players": False,
            "rotation": False,
            "maps": True,
            "experiences": False,
            "lightings": False,
            "kick": False,
            "ban": False,
        }
        assert data["state"] == "available"
        assert data["observedAt"].endswith("Z")
        assert "config" not in data and "secret" not in data
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_first_failed_capability_probe_is_unknown_not_unsupported() -> None:
    app, rcon = make_app(lambda _: httpx.Response(503))
    try:
        async with panel_client(app) as client:
            response = await client.get("/api/server/capabilities")
        assert response.status_code == 200
        assert response.json() == {
            "features": dict.fromkeys(
                ("status", "players", "rotation", "maps", "experiences", "lightings", "kick", "ban")
            ),
            "observedAt": None,
            "state": "unavailable",
        }
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_write_capabilities_require_post_and_normalize_parameter_names() -> None:
    app, rcon = make_app(lambda _: cap([
        "POST /v1/players/:steamId/kick", "POST /v1/bans", "GET /v1/players",
    ]))
    try:
        async with panel_client(app) as client:
            response = await client.get("/api/server/capabilities")
        assert response.status_code == 200
        assert response.json()["features"]["kick"] is True
        assert response.json()["features"]["ban"] is True
        assert response.json()["features"]["players"] is True
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_failed_refresh_keeps_values_but_marks_capabilities_stale() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return cap() if attempts == 1 else httpx.Response(503)

    app, rcon = make_app(handler, cap_ttl=0.01)
    try:
        async with panel_client(app) as client:
            first = await client.get("/api/server/capabilities")
            await asyncio.sleep(0.02)
            second = await client.get("/api/server/capabilities")
        assert first.json()["state"] == "available"
        assert second.json()["state"] == "stale"
        assert second.json()["features"] == first.json()["features"]
        assert second.json()["observedAt"] == first.json()["observedAt"]
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_catalog_projects_known_shapes_and_explicit_empty() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path.endswith("capabilities"):
            return cap()
        if request.url.path.endswith("maps"):
            return httpx.Response(200, json={"maps": [{"id": "MapA", "label": "地图 A", "secret": "drop"}]})
        if request.url.path.endswith("experiences"):
            return httpx.Response(200, json={"experiences": []})
        return httpx.Response(200, json=["Day", "Night"])

    app, rcon = make_app(handler)
    try:
        async with panel_client(app) as client:
            maps = await client.get("/api/server/catalog/maps")
            experiences = await client.get("/api/server/catalog/experiences")
            lightings = await client.get("/api/server/catalog/lightings")
        assert maps.status_code == experiences.status_code == lightings.status_code == 200
        assert maps.json()["items"] == [{"id": "MapA", "label": "地图 A"}]
        assert maps.json()["kind"] == "maps"
        assert maps.json()["observedAt"].endswith("Z")
        assert experiences.json()["items"] == []
        assert lightings.json()["items"] == [
            {"id": "Day", "label": "Day"},
            {"id": "Night", "label": "Night"},
        ]
        assert all(request.method == "GET" for request in sent)
        assert {request.url.path for request in sent} == {
            "/v1/capabilities",
            "/v1/catalog/maps",
            "/v1/catalog/experiences",
            "/v1/catalog/lightings",
        }
    finally:
        await rcon.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [{}, {"items": "not-a-list"}])
async def test_missing_or_invalid_catalog_list_is_error(raw: dict) -> None:
    app, rcon = make_app(
        lambda request: cap()
        if request.url.path.endswith("capabilities")
        else httpx.Response(200, json=raw)
    )
    try:
        async with panel_client(app) as client:
            response = await client.get("/api/server/catalog/maps")
        assert response.status_code == 502
        assert response.json()["code"] == "invalid_upstream"
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_unsupported_catalog_or_arbitrary_kind_never_reaches_sensitive_route() -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        return cap([])

    app, rcon = make_app(handler)
    try:
        async with panel_client(app) as client:
            unsupported = await client.get("/api/server/catalog/maps")
            arbitrary = await client.get("/api/server/catalog/bans")
        assert unsupported.status_code == arbitrary.status_code == 501
        assert sent == ["/v1/capabilities"]
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_rotation_entries_preserve_nullable_fields_and_mode() -> None:
    app, rcon = make_app(
        lambda request: cap()
        if request.url.path.endswith("capabilities")
        else httpx.Response(
            200,
            json={"mode": "random", "entries": [{"map": "MapA"}, {"order": 3, "map": "MapB", "experiences": [], "lighting": "Night"}]},
        )
    )
    try:
        async with panel_client(app) as client:
            response = await client.get("/api/server/rotation")
        assert response.status_code == 200
        assert response.json()["mode"] == "random"
        assert response.json()["items"] == [
            {"order": 0, "map": "MapA", "experiences": None, "lighting": None, "zoneAlternator": None, "status": None, "denied": None},
            {"order": 3, "map": "MapB", "experiences": [], "lighting": "Night", "zoneAlternator": None, "status": None, "denied": None},
        ]
    finally:
        await rcon.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [{}, {"entries": "bad"}, {"entries": [{"order": -1, "map": "MapA"}]}, {"items": [{"lighting": "Night"}]}],
)
async def test_invalid_rotation_list_or_item_is_error(raw: dict) -> None:
    app, rcon = make_app(
        lambda request: cap()
        if request.url.path.endswith("capabilities")
        else httpx.Response(200, json=raw)
    )
    try:
        async with panel_client(app) as client:
            response = await client.get("/api/server/rotation")
        assert response.status_code == 502
        assert response.json()["code"] == "invalid_upstream"
    finally:
        await rcon.close()


@pytest.mark.asyncio
async def test_explicit_empty_rotation_is_valid_and_unknown_mode_is_named() -> None:
    app, rcon = make_app(
        lambda request: cap()
        if request.url.path.endswith("capabilities")
        else httpx.Response(200, json={"items": [], "mode": "future-mode"})
    )
    try:
        async with panel_client(app) as client:
            response = await client.get("/api/server/rotation")
        assert response.status_code == 200
        assert response.json()["items"] == []
        assert response.json()["mode"] == "unknown"
    finally:
        await rcon.close()
