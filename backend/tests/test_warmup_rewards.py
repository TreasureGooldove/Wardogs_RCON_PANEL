"""Warmup rewards use only local MockTransport; no live server writes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json

import httpx
from fastapi.testclient import TestClient

from app.config import PanelSettings, RconTarget
from app.main import create_app
from app.rcon.config_doc import reserved_ids_from_text
from app.warmup.engine import next_detection
from app.warmup.store import DEFAULT_NOTIFICATION_TEXT, WarmupStore
from app.storage.db import Database


PANEL = "https://panel.example.invalid"
ORIGIN = "https://rcon.example.invalid"
A = "76561198000000001"
B = "76561198000000002"
C = "76561198000000003"
D = "76561198000000004"


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


def enable(app, *, threshold=3, days=2, mode="private",
           message="感谢您的暖服支持！您已获赠{x}天预留位。"):
    app.state.warmup_store.save(
        ORIGIN, enabled=True, player_threshold=threshold, gift_days=days,
        interval_mode="hours", interval_hours=24, notification_mode=mode,
        notification_text=message,
        reset_threshold=threshold - 1, reset_minutes=0,
    )


def players(*ids):
    return [{"steamId": sid, "name": f"Player {index}"}
            for index, sid in enumerate(ids)]


def test_daily_and_hourly_limits_both_apply():
    last = {"started_at": "2026-09-27T15:59:00+00:00"}
    assert next_detection({"interval_hours": 6}, last) == datetime.fromisoformat(
        "2026-09-27T21:59:00+00:00")
    assert next_detection({"interval_hours": 1}, last) == datetime.fromisoformat(
        "2026-09-27T16:59:00+00:00")
    assert next_detection({"interval_hours": 1}, {"started_at": "2026-09-27T00:00:00+00:00"}) == datetime.fromisoformat(
        "2026-09-27T16:00:00+00:00")


def test_low_population_must_be_continuous_before_rearming(tmp_path):
    app, _ = make_app(tmp_path)
    store = app.state.warmup_store
    start = datetime(2026, 9, 27, 0, tzinfo=UTC)
    def sample(count, seconds):
        return store.advance_cycle(ORIGIN, count, reset_threshold=10,
                                   reset_minutes=10, player_threshold=20,
                                   now=start + timedelta(seconds=seconds))
    assert not sample(9, 0)
    assert not sample(11, 300)  # A rise interrupts the low window.
    for seconds in range(305, 905, 5):
        assert not sample(9, seconds)
    assert store.cycle(ORIGIN)["phase"] == "waiting_low"
    assert not sample(9, 905)
    assert store.cycle(ORIGIN)["phase"] == "armed"
    assert sample(20, 910)
    assert not sample(20, 915)
    asyncio.run(app.state.rcon_runtime.close())


def test_restart_while_full_does_not_turn_persisted_arm_into_a_reward(tmp_path):
    app, _ = make_app(tmp_path)
    start = datetime(2026, 9, 27, 0, tzinfo=UTC)
    store = app.state.warmup_store
    assert not store.advance_cycle(ORIGIN, 0, reset_threshold=10, reset_minutes=0,
                                   player_threshold=20, now=start)
    assert store.cycle(ORIGIN)["phase"] == "armed"
    restarted = WarmupStore(app.state.database)
    restarted.initialize()
    assert restarted.cycle(ORIGIN)["phase"] == "armed"
    assert not restarted.advance_cycle(ORIGIN, 20, reset_threshold=10,
                                       reset_minutes=0, player_threshold=20,
                                       now=start + timedelta(seconds=5), first_sample=True)
    assert restarted.cycle(ORIGIN)["phase"] == "waiting_low"
    asyncio.run(app.state.rcon_runtime.close())


def test_existing_warmup_tables_gain_editable_notification_without_losing_policy(tmp_path):
    database = Database(tmp_path / "old.sqlite3")
    with database._connect() as db:
        db.executescript("""
            CREATE TABLE warmup_config (
                id INTEGER PRIMARY KEY, origin TEXT, enabled INTEGER, player_threshold INTEGER,
                gift_days INTEGER, interval_mode TEXT, interval_hours INTEGER,
                notification_mode TEXT, updated_at TEXT
            );
            INSERT INTO warmup_config VALUES (1,'test-origin',0,30,2,'daily',24,'private','old');
            CREATE TABLE warmup_runs (
                id TEXT PRIMARY KEY, origin TEXT, started_at TEXT, finished_at TEXT,
                outcome TEXT, player_count INTEGER, gift_days INTEGER, awarded_count INTEGER,
                skipped_count INTEGER, notification_mode TEXT, notification_status TEXT, detail TEXT
            );
            INSERT INTO warmup_runs VALUES ('old-run','test-origin','2026-09-27T00:00:00+00:00',
                '2026-09-27T00:00:01+00:00','accepted',30,2,30,0,'private','accepted','');
        """)
    store = WarmupStore(database)
    store.initialize()
    store.initialize()
    assert store.config()["player_threshold"] == 30
    assert store.config()["reset_threshold"] == 15
    assert store.config()["reset_minutes"] == 10
    assert store.config()["notification_text"] == DEFAULT_NOTIFICATION_TEXT
    assert store.latest("test-origin")["notification_text"] == DEFAULT_NOTIFICATION_TEXT


def test_threshold_grants_new_player_without_shortening_existing_or_permanent(tmp_path):
    app, state = make_app(tmp_path)
    engine = app.state.warmup_engine
    engine.observe(ORIGIN, players(A, B, C))
    asyncio.run(engine.tick())  # Disabled by default.
    assert state["writes"] == []
    enable(app, message="测试奖励 {x} 天")
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
    enable(app, message="已经修改的文本 {x}")
    asyncio.run(engine.tick())
    assert len(state["writes"]) == 1
    assert len(state["messages"]) == 1
    assert state["messages"][0].url.path.endswith(f"/{B}/message")
    assert json.loads(state["messages"][0].content)["message"] == "测试奖励 2 天"
    asyncio.run(app.state.rcon_runtime.close())


def test_existing_short_expiry_resets_to_gift_window_without_config_write(tmp_path):
    app, state = make_app(tmp_path)
    enable(app, threshold=1, days=2, mode="broadcast", message="感谢暖服，获得{x}天")
    app.state.database.put_reserved_metadata(
        ORIGIN, A, "原有预留", (datetime.now(UTC) + timedelta(hours=6)).isoformat())
    engine = app.state.warmup_engine
    engine.observe(ORIGIN, players())
    asyncio.run(engine.tick())
    engine.observe(ORIGIN, players(A))
    asyncio.run(engine.tick())
    assert state["writes"] == []
    metadata = app.state.database.reserved_metadata(ORIGIN)
    assert datetime.fromisoformat(metadata[A]["expiresAt"]) >= datetime.now(UTC) + timedelta(days=2, seconds=-5)
    asyncio.run(engine.tick())
    assert len(state["messages"]) == 1
    assert state["messages"][0].url.path == "/v1/broadcast"
    assert json.loads(state["messages"][0].content)["message"] == "感谢暖服，获得2天"
    asyncio.run(app.state.rcon_runtime.close())


def test_uncertain_config_write_blocks_every_later_detection(tmp_path):
    app, state = make_app(tmp_path, timeout=True)
    enable(app, threshold=1)
    engine = app.state.warmup_engine
    engine.observe(ORIGIN, players())
    asyncio.run(engine.tick())
    engine.observe(ORIGIN, players(B))
    asyncio.run(engine.tick())
    assert len(state["writes"]) == 1
    assert app.state.warmup_store.recent(ORIGIN)[0]["outcome"] == "attention"
    assert app.state.database.reserved_metadata(ORIGIN) == {}
    asyncio.run(engine.tick())
    assert len(state["writes"]) == 1
    assert app.state.warmup_store.blocked(ORIGIN)
    asyncio.run(app.state.rcon_runtime.close())


def test_cooldown_crossing_is_skipped_and_full_server_never_gets_delayed_reward(tmp_path):
    app, state = make_app(tmp_path)
    enable(app, threshold=1)
    engine = app.state.warmup_engine
    engine.observe(ORIGIN, players())
    asyncio.run(engine.tick())
    engine.observe(ORIGIN, players(B))
    asyncio.run(engine.tick())
    assert len(app.state.warmup_store.recent(ORIGIN)) == 1
    engine.observe(ORIGIN, players())
    asyncio.run(engine.tick())
    engine.observe(ORIGIN, players(D))  # Crosses while both time limits are active.
    asyncio.run(engine.tick())
    assert len(app.state.warmup_store.recent(ORIGIN)) == 1
    with app.state.database._connect() as db:
        db.execute("UPDATE warmup_runs SET started_at=?", ((datetime.now(UTC) - timedelta(days=2)).isoformat(),))
    engine.observe(ORIGIN, players(D))  # Still full after cooldown: no retroactive gift.
    asyncio.run(engine.tick())
    assert len(app.state.warmup_store.recent(ORIGIN)) == 1
    engine.observe(ORIGIN, players())
    asyncio.run(engine.tick())
    engine.observe(ORIGIN, players(D))
    asyncio.run(engine.tick())
    assert len(app.state.warmup_store.recent(ORIGIN)) == 2
    assert len(state["writes"]) == 2
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
        assert current["notificationText"] == "感谢您的暖服支持！您已获赠{x}天预留位。"
        body = {"targetRevision": current["targetRevision"], "enabled": True,
                "playerThreshold": 20, "resetThreshold": 10, "resetMinutes": 10,
                "giftDays": 1,
                "intervalHours": 24, "notificationMode": "private",
                "notificationText": "谢谢支持，奖励{x}天"}
        assert client.put("/api/warmup", json={**body, "notificationText": "错误\n公告"},
                          headers={"Origin": PANEL}).status_code == 400
        assert client.put("/api/warmup", json=body, headers={"Origin": PANEL}).status_code == 400
        assert client.put("/api/warmup", json={**body, "password": "wrong-password"},
                          headers={"Origin": PANEL}).status_code == 401
        saved = client.put("/api/warmup", json={**body, "password": "test-owner-password"},
                           headers={"Origin": PANEL})
        assert saved.status_code == 200
        assert saved.json()["notificationText"] == "谢谢支持，奖励{x}天"
        app.state.warmup_engine.observe(ORIGIN, players(A, B, C))
        status = client.get("/api/warmup/status")
        assert status.status_code == 200
        assert status.json()["observedPlayers"] == 3
        assert status.json()["playerThreshold"] == 20
        assert status.json()["cyclePhase"] == "waiting_low"
        assert client.post("/api/subusers", json={
            "username": "viewer", "password": "test-viewer-password",
        }, headers={"Origin": PANEL}).status_code == 201
        with TestClient(app, base_url=PANEL) as viewer:
            assert viewer.post("/api/auth/login", json={
                "username": "viewer", "password": "test-viewer-password",
            }, headers={"Origin": PANEL}).status_code == 200
            assert viewer.get("/api/warmup").status_code == 403
            assert viewer.get("/api/warmup/status").status_code == 403
            assert viewer.put("/api/warmup", json=body,
                              headers={"Origin": PANEL}).status_code == 403
    assert state["writes"] == []
