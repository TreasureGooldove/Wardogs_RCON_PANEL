"""Single-server settings use only local mock RCON traffic."""

import asyncio
import sqlite3
from datetime import datetime, timezone

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import PanelSettings, RconTarget
from app.main import create_app
from app.rcon.runtime import RconRuntime, RuntimeReadService
from app.storage.db import Database


ORIGIN = "https://panel.example"
OLD = "https://old.wardogs.invalid"
NEW = "https://new.wardogs.invalid"
SECRET = "settings-local-test-secret"


def make_panel(tmp_path, *, key=True, public_http=False, target=None):
    settings = PanelSettings(
        db_path=tmp_path / "panel.sqlite3",
        public_origin=ORIGIN,
        session_secure=True,
        rcon_target=target,
        config_key=Fernet.generate_key().decode() if key else None,
        allow_public_http_rcon=public_http,
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.host, request.url.path))
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"routes": ["GET /v1/status"]})
        if request.url.path == "/v1/status":
            return httpx.Response(200, json={"map": request.url.host})
        raise AssertionError(f"unexpected mock route {request.url.path}")

    app = create_app(settings, rcon_transport=httpx.MockTransport(handler))
    app.state.auth_service.create_admin("admin", "correct horse battery staple")
    return app, settings, calls


def login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct horse battery staple"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


def payload(origin=OLD, bearer=SECRET, *, public_http=False):
    data = {"name": "主服务器", "origin": origin, "allowPublicHttp": public_http}
    if bearer is not None:
        data["bearer"] = bearer
    return data


def test_settings_auth_origin_encryption_and_hot_switch(tmp_path):
    app, settings, calls = make_panel(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/server/settings").status_code == 401
        login(client)
        empty = client.get("/api/server/settings").json()
        assert empty == {
            "name": "Wardogs 服务器", "origin": "", "hasBearer": False,
            "allowPublicHttp": False, "configured": False, "updatedAt": None,
        }
        for headers in ({}, {"Origin": "https://other.example"}):
            denied = client.put("/api/server/settings", json=payload(), headers=headers)
            assert denied.status_code == 401
            assert denied.json()["code"] == "not_authenticated"
        assert calls == []

        first = client.put("/api/server/settings", json=payload(), headers={"Origin": ORIGIN})
        assert first.status_code == 200
        assert first.json()["configured"] is True
        assert first.json()["hasBearer"] is True
        assert first.json()["updatedAt"]
        assert SECRET not in first.text
        assert client.get("/api/server/status").json()["map"] == "old.wardogs.invalid"

        changed = client.put(
            "/api/server/settings",
            json=payload(NEW, "new-secret"),
            headers={"Origin": ORIGIN},
        )
        assert changed.status_code == 200
        assert client.get("/api/server/status").json()["map"] == "new.wardogs.invalid"
        assert any(host == "old.wardogs.invalid" for _, host, _ in calls)
        assert any(host == "new.wardogs.invalid" for _, host, _ in calls)

    with sqlite3.connect(settings.db_path) as connection:
        ciphertext = connection.execute(
            "SELECT bearer_ciphertext FROM server_settings WHERE id = 1"
        ).fetchone()[0]
    assert ciphertext.startswith("gAAAA")
    assert SECRET not in settings.db_path.read_bytes().decode("utf-8", errors="ignore")
    assert "new-secret" not in settings.db_path.read_bytes().decode("utf-8", errors="ignore")
    assert ciphertext != "new-secret"
    reopened = create_app(settings, rcon_transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))
    assert reopened.state.rcon_runtime.target.origin == NEW


def test_origin_change_requires_new_bearer_and_missing_key_fails_closed(tmp_path):
    app, _, _ = make_panel(tmp_path)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        assert client.put("/api/server/settings", json=payload(), headers={"Origin": ORIGIN}).status_code == 200
        missing = client.put(
            "/api/server/settings", json=payload(NEW, None), headers={"Origin": ORIGIN}
        )
        assert missing.status_code == 400
        assert missing.json()["code"] == "settings_bearer_required"
        assert client.get("/api/server/settings").json()["origin"] == OLD
        retained = client.put(
            "/api/server/settings", json=payload(OLD, None), headers={"Origin": ORIGIN}
        )
        assert retained.status_code == 200

    no_key, _, _ = make_panel(tmp_path / "other", key=False)
    with TestClient(no_key, base_url=ORIGIN) as client:
        login(client)
        rejected = client.put("/api/server/settings", json=payload(), headers={"Origin": ORIGIN})
        assert rejected.status_code == 503
        assert rejected.json()["code"] == "settings_key_unavailable"


