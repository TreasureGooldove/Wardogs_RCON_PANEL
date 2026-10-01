import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import PanelSettings, RconTarget, load_settings
from app.errors import PanelError
from app.main import create_app

SID = "76561190000000001"
READ = "test-read-" + "a" * 40
ADMIN = "test-admin-" + "b" * 40
ORIGIN = "https://game.example.test"


@pytest.fixture
def panel(tmp_path):
    app = create_app(PanelSettings(tmp_path / "db.sqlite3", "https://panel.example.test", True,
        rcon_target=RconTarget(ORIGIN, "fake-rcon"), bot_read_token=READ, bot_admin_token=ADMIN))
    runtime = app.state.rcon_runtime
    runtime.capabilities.require = AsyncMock()
    runtime.capabilities.require_advertised = AsyncMock()
    runtime.capabilities.require_write = AsyncMock()
    runtime.read_service.players = AsyncMock(return_value={"players": [
        {"steamId": SID, "name": "Fictional", "kills": 4, "deaths": 2, "cash": 10, "pingMs": 20}],
        "observedAt": "2026-09-30T00:00:00Z", "stale": False})
    runtime.invalidate_players_unlocked = lambda: None
    runtime.client.moderate = AsyncMock()
    return app


def payload(app, **changes):
    return {"steamId": SID, "reason": "test only", "requestId": "test-request-0001",
            "targetRevision": app.state.rcon_runtime.target_revision,
            "operator": {"qqId": "10000", "groupId": "20000"}, **changes}


def client(app, token=None):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://panel.example.test",
                             headers={"Authorization": f"Bearer {token}"} if token else {})


@pytest.mark.asyncio
async def test_auth_scope_and_redaction(panel):
    async with client(panel) as c:
        assert (await c.get("/api/bot/players", cookies={"session": "fake"})).status_code == 401
    async with client(panel, READ) as c:
        assert (await c.get("/api/bot/players")).status_code == 403
        assert (await c.post("/api/bot/bans", json=payload(panel))).status_code == 403
        result = await c.get("/api/bot/capabilities")
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["features"]["ban"] is False
        assert ORIGIN not in result.text and READ not in result.text
        assert (await c.get("/api/server/players")).status_code == 401
        schema = (await c.get("/api/bot/openapi.json")).json()
        assert len(schema["paths"]) == 6
        assert schema["components"]["securitySchemes"]["BotToken"]["scheme"] == "bearer"
        assert "StatsResponse" in schema["components"]["schemas"]
        assert (await c.get("/api/bot/players/123/stats")).json()["code"] == "bot_invalid_request"
    panel.state.rcon_runtime.client.moderate.assert_not_called()


@pytest.mark.asyncio
async def test_recorded_stats_ranking_unknown_and_offline(panel):
    store = panel.state.history_store
    for sid, name in ((SID, "A"), ("76561190000000002", "B")):
        store.record(ORIGIN, {"map": "Fake"}, {"players": [{"steamId": sid, "name": name,
            "kills": 12, "deaths": 8, "cash": 100}]}, datetime.now(UTC))
    async with client(panel, READ) as c:
        data = (await c.get(f"/api/bot/players/{SID}/stats")).json()
        assert data["stats"]["kills"] == 12 and data["currentMatch"]["kills"] == 4
        assert data["stats"]["wins"] is None and data["stats"]["battleLevel"] is None
        assert data["scope"] == "recorded_lifetime"
        rank = (await c.get(f"/api/bot/players/{SID}/ranking")).json()
        assert rank["rank"] == 1 and rank["totalPlayers"] == 2 and rank["includesOffline"]
        other = (await c.get("/api/bot/players/76561190000000002/ranking")).json()
        assert other["rank"] == 1
        unknown = (await c.get("/api/bot/players/76561190000000999/stats")).json()
        assert all(v is None for v in unknown["stats"].values())
        assert unknown["online"] is False
        assert (await c.get(f"/api/bot/players/{SID}/ranking?period=week")).status_code == 400


@pytest.mark.asyncio
async def test_reserved_individual_runtime_vs_config(panel, monkeypatch):
    monkeypatch.setattr("app.api.bot.read_reserved", AsyncMock(return_value=[]))
    monkeypatch.setattr("app.api.bot.read_config", AsyncMock(return_value={"text":
        f"[/Script/WDGame.WDGameSession]\n.DefaultReservedPlayerIds={SID}\n"}))
    panel.state.database.put_reserved_metadata(ORIGIN, SID, "fake reward", "2026-10-01T00:00:00Z")
    async with client(panel, READ) as c:
        data = (await c.get(f"/api/bot/players/{SID}/reserved-slot")).json()
        assert data["configured"] and not data["active"] and data["pendingRestart"]
        assert data["reason"] == "fake reward" and "reservedSlots" not in data


