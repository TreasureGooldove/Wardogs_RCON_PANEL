"""Owner/subuser permissions using an in-process mock, never a live RCON server."""

from datetime import datetime, timezone
import sqlite3

from fastapi.testclient import TestClient
import httpx

from app.auth.passwords import hash_password
from app.config import PanelSettings, RconTarget
from app.main import create_app
from app.storage.db import Database


PANEL_ORIGIN = "https://panel.example"
RCON_ORIGIN = "https://rcon.wardogs.invalid"
STEAM_ID = "76561198000000001"
OWNER_PASSWORD = "owner-test-password"
SUB_PASSWORD = "subuser-test-password"
NEW_PASSWORD = "subuser-new-password"


def login(client, username, password):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": PANEL_ORIGIN},
    )


def make_app(tmp_path):
    sent = []

    def handler(request):
        sent.append((request.method, request.url.path))
        if request.url.path == "/v1/capabilities":
            return httpx.Response(
                200,
                json={"routes": [
                    "GET /v1/status", "GET /v1/players",
                    "POST /v1/players/{steamId}/kick", "POST /v1/bans",
                ]},
            )
        if request.url.path == "/v1/status":
            return httpx.Response(200, json={"map": "test-map"})
        if request.url.path == "/v1/players":
            return httpx.Response(200, json={"players": [{
                "steamId": STEAM_ID, "name": "TestPlayer", "faction": "A",
            }]})
        if request.method == "POST":
            return httpx.Response(200, json={"ok": True})
        raise AssertionError("unexpected mock route")

    settings = PanelSettings(
        db_path=tmp_path / "panel.sqlite3",
        public_origin=PANEL_ORIGIN,
        session_secure=True,
        rcon_target=RconTarget(origin=RCON_ORIGIN, bearer_secret="local-only-secret"),
    )
    app = create_app(settings, rcon_transport=httpx.MockTransport(handler))
    app.state.auth_service.create_admin("Owner", OWNER_PASSWORD)
    return app, settings, sent


