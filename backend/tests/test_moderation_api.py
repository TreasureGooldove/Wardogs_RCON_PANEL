"""Moderation HTTP contract with an in-process upstream; no live RCON calls."""

import json
import sqlite3

from fastapi.testclient import TestClient
import httpx
import pytest
from cryptography.fernet import Fernet

from app.config import PanelSettings, RconTarget
from app.main import create_app


PANEL_ORIGIN = "https://panel.example"
RCON_ORIGIN = "https://rcon.wardogs.invalid"
SECRET = "local-test-bearer-never-log"
STEAM_ID = "76561198000000001"


def panel(tmp_path, handler):
    settings = PanelSettings(
        db_path=tmp_path / "panel.sqlite3",
        public_origin=PANEL_ORIGIN,
        session_secure=True,
        rcon_target=RconTarget(origin=RCON_ORIGIN, bearer_secret=SECRET),
    )
    app = create_app(settings, rcon_transport=httpx.MockTransport(handler))
    app.state.auth_service.create_admin("admin", "correct horse battery staple")
    return app, settings


def login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct horse battery staple"},
        headers={"Origin": PANEL_ORIGIN},
    )
    assert response.status_code == 200


def capability_response(*, write=True):
    routes = ["GET /v1/players"]
    if write:
        routes.extend(["POST /v1/players/{steamId}/kick", "POST /v1/bans"])
    return httpx.Response(200, json={"routes": routes})


def audit_rows(path):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT action, steam_id, outcome, reason, request_id, target_revision, target_origin FROM moderation_audit ORDER BY id"
        ).fetchall()


