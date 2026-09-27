"""Manual warnings are single-attempt messages with local, scoped history."""

import json

from fastapi.testclient import TestClient
import httpx

from app.config import PanelSettings, RconTarget
from app.main import create_app


ORIGIN = "https://panel.example"
TARGET = "https://rcon.wardogs.invalid"
STEAM_ID = "76561198000000001"
PASSWORD = "owner-test-password"
SUB_PASSWORD = "viewer-test-password"


def make_panel(tmp_path, *, advertised=None, on_write=None):
    sent = []
    players = [{"steamId": STEAM_ID, "name": "Live Player", "faction": "Lonestar"}]
    routes = advertised if advertised is not None else [
        "GET /v1/players", "POST /v1/players/{steamId}/message",
    ]

    def handler(request):
        sent.append(request)
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"routes": routes})
        if request.url.path == "/v1/players":
            return httpx.Response(200, json={"players": list(players)})
        if on_write is not None:
            return on_write(request)
        return httpx.Response(200, json={"message": "delivered"})

    app = create_app(
        PanelSettings(
            db_path=tmp_path / "panel.sqlite3", public_origin=ORIGIN,
            session_secure=True,
            rcon_target=RconTarget(origin=TARGET, bearer_secret="local-test-secret"),
        ),
        rcon_transport=httpx.MockTransport(handler),
    )
    owner = app.state.auth_service.create_admin("Owner", PASSWORD)
    return app, owner, players, sent


def login(client, username="owner", password=PASSWORD):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


def warning(client, revision, *, reason="请遵守服务器规则"):
    return client.post(
        "/api/server/warnings",
        json={"steamId": STEAM_ID, "reason": reason, "targetRevision": revision},
        headers={"Origin": ORIGIN},
    )


def test_warning_sends_exact_message_once_and_local_history_is_login_readable(tmp_path):
    app, owner_account, _, sent = make_panel(tmp_path)
    with TestClient(app, base_url=ORIGIN) as owner:
        assert owner.get(f"/api/server/warnings?steamId={STEAM_ID}").status_code == 401
        assert owner.post("/api/server/warnings", json={}).status_code == 401
        assert sent == []
        login(owner)
        revision = owner.get("/api/server/players").json()["targetRevision"]
        app.state.database.append_moderation_audit(
            owner_account.id, "warning", STEAM_ID, "accepted", "other server",
            "other-request", revision, "https://other-rcon.invalid",
        )
        sent_result = warning(owner, revision)
        assert sent_result.status_code == 200
        assert sent_result.json() == {"ok": True, "recorded": True}
        upstream_writes = [request for request in sent if request.method == "POST"]
        assert len(upstream_writes) == 1
        assert upstream_writes[0].url.path == f"/v1/players/{STEAM_ID}/message"
        assert json.loads(upstream_writes[0].content) == {
            "message": "【管理员警告】请遵守服务器规则",
        }
        assert len([request for request in sent if request.url.path == "/v1/players"]) == 2
        assert owner.post(
            "/api/subusers",
            json={"username": "Viewer", "password": SUB_PASSWORD,
                  "canKick": True, "canBan": True},
            headers={"Origin": ORIGIN},
        ).status_code == 201

        with TestClient(app, base_url=ORIGIN) as viewer:
            login(viewer, "viewer", SUB_PASSWORD)
            history = viewer.get(f"/api/server/warnings?steamId={STEAM_ID}")
            assert history.status_code == 200
            assert history.headers["cache-control"] == "no-store"
            data = history.json()
            assert data["steamId"] == STEAM_ID
            assert data["count"] == 1
            assert data["source"] == "panel_local"
            assert data["targetRevision"] == revision
            assert data["stale"] is False
            assert data["observedAt"].endswith("Z")
            assert len(data["entries"]) == 1
            assert data["entries"][0]["reason"] == "请遵守服务器规则"
            assert data["entries"][0]["actor"] == "Owner"
            assert data["entries"][0]["outcome"] == "accepted"
            before = len(sent)
            denied = warning(viewer, revision)
            assert denied.status_code == 403
            assert len(sent) == before


def test_warning_fails_closed_for_origin_revision_reason_online_and_capability(tmp_path):
    app, _, players, sent = make_panel(tmp_path)
    with TestClient(app, base_url=ORIGIN) as owner:
        login(owner)
        revision = owner.get("/api/server/players").json()["targetRevision"]
        before = len(sent)
        no_origin = owner.post(
            "/api/server/warnings",
            json={"steamId": STEAM_ID, "reason": "test", "targetRevision": revision},
        )
        stale = warning(owner, "old-target")
        empty = warning(owner, revision, reason="  ")
        too_long = warning(owner, revision, reason="x" * 194)
        assert no_origin.status_code == 401
        assert stale.status_code == 409
        assert (empty.status_code, empty.json()["code"]) == (400, "invalid_moderation_reason")
        assert (too_long.status_code, too_long.json()["code"]) == (400, "invalid_moderation_reason")
        assert len(sent) == before
        players.clear()
        offline = warning(owner, revision)
        assert (offline.status_code, offline.json()["code"]) == (409, "player_not_online")
        assert not [request for request in sent if request.method == "POST"]
        history = owner.get(f"/api/server/warnings?steamId={STEAM_ID}").json()
        assert history["count"] == 0
        assert [row["outcome"] for row in history["entries"]] == ["rejected"]
        assert owner.get("/api/server/warnings?steamId=bad").json()["code"] == "invalid_moderation_target"

    app, _, _, sent = make_panel(tmp_path / "unsupported", advertised=["GET /v1/players"])
    with TestClient(app, base_url=ORIGIN) as owner:
        login(owner)
        revision = owner.get("/api/server/players").json()["targetRevision"]
        before = len(sent)
        unsupported = warning(owner, revision)
        assert (unsupported.status_code, unsupported.json()["code"]) == (501, "action_unsupported")
        assert len(sent) == before


def test_warning_timeout_is_uncertain_and_never_counted_or_retried(tmp_path):
    writes = []

    def timeout(request):
        writes.append(request)
        raise httpx.ReadTimeout("simulated timeout", request=request)

    app, _, _, _ = make_panel(tmp_path, on_write=timeout)
    with TestClient(app, base_url=ORIGIN) as owner:
        login(owner)
        revision = owner.get("/api/server/players").json()["targetRevision"]
        response = warning(owner, revision)
        assert (response.status_code, response.json()["code"]) == (502, "action_uncertain")
        assert len(writes) == 1
        history = owner.get(f"/api/server/warnings?steamId={STEAM_ID}").json()
        assert history["count"] == 0
        assert len(history["entries"]) == 1
        assert history["entries"][0]["outcome"] == "uncertain"


def test_warning_audit_failure_does_not_encourage_repeat_send(tmp_path, monkeypatch):
    app, _, _, sent = make_panel(tmp_path)
    with TestClient(app, base_url=ORIGIN) as owner:
        login(owner)
        revision = owner.get("/api/server/players").json()["targetRevision"]

        def audit_unavailable(*_args, **_kwargs):
            raise OSError("simulated local audit failure")

        monkeypatch.setattr(app.state.database, "append_moderation_audit", audit_unavailable)
        response = warning(owner, revision)
        assert response.status_code == 200
        assert response.json() == {"ok": True, "recorded": False}
        assert len([request for request in sent if request.method == "POST"]) == 1