def test_existing_single_admin_migrates_to_owner_idempotently(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    timestamp = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.executescript("""
            CREATE TABLE admins (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                username_key TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                disabled INTEGER NOT NULL DEFAULT 0 CHECK(disabled IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        connection.execute(
            "INSERT INTO admins VALUES (?, ?, ?, ?, 0, ?, ?)",
            ("legacy-owner", "Owner", "owner", hash_password(OWNER_PASSWORD), timestamp, timestamp),
        )
    database = Database(db_path)
    database.initialize()
    database.initialize()
    owner = database.get_admin_by_username("OWNER")
    assert owner is not None
    assert owner.role == "owner"
    assert owner.can_kick is True and owner.can_ban is True
    settings = PanelSettings(db_path=db_path, public_origin=PANEL_ORIGIN, session_secure=True)
    app = create_app(settings)
    with TestClient(app, base_url=PANEL_ORIGIN) as client:
        response = login(client, "owner", OWNER_PASSWORD)
        assert response.status_code == 200
        assert response.json()["role"] == "owner"
        assert response.json()["canKick"] is True
        assert response.json()["canBan"] is True


def test_default_readonly_subuser_cannot_manage_connection_accounts_or_players(tmp_path):
    app, settings, sent = make_app(tmp_path)
    with TestClient(app, base_url=PANEL_ORIGIN) as owner:
        assert login(owner, "owner", OWNER_PASSWORD).status_code == 200
        denied_origin = owner.post(
            "/api/subusers",
            json={"username": "Scout", "password": SUB_PASSWORD},
        )
        assert denied_origin.status_code == 401
        created = owner.post(
            "/api/subusers",
            json={"username": "Scout", "password": SUB_PASSWORD},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert created.status_code == 201
        body = created.json()
        assert body["role"] == "subuser"
        assert body["canKick"] is False and body["canBan"] is False
        assert body["disabled"] is False
        assert "password" not in created.text and "hash" not in created.text
        assert owner.get("/api/subusers").json()[0]["id"] == body["id"]
        duplicate = owner.post(
            "/api/subusers",
            json={"username": "sCOuT", "password": SUB_PASSWORD},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "username_exists"

        with TestClient(app, base_url=PANEL_ORIGIN) as subuser:
            logged_in = login(subuser, "SCOUT", SUB_PASSWORD)
            assert logged_in.status_code == 200
            assert logged_in.json()["role"] == "subuser"
            assert logged_in.json()["canKick"] is False
            assert logged_in.json()["canBan"] is False
            assert subuser.get("/api/auth/me").json()["role"] == "subuser"
            assert subuser.get("/api/server/status").json()["map"] == "test-map"
            players = subuser.get("/api/server/players")
            assert players.status_code == 200
            revision = players.json()["targetRevision"]
            sent_before_denied = list(sent)
            for path in ("/api/server/settings", "/api/subusers"):
                denied = subuser.get(path)
                assert denied.status_code == 403
                assert denied.json()["code"] == "permission_denied"
            denied_put = subuser.put(
                "/api/server/settings",
                json={"name": "changed", "origin": RCON_ORIGIN, "allowPublicHttp": False},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert denied_put.status_code == 403
            denied_create = subuser.post(
                "/api/subusers",
                json={"username": "other", "password": SUB_PASSWORD},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert denied_create.status_code == 403
            for path in ("/api/server/kicks", "/api/server/bans"):
                denied = subuser.post(
                    path,
                    json={"steamId": STEAM_ID, "reason": "test", "targetRevision": revision},
                    headers={"Origin": PANEL_ORIGIN},
                )
                assert denied.status_code == 403
                assert denied.json()["code"] == "permission_denied"
            assert sent == sent_before_denied
    with sqlite3.connect(settings.db_path) as connection:
        rows = connection.execute("SELECT role, can_kick, can_ban FROM admins ORDER BY role").fetchall()
    assert ("subuser", 0, 0) in rows


def test_grant_kick_revoke_sessions_then_reset_password_and_disable(tmp_path):
    app, _, sent = make_app(tmp_path)
    with TestClient(app, base_url=PANEL_ORIGIN) as owner:
        assert login(owner, "owner", OWNER_PASSWORD).status_code == 200
        created = owner.post(
            "/api/subusers",
            json={"username": "Operator", "password": SUB_PASSWORD},
            headers={"Origin": PANEL_ORIGIN},
        )
        account_id = created.json()["id"]
        with TestClient(app, base_url=PANEL_ORIGIN) as subuser:
            assert login(subuser, "operator", SUB_PASSWORD).status_code == 200
            old_cookie = subuser.cookies["panel_session"]
            patched = owner.patch(
                f"/api/subusers/{account_id}",
                json={"canKick": True},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert patched.status_code == 200
            assert patched.json()["canKick"] is True
            assert patched.json()["canBan"] is False
            assert subuser.get("/api/auth/me").status_code == 401
            assert login(subuser, "OPERATOR", SUB_PASSWORD).status_code == 200
            assert subuser.cookies["panel_session"] != old_cookie
            assert subuser.get("/api/auth/me").json()["canKick"] is True

            players = subuser.get("/api/server/players").json()
            revision = players["targetRevision"]
            denied_ban = subuser.post(
                "/api/server/bans",
                json={"steamId": STEAM_ID, "reason": "test", "targetRevision": revision},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert denied_ban.status_code == 403
            before_kick = sum(method == "POST" for method, _ in sent)
            kicked = subuser.post(
                "/api/server/kicks",
                json={"steamId": STEAM_ID, "reason": "test", "targetRevision": revision},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert kicked.status_code == 200
            assert sum(method == "POST" for method, _ in sent) == before_kick + 1

            downgraded = owner.patch(
                f"/api/subusers/{account_id}",
                json={"canKick": False},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert downgraded.status_code == 200
            assert downgraded.json()["canKick"] is False
            assert subuser.get("/api/auth/me").status_code == 401
            assert login(subuser, "operator", SUB_PASSWORD).status_code == 200
            denied_again = subuser.post(
                "/api/server/kicks",
                json={"steamId": STEAM_ID, "reason": "test", "targetRevision": revision},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert denied_again.status_code == 403
            assert sum(method == "POST" for method, _ in sent) == before_kick + 1

            reset = owner.post(
                f"/api/subusers/{account_id}/reset-password",
                json={"password": NEW_PASSWORD},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert reset.status_code == 204
            assert subuser.get("/api/auth/me").status_code == 401
            assert login(subuser, "operator", SUB_PASSWORD).status_code == 401
            assert login(subuser, "operator", NEW_PASSWORD).status_code == 200

            disabled = owner.patch(
                f"/api/subusers/{account_id}",
                json={"disabled": True},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert disabled.status_code == 200
            assert disabled.json()["disabled"] is True
            assert subuser.get("/api/auth/me").status_code == 401
            assert login(subuser, "operator", NEW_PASSWORD).status_code == 401
        assert owner.get("/api/auth/me").status_code == 200
        assert owner.patch(
            f"/api/subusers/{owner.get('/api/auth/me').json()['id']}",
            json={"disabled": True}, headers={"Origin": PANEL_ORIGIN},
        ).status_code == 404


def test_ban_grant_does_not_grant_kick(tmp_path):
    app, _, sent = make_app(tmp_path)
    with TestClient(app, base_url=PANEL_ORIGIN) as owner:
        assert login(owner, "owner", OWNER_PASSWORD).status_code == 200
        created = owner.post(
            "/api/subusers",
            json={"username": "BanOnly", "password": SUB_PASSWORD, "canBan": True},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert created.status_code == 201
        with TestClient(app, base_url=PANEL_ORIGIN) as subuser:
            authenticated = login(subuser, "banonly", SUB_PASSWORD)
            assert authenticated.status_code == 200
            assert authenticated.json()["canKick"] is False
            assert authenticated.json()["canBan"] is True
            revision = subuser.get("/api/server/players").json()["targetRevision"]
            denied = subuser.post(
                "/api/server/kicks",
                json={"steamId": STEAM_ID, "targetRevision": revision},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert denied.status_code == 403
            assert ("POST", f"/v1/players/{STEAM_ID}/kick") not in sent
            accepted = subuser.post(
                "/api/server/bans",
                json={"steamId": STEAM_ID, "targetRevision": revision},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert accepted.status_code == 200
            assert ("POST", "/v1/bans") in sent
