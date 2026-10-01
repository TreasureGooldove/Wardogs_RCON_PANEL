"""Bot gateway probes use mock transport and cannot perform game writes."""
from dataclasses import replace
import json

import httpx
import pytest

from app.api.diagnostics import probe_bot_gateway
from app.config import PanelSettings, load_settings
from app.main import create_app

TOKEN = "fictional-probe-token-" + "a" * 40
SCHEMA = {"paths": {"/api/bot/players/{steamId}/stats": {}, "/api/bot/bans": {}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,state", [
    (200, json.dumps(SCHEMA), "available"), (401, "secret detail", "auth_rejected"),
    (403, "secret detail", "auth_rejected"), (404, "not found", "http_error"),
    (302, "redirect", "http_error"), (200, "not JSON", "invalid_response"),
    (200, "{}", "invalid_response"), (200, "x" * 140000, "invalid_response"),
], ids=["success", "unauthorized", "forbidden", "missing", "redirect", "malformed", "wrong-schema", "oversized"])
async def test_probe_boundaries(status, body, state):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/api/bot/openapi.json"
        assert request.headers["authorization"] == "Bearer " + TOKEN
        assert "cookie" not in request.headers
        return httpx.Response(status, text=body, headers={"Location": "https://other.invalid"})
    result = await probe_bot_gateway("http://panel.example.test:23334", TOKEN, transport=httpx.MockTransport(handler))
    assert result["state"] == state and result["httpStatus"] == status
    assert len(calls) == 1 and TOKEN not in json.dumps(result) and "secret detail" not in json.dumps(result)


@pytest.mark.asyncio
async def test_unconfigured_or_disconnected_probe_never_exposes_secret():
    def handler(request):
        raise httpx.ConnectError("private detail " + TOKEN)
    transport = httpx.MockTransport(handler)
    assert (await probe_bot_gateway("http://panel.example.test:23334", None, transport=transport))["state"] == "unconfigured"
    result = await probe_bot_gateway("http://panel.example.test:23334", TOKEN, transport=transport)
    assert result["state"] == "unreachable" and result["httpStatus"] is None
    assert TOKEN not in json.dumps(result)


def test_gateway_origin_cannot_send_token_to_another_hostname(tmp_path):
    settings = PanelSettings(tmp_path / "db", "https://panel.example.test:23333", True)
    assert replace(settings, bot_public_origin="http://panel.example.test:23334").session_secure
    for bad in ("http://other.example.test:23334", "http://panel.example.test:23334/api/bot",
                "http://user:password@panel.example.test:23334", "http://panel.example.test:23334?token=bad"):
        with pytest.raises(ValueError):
            replace(settings, bot_public_origin=bad)
    loaded = load_settings({"PANEL_PUBLIC_ORIGIN": settings.public_origin,
                           "PANEL_BOT_PUBLIC_ORIGIN": "http://panel.example.test:23334"})
    assert loaded.bot_public_origin == "http://panel.example.test:23334" and loaded.session_secure


@pytest.mark.asyncio
async def test_status_is_owner_only_and_never_returns_credentials(tmp_path, monkeypatch):
    app = create_app(PanelSettings(tmp_path / "db", "https://panel.example.test", True,
                                 bot_read_token=TOKEN, bot_public_origin="http://panel.example.test:23334"))
    app.state.auth_service.create_admin("owner", "fictional owner password")
    app.state.auth_service.create_subuser("viewer", "fictional viewer password")
    calls = []
    async def probe(origin, token):
        calls.append((origin, bool(token)))
        return {"state": "available" if token else "unconfigured", "httpStatus": 200 if token else None, "latencyMs": 2 if token else None}
    monkeypatch.setattr("app.api.diagnostics.probe_bot_gateway", probe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://panel.example.test") as client:
        assert (await client.get("/api/server/bot-api-status")).status_code == 401
        for name, password, status in [("viewer", "fictional viewer password", 403), ("owner", "fictional owner password", 200)]:
            await client.post("/api/auth/login", json={"username": name, "password": password}, headers={"Origin": "https://panel.example.test"})
            response = await client.get("/api/server/bot-api-status")
            assert response.status_code == status
        assert response.headers["cache-control"] == "no-store"
        assert TOKEN not in response.text and "fictional owner password" not in response.text
        assert response.json()["credentials"][0]["configured"] is True
        assert response.json()["credentials"][1]["configured"] is False
    assert len(calls) == 2
