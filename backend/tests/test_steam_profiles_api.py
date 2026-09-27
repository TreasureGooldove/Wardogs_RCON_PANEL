"""Steam profile proxy contract; all upstream calls use MockTransport."""

import asyncio
import json
import secrets

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app.api.auth import build_auth_router
from app.api.steam import router as steam_router
from app.auth.sessions import AuthService
from app.config import PanelSettings
from app.errors import install_error_handlers
from app.main import create_app
from app.steam.service import SteamProfileService
from app.storage.db import Database


ORIGIN = "https://panel.example"
ID_A = "76561198000000001"
ID_B = "76561198000000002"


def make_app(tmp_path, handler, *, key=None, clock=None):
    settings = PanelSettings(
        db_path=tmp_path / "panel.sqlite3", public_origin=ORIGIN, session_secure=True
    )
    database = Database(settings.db_path)
    database.initialize()
    auth = AuthService(database, settings)
    auth.create_admin("admin", "test password has enough length")
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(build_auth_router(auth))
    app.include_router(steam_router)
    app.state.auth_service = auth
    service = SteamProfileService(
        key=key,
        transport=httpx.MockTransport(handler),
        **({"clock": clock} if clock is not None else {}),
    )
    app.state.steam_service = service
    return app, service


async def login(client):
    response = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test password has enough length"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


def payload(*ids):
    return {"steamIds": list(ids)}


def summaries(*players):
    return httpx.Response(200, json={"response": {"players": list(players)}})


@pytest.mark.asyncio
async def test_auth_origin_and_missing_key_prevent_steam_call(tmp_path):
    sent = []

    def handler(request):
        sent.append(request)
        return summaries()

    app, service = make_app(tmp_path, handler)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            unauth = await client.post("/api/steam/profiles", json=payload(ID_A), headers={"Origin": ORIGIN})
            assert (unauth.status_code, unauth.json()["code"]) == (401, "not_authenticated")
            await login(client)
            wrong_origin = await client.post("/api/steam/profiles", json=payload(ID_A))
            assert (wrong_origin.status_code, wrong_origin.json()["code"]) == (401, "not_authenticated")
            unconfigured = await client.post("/api/steam/profiles", json=payload(ID_A), headers={"Origin": ORIGIN})
            assert (unconfigured.status_code, unconfigured.json()["code"]) == (503, "steam_unconfigured")
        assert sent == []
    finally:
        await service.close()


def test_create_app_mounts_steam_route_and_closes_service(tmp_path, monkeypatch):
    monkeypatch.delenv("STEAM_WEB_API_KEY", raising=False)
    app = create_app(PanelSettings(
        db_path=tmp_path / "mounted.sqlite3", public_origin=ORIGIN, session_secure=True,
    ))
    app.state.auth_service.create_admin("admin", "test password has enough length")
    service = app.state.steam_service
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post(
            "/api/steam/profiles", json=payload(ID_A), headers={"Origin": ORIGIN}
        ).status_code == 401
        assert client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test password has enough length"},
            headers={"Origin": ORIGIN},
        ).status_code == 200
        missing = client.post(
            "/api/steam/profiles", json=payload(ID_A), headers={"Origin": ORIGIN}
        )
        assert (missing.status_code, missing.json()["code"]) == (503, "steam_unconfigured")
    assert service._client.is_closed