def test_moderation_requires_session_origin_and_valid_target(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return capability_response()

    app, settings = panel(tmp_path, handler)
    revision = app.state.rcon_runtime.target_revision
    with TestClient(app, base_url=PANEL_ORIGIN) as client:
        unauth = client.post(
            "/api/server/kicks", json={"steamId": STEAM_ID, "reason": "test", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert unauth.status_code == 401
        login(client)
        for headers in ({}, {"Origin": "https://other.example"}):
            denied = client.post(
                "/api/server/kicks", json={"steamId": STEAM_ID, "reason": "test", "targetRevision": revision},
                headers=headers,
            )
            assert denied.status_code == 401
            assert denied.json()["code"] == "not_authenticated"
        bad_target = client.post(
            "/api/server/kicks", json={"steamId": "not-steam", "reason": "test", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert bad_target.status_code == 400
        assert bad_target.json()["code"] == "invalid_moderation_target"
        bad_reason = client.post(
            "/api/server/kicks", json={"steamId": STEAM_ID, "reason": "line\nbreak", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert bad_reason.status_code == 400
        assert bad_reason.json()["code"] == "invalid_moderation_reason"
        invalid_body = client.post(
            "/api/server/bans", json={"steamId": STEAM_ID, "reason": "x" * 201, "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert invalid_body.status_code == 400
        assert invalid_body.json()["code"] == "invalid_moderation_reason"
    assert calls == []
    assert audit_rows(settings.db_path) == []


def test_unadvertised_moderation_action_never_posts(tmp_path):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        assert request.url.path == "/v1/capabilities"
        return capability_response(write=False)

    app, settings = panel(tmp_path, handler)
    revision = app.state.rcon_runtime.target_revision
    with TestClient(app, base_url=PANEL_ORIGIN) as client:
        login(client)
        response = client.post(
            "/api/server/bans", json={"steamId": STEAM_ID, "reason": "test", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
    assert response.status_code == 501
    assert response.json()["code"] == "action_unsupported"
    assert calls == [("GET", "/v1/capabilities")]
    assert audit_rows(settings.db_path)[0][:3] == ("ban", STEAM_ID, "rejected")


@pytest.mark.parametrize(
    ("action", "api_path", "upstream_path"),
    [
        ("kick", "/api/server/kicks", f"/v1/players/{STEAM_ID}/kick"),
        ("ban", "/api/server/bans", "/v1/bans"),
    ],
)
def test_accepted_moderation_posts_once_invalidates_players_and_audits(
    tmp_path, action, api_path, upstream_path
):
    calls = []
    players = [{"steamId": STEAM_ID, "name": "TestPlayer", "faction": "A"}]

    def handler(request):
        calls.append(request)
        if request.url.path == "/v1/capabilities":
            return capability_response()
        if request.url.path == "/v1/players":
            return httpx.Response(200, json={"players": list(players)})
        assert request.method == "POST"
        assert request.url.path == upstream_path
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        body = json.loads(request.content)
        assert body["reason"] == "test reason"
        if action == "ban":
            assert body["steamId"] == STEAM_ID
        else:
            assert "steamId" not in body
        players.clear()
        return httpx.Response(200, json={"ok": True})

    app, settings = panel(tmp_path, handler)
    with TestClient(app, base_url=PANEL_ORIGIN) as client:
        login(client)
        first = client.get("/api/server/players")
        assert len(first.json()["players"]) == 1
        revision = first.json()["targetRevision"]
        accepted = client.post(
            api_path, json={"steamId": STEAM_ID, "reason": "test reason", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert accepted.status_code == 200
        assert accepted.json() == {"ok": True}
        assert client.get("/api/server/players").json()["players"] == []

    assert sum(request.method == "POST" for request in calls) == 1
    assert sum(request.url.path == "/v1/players" for request in calls) == 2
    rows = audit_rows(settings.db_path)
    assert len(rows) == 1
    assert rows[0][:4] == (action, STEAM_ID, "accepted", "test reason")
    assert rows[0][4] == accepted.headers["X-Request-ID"]
    assert rows[0][5] == revision
    assert rows[0][6] == RCON_ORIGIN
    assert SECRET not in settings.db_path.read_bytes().decode("utf-8", errors="ignore")


def test_write_timeout_is_uncertain_and_never_retried(tmp_path):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/capabilities":
            return capability_response()
        assert request.method == "POST"
        raise httpx.ReadTimeout("simulated uncertain result", request=request)

    app, settings = panel(tmp_path, handler)
    revision = app.state.rcon_runtime.target_revision
    with TestClient(app, base_url=PANEL_ORIGIN) as client:
        login(client)
        response = client.post(
            "/api/server/kicks", json={"steamId": STEAM_ID, "reason": "test", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
    assert response.status_code == 502
    assert response.json()["code"] == "action_uncertain"
    assert calls == [
        ("GET", "/v1/capabilities"),
        ("POST", f"/v1/players/{STEAM_ID}/kick"),
    ]
    assert audit_rows(settings.db_path)[0][:4] == ("kick", STEAM_ID, "uncertain", "test")
    assert audit_rows(settings.db_path)[0][5] == revision
    assert audit_rows(settings.db_path)[0][6] == RCON_ORIGIN


def test_target_switch_rejects_old_unknown_and_missing_revisions_without_writes(tmp_path):
    calls: list[tuple[str, str, str]] = []

    def handler(request):
        calls.append((request.method, request.url.host, request.url.path))
        if request.url.path == "/v1/capabilities":
            return capability_response()
        if request.url.path == "/v1/players":
            return httpx.Response(200, json={"players": [{"steamId": STEAM_ID, "name": "A player"}]})
        assert request.method == "POST"
        return httpx.Response(200, json={"ok": True})

    settings = PanelSettings(
        db_path=tmp_path / "panel.sqlite3",
        public_origin=PANEL_ORIGIN,
        session_secure=True,
        rcon_target=RconTarget(origin=RCON_ORIGIN, bearer_secret=SECRET),
        config_key=Fernet.generate_key().decode(),
    )
    app = create_app(settings, rcon_transport=httpx.MockTransport(handler))
    app.state.auth_service.create_admin("admin", "correct horse battery staple")
    new_origin = "https://other.wardogs.invalid"
    with TestClient(app, base_url=PANEL_ORIGIN) as client:
        login(client)
        old_snapshot = client.get("/api/server/players")
        assert old_snapshot.status_code == 200
        old_revision = old_snapshot.json()["targetRevision"]
        changed = client.put(
            "/api/server/settings",
            json={"name": "Server B", "origin": new_origin, "bearer": "new-test-secret"},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert changed.status_code == 200
        assert app.state.rcon_runtime.target_revision != old_revision

        for path in ("/api/server/kicks", "/api/server/bans"):
            for candidate in (old_revision, "unknown", "", None):
                payload = {"steamId": STEAM_ID, "reason": "test"}
                if candidate is not None:
                    payload["targetRevision"] = candidate
                denied = client.post(path, json=payload, headers={"Origin": PANEL_ORIGIN})
                assert denied.status_code == 409
                assert denied.json()["code"] == "stale_server_target"

        assert not any(method == "POST" for method, _, _ in calls)
        assert audit_rows(settings.db_path) == []

        new_snapshot = client.get("/api/server/players")
        new_revision = new_snapshot.json()["targetRevision"]
        assert new_revision != old_revision
        accepted = client.post(
            "/api/server/kicks",
            json={"steamId": STEAM_ID, "reason": "test", "targetRevision": new_revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert accepted.status_code == 200

    assert [(method, host, path) for method, host, path in calls if method == "POST"] == [
        ("POST", "other.wardogs.invalid", f"/v1/players/{STEAM_ID}/kick")
    ]
    assert audit_rows(settings.db_path)[0][5] == new_revision
    assert audit_rows(settings.db_path)[0][6] == new_origin
