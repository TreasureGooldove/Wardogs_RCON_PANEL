"""Warmup rewards use only local MockTransport; no live server writes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from app.config import PanelSettings, RconTarget
from app.main import create_app
from app.rcon.config_doc import reserved_ids_from_text
from app.warmup.engine import next_detection


PANEL = "https://panel.example.invalid"
ORIGIN = "https://rcon.example.invalid"
A = "76561198000000001"
B = "76561198000000002"
C = "76561198000000003"


def make_app(tmp_path, *, timeout=False):
    config = "[/Script/WDGame.WDGameSession]\n!DefaultReservedPlayerIds=ClearArray\n"
    config += f".DefaultReservedPlayerIds={A}\n+DefaultReservedPlayerIds={C}\n"
    state = {"text": config, "writes": [], "messages": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/capabilities":
            return httpx.Response(200, json={"routes": [
                "GET /v1/config", "PUT /v1/config",
                "POST /v1/players/{steamId}/message", "POST /v1/broadcast",
            ], "config": {"writable": True}})
        if path == "/v1/config" and request.method == "GET":
            return httpx.Response(200, json={
                "revision": "revision-1", "writable": True, "text": state["text"],
                "sections": [], "warnings": [],
            })
        if path == "/v1/config" and request.method == "PUT":
            state["writes"].append(request)
            if timeout:
                raise httpx.ReadTimeout("mock timeout", request=request)
            state["text"] = request.content.decode()
            return httpx.Response(200, json={"ok": True, "revision": "revision-2"})
        if request.method == "POST":
            state["messages"].append(request)
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(f"unexpected mock route {request.method} {path}")

    app = create_app(PanelSettings(
        db_path=tmp_path / "panel.sqlite3", public_origin=PANEL,
        session_secure=True, history_enabled=True,
        rcon_target=RconTarget(origin=ORIGIN, bearer_secret="mock-only"),
    ), rcon_transport=httpx.MockTransport(handler))
    app.state.auth_service.create_admin("owner", "test-owner-password")
    return app, state


def enable(app, *, threshold=3, days=2, mode="private"):
    app.state.warmup_store.save(
        ORIGIN, enabled=True, player_threshold=threshold, gift_days=days,
        interval_mode="hours", interval_hours=24, notification_mode=mode,
    )


def players(*ids):
    return [{"steamId": sid, "name": f"Player {index}"}
            for index, sid in enumerate(ids)]


def test_daily_and_hourly_next_detection():
    last = {"started_at": "2026-09-27T15:59:00+00:00"}
    assert next_detection({"interval_mode": "daily"}, last) == datetime.fromisoformat(
        "2026-09-27T16:00:00+00:00")
    assert next_detection({"interval_mode": "hours", "interval_hours": 6}, last) == datetime.fromisoformat(
        "2026-09-27T21:59:00+00:00")


def test_threshold_grants_new_player_without_shortening_existing_or_permanent(tmp_path):
    app, state = make_app(tmp_path)
    engine = app.state.warmup_engine
    engine.observe(ORIGIN, players(A, B, C))
    asyncio.run(engine.tick())  # Disabled by default.
    assert state["writes"] == []
    enable(app)
    app.state.database.put_reserved_metadata(
        ORIGIN, A, "原有预留", (datetime.now(UTC) + timedelta(days=3)).isoformat())
    engine.observe(ORIGIN, players(A, B))
    asyncio.run(engine.tick())  # Below threshold.
    assert state["writes"] == []
    engine.observe(ORIGIN, players(A, B, C))
    asyncio.run(engine.tick())
    assert len(state["writes"]) == 1
    assert set(reserved_ids_from_text(state["text"])) == {A, B, C}
    metadata = app.state.database.reserved_metadata(ORIGIN)
    assert metadata[A]["reason"] == "原有预留"
    assert datetime.fromisoformat(metadata[A]["expiresAt"]) > datetime.now(UTC) + timedelta(days=2)
    assert metadata[B]["reason"] == "暖服奖励"
    assert C not in metadata
    run = app.state.warmup_store.recent(ORIGIN)[0]
    assert (run["outcome"], run["awarded_count"], run["skipped_count"]) == ("accepted", 1, 2)
    asyncio.run(engine.tick())
    assert len(state["writes"]) == 1
    assert len(state["messages"]) == 1
    assert state["messages"][0].url.path.endswith(f"/{B}/message")
    asyncio.run(app.state.rcon_runtime.close())


def test_existing_short_expiry_resets_to_gift_window_without_config_write(tmp_path):
    app, state = make_app(tmp_path)
    enable(app, threshold=1, days=2, mode="broadcast")
    app.state.database.put_reserved_metadata(
        ORIGIN, A, "原有预留", (datetime.now(UTC) + timedelta(hours=6)).isoformat())
    engine = app.state.warmup_engine
    engine.observe(ORIGIN, players(A))
    asyncio.run(engine.tick())
    assert state["writes"] == []
    metadata = app.state.database.reserved_metadata(ORIGIN)
    assert datetime.fromisoformat(metadata[A]["expiresAt"]) >= datetime.now(UTC) + timedelta(days=2, seconds=-5)
    asyncio.run(engine.tick())
    assert len(state["messages"]) == 1
    assert state["messages"][0].url.path == "/v1/broadcast"
    asyncio.run(app.state.rcon_runtime.close())


def test_uncertain_config_write_blocks_every_later_detection(tmp_path):
    app, state = make_app(tmp_path, timeout=True)
    enable(app, threshold=1)
    engine = app.state.warmup_engine
    engine.observe(ORIGIN, players(B))
    asyncio.run(engine.tick())
    assert len(state["writes"]) == 1
    assert app.state.warmup_store.recent(ORIGIN)[0]["outcome"] == "attention"
    assert app.state.database.reserved_metadata(ORIGIN) == {}
    asyncio.run(engine.tick())
    assert len(state["writes"]) == 1
    assert app.state.warmup_store.blocked(ORIGIN)
    asyncio.run(app.state.rcon_runtime.close())


def test_owner_config_requires_reauthentication_and_subuser_cannot_change(tmp_path):
    app, state = make_app(tmp_path)
    with TestClient(app, base_url=PANEL) as client:
        assert client.get("/api/warmup").status_code == 401
        assert client.post("/api/auth/login", json={
            "username": "owner", "password": "test-owner-password",
        }, headers={"Origin": PANEL}).status_code == 200
        current = client.get("/api/warmup").json()
        assert current["enabled"] is False
        body = {"targetRevision": current["targetRevision"], "enabled": True,
                "playerThreshold": 20, "giftDays": 1, "intervalMode": "daily",
                "intervalHours": 24, "notificationMode": "private"}
        assert client.put("/api/warmup", json=body, headers={"Origin": PANEL}).status_code == 400
        assert client.put("/api/warmup", json={**body, "password": "wrong-password"},
                          headers={"Origin": PANEL}).status_code == 401
        assert client.put("/api/warmup", json={**body, "password": "test-owner-password"},
                          headers={"Origin": PANEL}).status_code == 200
        assert client.post("/api/subusers", json={
            "username": "viewer", "password": "test-viewer-password",
        }, headers={"Origin": PANEL}).status_code == 201
        with TestClient(app, base_url=PANEL) as viewer:
            assert viewer.post("/api/auth/login", json={
                "username": "viewer", "password": "test-viewer-password",
            }, headers={"Origin": PANEL}).status_code == 200
            assert viewer.get("/api/warmup").status_code == 403
            assert viewer.put("/api/warmup", json=body,
                              headers={"Origin": PANEL}).status_code == 403
    assert state["writes"] == []
