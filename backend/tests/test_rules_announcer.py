"""Mock-only checks for rule messages; no production RCON writes."""

import asyncio
import json
from time import monotonic

import httpx
from fastapi.testclient import TestClient

from app.config import PanelSettings, RconTarget
from app.main import create_app
from app.rules.engine import RulesEngine, render_parts, validate_template


ORIGIN = "https://rcon.example.invalid"
FIRST = "76561190000000001"
SECOND = "76561190000000002"


def test_rule_templates_are_complete_and_only_allow_named_placeholders():
    text = "服规" * 105 + " {name}"
    validate_template(text, required=True)
    parts = render_parts(text, "Test", SECOND, 2)
    assert len(parts) == 2
    assert "".join(parts) == "服规" * 105 + " Test"
    try:
        validate_template("{name.__class__}", required=True)
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe placeholder accepted")


def test_cold_baseline_delay_dedup_and_restart_cooldown(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"routes": [
                "GET /v1/players", "POST /v1/players/{steamId}/message",
            ]})
        return httpx.Response(200, json={"message": "accepted"})

    app = create_app(
        PanelSettings(
            db_path=tmp_path / "panel.sqlite3", public_origin="http://127.0.0.1:8000",
            session_secure=False,
            rcon_target=RconTarget(origin=ORIGIN, bearer_secret="mock-only"),
        ),
        rcon_transport=httpx.MockTransport(handler),
    )
    store = app.state.rules_store
    store.save(ORIGIN, enabled=True, first_text="Hi {name}", second_text="B" * 205,
               delay_seconds=0, gap_seconds=0, cooldown_minutes=30, max_per_round=3)
    engine = app.state.rules_engine
    old = {"steamId": FIRST, "name": "Already Online"}
    new = {"steamId": SECOND, "name": "New Player"}
    engine.observe(ORIGIN, [old])
    assert engine.status()["pending"] == 0
    engine.observe(ORIGIN, [old, new])
    assert engine.status()["pending"] == 1
    asyncio.run(engine.tick())
    asyncio.run(engine.tick())
    writes = [request for request in requests if request.method == "POST"]
    assert len(writes) == 3  # First stage, then two complete chunks of stage two.
    assert "".join(json.loads(request.content)["message"] for request in writes[1:]) == "B" * 205
    assert all(request.url.path == f"/v1/players/{SECOND}/message" for request in writes)
    assert len(store.recent(ORIGIN)) == 3
    engine.observe(ORIGIN, [old])
    engine.observe(ORIGIN, [old, new])
    assert engine.status()["pending"] == 0
    restarted = RulesEngine(app.state.rcon_runtime, store)
    restarted.observe(ORIGIN, [old])
    restarted.observe(ORIGIN, [old, new])
    assert restarted.status()["pending"] == 0
    restarted._baseline_at = monotonic() - 91
    restarted.observe(ORIGIN, [old, new])
    assert restarted.status()["pending"] == 0
    asyncio.run(app.state.rcon_runtime.close())


def test_rules_configuration_is_owner_only_and_cannot_enable_without_collector(tmp_path):
    panel_origin = "https://panel.example.invalid"
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"routes": []})

    app = create_app(
        PanelSettings(
            db_path=tmp_path / "panel.sqlite3", public_origin=panel_origin,
            session_secure=True,
            rcon_target=RconTarget(origin=ORIGIN, bearer_secret="mock-only"),
        ), rcon_transport=httpx.MockTransport(handler),
    )
    app.state.auth_service.create_admin("Owner", "test-owner-password")
    with TestClient(app, base_url=panel_origin) as client:
        assert client.get("/api/rules").status_code == 401
        assert client.post("/api/auth/login", json={
            "username": "Owner", "password": "test-owner-password",
        }, headers={"Origin": panel_origin}).status_code == 200
        config = client.get("/api/rules").json()
        assert config["enabled"] is False
        body = {"targetRevision": config["targetRevision"], "enabled": False,
                "firstText": "Rule {name}", "secondText": "", "delaySeconds": 60,
                "gapSeconds": 10, "cooldownMinutes": 30, "maxPerRound": 3}
        assert client.put("/api/rules", json=body, headers={"Origin": panel_origin}).status_code == 200
        assert client.put("/api/rules", json={**body, "enabled": True},
                          headers={"Origin": panel_origin}).status_code == 400
        assert client.put("/api/rules", json={**body, "firstText": "{name.__class__}"},
                          headers={"Origin": panel_origin}).status_code == 400
        assert client.post("/api/subusers", json={
            "username": "Viewer", "password": "test-viewer-password",
        }, headers={"Origin": panel_origin}).status_code == 201
        with TestClient(app, base_url=panel_origin) as viewer:
            assert viewer.post("/api/auth/login", json={
                "username": "Viewer", "password": "test-viewer-password",
            }, headers={"Origin": panel_origin}).status_code == 200
            assert viewer.get("/api/rules").status_code == 403
            assert viewer.put("/api/rules", json=body,
                              headers={"Origin": panel_origin}).status_code == 403
    assert requests == []


def test_uncertain_send_is_recorded_once_and_not_retried(tmp_path):
    attempts = []

    def handler(request):
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"routes": [
                "GET /v1/players", "POST /v1/players/{steamId}/message",
            ]})
        if request.method == "POST":
            attempts.append(request)
            raise httpx.ReadTimeout("mock timeout")
        return httpx.Response(200, json={"players": []})

    app = create_app(
        PanelSettings(db_path=tmp_path / "panel.sqlite3",
                      public_origin="http://127.0.0.1:8000", session_secure=False,
                      rcon_target=RconTarget(origin=ORIGIN, bearer_secret="mock-only")),
        rcon_transport=httpx.MockTransport(handler),
    )
    store = app.state.rules_store
    store.save(ORIGIN, enabled=True, first_text="Rules", second_text="",
               delay_seconds=0, gap_seconds=0, cooldown_minutes=30, max_per_round=3)
    engine = app.state.rules_engine
    engine.observe(ORIGIN, [])
    engine.observe(ORIGIN, [{"steamId": SECOND, "name": "New"}])
    asyncio.run(engine.tick())
    assert len(attempts) == 1
    assert store.recent(ORIGIN)[0]["outcome"] == "uncertain"
    engine.observe(ORIGIN, [])
    engine.observe(ORIGIN, [{"steamId": SECOND, "name": "New"}])
    asyncio.run(engine.tick())
    assert len(attempts) == 1
    asyncio.run(app.state.rcon_runtime.close())