@pytest.mark.asyncio
async def test_fixed_host_header_only_key_and_safe_projection(tmp_path):
    sent = []
    key = secrets.token_hex(16)
    avatar = "https://avatars.steamstatic.com/abc_full.jpg"

    def handler(request):
        sent.append(request)
        return summaries(
            {
                "steamid": ID_A,
                "personaname": "Public name",
                "avatarfull": avatar,
                "profileurl": "https://attacker.example/fake",
                "privateToken": "must-not-appear",
            },
            {
                "steamid": ID_B,
                "personaname": "Other",
                "avatarfull": "http://127.0.0.1/private.jpg",
                "avatar": "https://evil.example/avatar.jpg",
                "adminPassword": "must-not-appear",
            },
        )

    app, service = make_app(tmp_path, handler, key=key)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            await login(client)
            response = await client.post(
                "/api/steam/profiles", json=payload(ID_A, ID_B), headers={"Origin": ORIGIN}
            )
        assert response.status_code == 200
        assert response.json() == {
            "profiles": [
                {
                    "steamId": ID_A,
                    "personaName": "Public name",
                    "avatarUrl": avatar,
                    "profileUrl": f"https://steamcommunity.com/profiles/{ID_A}/",
                },
                {
                    "steamId": ID_B,
                    "personaName": "Other",
                    "avatarUrl": None,
                    "profileUrl": f"https://steamcommunity.com/profiles/{ID_B}/",
                },
            ]
        }
        assert len(sent) == 1
        assert sent[0].method == "GET"
        assert str(sent[0].url).startswith(
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?steamids="
        )
        assert sent[0].url.params.get("steamids") == f"{ID_A},{ID_B}"
        assert "key" not in sent[0].url.params
        assert sent[0].headers["x-webapi-key"] == key
        assert key not in response.text
        assert "must-not-appear" not in response.text
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {},
        {"steamIds": []},
        {"steamIds": [ID_A, "0"]},
        {"steamIds": ["00000000000000000"]},
        {"steamIds": [76561198000000001]},
        {"steamIds": [ID_A], "extra": "secret"},
        {"steamIds": [ID_A] * 101},
    ],
)
async def test_invalid_payload_is_rejected_without_upstream(tmp_path, invalid):
    sent = []

    def handler(request):
        sent.append(request)
        return summaries()

    app, service = make_app(tmp_path, handler, key=secrets.token_hex(16))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            await login(client)
            response = await client.post("/api/steam/profiles", json=invalid, headers={"Origin": ORIGIN})
        assert (response.status_code, response.json()["code"]) == (400, "invalid_steam_ids")
        assert "secret" not in response.text
        assert sent == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cache_handles_missing_profile_and_five_second_poll(tmp_path):
    sent = []
    clock = [100.0]

    def handler(request):
        sent.append(request)
        return summaries({"steamid": ID_A, "personaname": "Known"})

    app, service = make_app(tmp_path, handler, key=secrets.token_hex(16), clock=lambda: clock[0])
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            await login(client)
            first = await client.post("/api/steam/profiles", json=payload(ID_A, ID_B, ID_A), headers={"Origin": ORIGIN})
            clock[0] += 5
            second = await client.post("/api/steam/profiles", json=payload(ID_A, ID_B), headers={"Origin": ORIGIN})
            clock[0] += 596
            third = await client.post("/api/steam/profiles", json=payload(ID_A, ID_B), headers={"Origin": ORIGIN})
        assert first.status_code == second.status_code == third.status_code == 200
        assert [item["steamId"] for item in first.json()["profiles"]] == [ID_A, ID_B, ID_A]
        assert first.json()["profiles"][1]["personaName"] is None
        assert len(sent) == 2
        assert sent[0].url.params["steamids"] == f"{ID_A},{ID_B}"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_uncached_batch_is_globally_throttled(tmp_path):
    sent = []
    clock = [100.0]

    def handler(request):
        sent.append(request)
        return summaries()

    app, service = make_app(tmp_path, handler, key=secrets.token_hex(16), clock=lambda: clock[0])
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            await login(client)
            first = await client.post("/api/steam/profiles", json=payload(ID_A), headers={"Origin": ORIGIN})
            second = await client.post("/api/steam/profiles", json=payload(ID_B), headers={"Origin": ORIGIN})
            clock[0] += 1
            third = await client.post("/api/steam/profiles", json=payload(ID_B), headers={"Origin": ORIGIN})
        assert first.status_code == third.status_code == 200
        assert (second.status_code, second.json()["code"]) == (429, "steam_rate_limited")
        assert len(sent) == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_concurrent_same_id_queries_join_one_upstream_call(tmp_path):
    started = asyncio.Event()
    release = asyncio.Event()
    sent = []

    async def handler(request):
        sent.append(request)
        started.set()
        await release.wait()
        return summaries({"steamid": ID_A, "personaname": "Shared"})

    app, service = make_app(tmp_path, handler, key=secrets.token_hex(16))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            await login(client)
            one = asyncio.create_task(client.post("/api/steam/profiles", json=payload(ID_A), headers={"Origin": ORIGIN}))
            await asyncio.wait_for(started.wait(), 2)
            two = asyncio.create_task(client.post("/api/steam/profiles", json=payload(ID_A), headers={"Origin": ORIGIN}))
            await asyncio.sleep(0)
            release.set()
            first, second = await asyncio.gather(one, two)
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert len(sent) == 1
    finally:
        release.set()
        await service.close()


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_failure_has_short_negative_cache(tmp_path):
    sent = []
    clock = [100.0]

    def handler(request):
        sent.append(request)
        return httpx.Response(302, headers={"Location": "https://attacker.example/steal"})

    app, service = make_app(tmp_path, handler, key=secrets.token_hex(16), clock=lambda: clock[0])
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            await login(client)
            first = await client.post("/api/steam/profiles", json=payload(ID_A), headers={"Origin": ORIGIN})
            clock[0] += 5
            second = await client.post("/api/steam/profiles", json=payload(ID_A), headers={"Origin": ORIGIN})
            clock[0] += 26
            third = await client.post("/api/steam/profiles", json=payload(ID_A), headers={"Origin": ORIGIN})
        assert [item.json()["code"] for item in (first, second, third)] == ["steam_bad_response"] * 3
        assert len(sent) == 2
        assert all(item.url.host == "api.steampowered.com" for item in sent)
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        pytest.param(
            json.dumps({"response": {"players": []}, "secret": "x" * 262_144}).encode(),
            id="oversized",
        ),
        pytest.param(("[" * 1100 + "]" * 1100).encode(), id="deeply-nested"),
    ],
)
async def test_oversized_or_malformed_upstream_response_is_redacted(tmp_path, content):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, content=content)

    app, service = make_app(tmp_path, handler, key=secrets.token_hex(16))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=ORIGIN) as client:
            await login(client)
            response = await client.post("/api/steam/profiles", json=payload(ID_A), headers={"Origin": ORIGIN})
        assert (response.status_code, response.json()["code"]) == (502, "steam_bad_response")
        assert "secret" not in response.text
        assert len(sent) == 1
    finally:
        await service.close()