def test_public_http_requires_per_server_opt_in_and_deployment_gate(tmp_path):
    address = "http://198.51.100.42:9191"
    app, _, _ = make_panel(tmp_path / "closed")
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        denied = client.put(
            "/api/server/settings", json=payload(address, public_http=True),
            headers={"Origin": ORIGIN},
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "settings_public_http_disabled"

    app, _, _ = make_panel(tmp_path / "open", public_http=True)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        not_opted_in = client.put(
            "/api/server/settings", json=payload(address), headers={"Origin": ORIGIN}
        )
        assert not_opted_in.status_code == 400
        assert not_opted_in.json()["code"] == "invalid_settings"
        accepted = client.put(
            "/api/server/settings", json=payload(address, public_http=True),
            headers={"Origin": ORIGIN},
        )
        assert accepted.status_code == 200
        assert accepted.json()["allowPublicHttp"] is True
        assert accepted.json()["origin"] == address
        assert SECRET not in accepted.text


def test_env_fallback_and_lost_key_do_not_expose_secret(tmp_path):
    target = RconTarget(origin=OLD, bearer_secret=SECRET)
    app, settings, _ = make_panel(tmp_path, target=target)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        view = client.get("/api/server/settings").json()
        assert view["origin"] == OLD
        assert view["hasBearer"] is True
        assert SECRET not in str(view)
        assert client.put(
            "/api/server/settings", json=payload(OLD, None), headers={"Origin": ORIGIN}
        ).status_code == 200
    wrong_key = PanelSettings(
        db_path=settings.db_path, public_origin=ORIGIN, session_secure=True,
        config_key=Fernet.generate_key().decode(),
    )
    reopened = create_app(wrong_key)
    assert reopened.state.rcon_runtime.target is None


def test_audit_records_metadata_only(tmp_path):
    database = Database(tmp_path / "audit.sqlite3")
    database.initialize()
    admin = database.create_admin("admin", "hash", datetime.now(timezone.utc))
    database.append_moderation_audit(admin.id, "kick", "76561198000000000", "accepted", "test reason", "request-1")
    with sqlite3.connect(database.path) as connection:
        row = connection.execute(
            "SELECT admin_id, action, steam_id, outcome, reason, request_id, created_at FROM moderation_audit"
        ).fetchone()
    assert row[:6] == (admin.id, "kick", "76561198000000000", "accepted", "test reason", "request-1")
    assert row[6]


def test_existing_audit_table_adds_target_identity_without_losing_rows(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE moderation_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id TEXT NOT NULL,
                action TEXT NOT NULL,
                steam_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                reason TEXT NOT NULL,
                request_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """INSERT INTO moderation_audit
               (admin_id, action, steam_id, outcome, reason, request_id, created_at)
               VALUES ('old-admin', 'kick', '76561198000000000', 'accepted', 'old', 'old-request', '2026-01-01')"""
        )
    Database(path).initialize()
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT steam_id, reason, target_revision, target_origin FROM moderation_audit"
        ).fetchone()
    assert row == ("76561198000000000", "old", "", "")


@pytest.mark.asyncio
async def test_target_switch_waits_for_inflight_read_and_discards_old_cache(tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"routes": ["GET /v1/status"]})
        if request.url.host == "old.wardogs.invalid":
            entered.set()
            await release.wait()
        return httpx.Response(200, json={"map": request.url.host})

    settings = PanelSettings(
        db_path=tmp_path / "panel.sqlite3", public_origin=ORIGIN, session_secure=True,
        rcon_target=RconTarget(origin=OLD, bearer_secret=SECRET),
        config_key=Fernet.generate_key().decode(),
    )
    database = Database(settings.db_path)
    database.initialize()
    runtime = RconRuntime(settings, database, transport=httpx.MockTransport(handler))
    reads = RuntimeReadService(runtime)
    try:
        old_read = asyncio.create_task(reads.status())
        await asyncio.wait_for(entered.wait(), timeout=2)
        update = asyncio.create_task(
            runtime.save(name="新服务器", origin=NEW, bearer="new-secret", allow_public_http=False)
        )
        await asyncio.sleep(0)
        assert not update.done()
        release.set()
        assert (await old_read)["map"] == "old.wardogs.invalid"
        assert (await update)["origin"] == NEW
        assert (await reads.status())["map"] == "new.wardogs.invalid"
    finally:
        release.set()
        await runtime.close()