@pytest.mark.asyncio
async def test_ban_idempotency_revision_audit_and_offline(panel):
    async with client(panel, ADMIN) as c:
        body = payload(panel)
        first = (await c.post("/api/bot/bans", json=body)).json()
        assert first["outcome"] == "accepted"
        repeated = (await c.post("/api/bot/bans", json=body)).json()
        assert repeated["replayed"]
        assert (await c.post("/api/bot/bans", json={**body, "reason": "changed"})).status_code == 409
        stale = (await c.post("/api/bot/bans", json=payload(panel, requestId="test-stale-0001", targetRevision="wrong"))).json()
        assert stale["outcome"] == "rejected" and stale["code"] == "stale_server_target"
        offline = (await c.post("/api/bot/bans", json=payload(panel, requestId="test-offline-0001", steamId="76561190000000999"))).json()
        assert offline["code"] == "player_not_online"
    panel.state.rcon_runtime.client.moderate.assert_awaited_once()
    with panel.state.database._connect() as db:
        row = db.execute("SELECT payload,result FROM bot_ban_requests WHERE request_id=?", (body["requestId"],)).fetchone()
        assert '"qqId": "10000"' in row["payload"] and '"outcome": "accepted"' in row["result"]


@pytest.mark.asyncio
async def test_timeout_and_restart_never_replay(panel):
    panel.state.rcon_runtime.client.moderate.side_effect = PanelError("action_uncertain")
    body = payload(panel)
    async with client(panel, ADMIN) as c:
        assert (await c.post("/api/bot/bans", json=body)).json()["outcome"] == "uncertain"
    restarted = create_app(panel.state.settings)
    async with client(restarted, ADMIN) as c:
        result = (await c.post("/api/bot/bans", json=body)).json()
        assert result["outcome"] == "uncertain" and result["replayed"]
    panel.state.rcon_runtime.client.moderate.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_duplicate_and_interrupted_claim(panel):
    entered, release = asyncio.Event(), asyncio.Event()
    async def delayed(*args):
        entered.set()
        await release.wait()
    panel.state.rcon_runtime.client.moderate.side_effect = delayed
    async with client(panel, ADMIN) as c:
        body = payload(panel)
        first = asyncio.create_task(c.post("/api/bot/bans", json=body))
        await entered.wait()
        second = (await c.post("/api/bot/bans", json=body)).json()
        assert second["outcome"] == "uncertain" and second["replayed"]
        release.set()
        assert (await first).json()["outcome"] == "accepted"
    panel.state.rcon_runtime.client.moderate.assert_awaited_once()
    pending = payload(panel, requestId="interrupted-0001")
    panel.state.bot_store.claim(pending, ORIGIN)
    restarted = create_app(panel.state.settings)
    async with client(restarted, ADMIN) as c:
        assert (await c.post("/api/bot/bans", json=pending)).json()["outcome"] == "uncertain"


def test_env_tokens_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        PanelSettings(tmp_path / "db", "http://localhost", False, bot_read_token="short")
    with pytest.raises(ValueError):
        PanelSettings(tmp_path / "db", "http://localhost", False, bot_read_token=READ, bot_admin_token=READ)
    settings = load_settings({"PANEL_BOT_READ_TOKEN": READ, "PANEL_BOT_ADMIN_TOKEN": ADMIN})
    assert settings.bot_read_token == READ and READ not in repr(settings)


@pytest.mark.asyncio
async def test_reserved_uses_actual_advertised_routes(tmp_path):
    def transport(request):
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"routes": ["GET /v1/reserved-slots", "GET /v1/config"]})
        if request.url.path == "/v1/reserved-slots":
            return httpx.Response(200, json={"reservedSlots": [SID]})
        if request.url.path == "/v1/config":
            return httpx.Response(200, json={"text": "[/Script/WDGame.WDGameSession]\nServerPassword=\n",
                                             "revision": "fake-1", "writable": False})
        raise AssertionError(str(request.url.path))
    app = create_app(PanelSettings(tmp_path / "db", "https://panel.example.test", True,
        rcon_target=RconTarget(ORIGIN, "fake-rcon"), bot_read_token=READ),
        rcon_transport=httpx.MockTransport(transport))
    async with client(app, READ) as c:
        response = await c.get(f"/api/bot/players/{SID}/reserved-slot")
        assert response.status_code == 200
        assert response.json()["active"] and not response.json()["configured"]
    await app.state.rcon_runtime.close()
