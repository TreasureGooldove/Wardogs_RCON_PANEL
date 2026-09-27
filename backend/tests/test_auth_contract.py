"""Panel identity contract; all data here is local test data."""

from datetime import datetime, timedelta, timezone
import sqlite3

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.auth import build_auth_router
from app.auth.sessions import AuthService
from app.cli import main as cli_main
from app.config import PanelSettings, RconTarget, load_settings
from app.errors import install_error_handlers
from app.storage.db import Database


@pytest.fixture
def panel(tmp_path):
    now = [datetime(2026, 9, 27, tzinfo=timezone.utc)]
    settings = PanelSettings(
        db_path=tmp_path / "panel.sqlite3",
        public_origin="https://panel.example",
        session_secure=True,
    )
    database = Database(settings.db_path)
    database.initialize()
    auth = AuthService(database, settings, clock=lambda: now[0])
    auth.create_admin("Admin", "correct horse battery staple")

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(build_auth_router(auth))

    @app.get("/api/server/status")
    def protected(_admin=Depends(auth.require_admin)):
        return {"ok": True}

    with TestClient(app, base_url=settings.public_origin) as client:
        yield client, database, now


def login(client, password="correct horse battery staple", origin="https://panel.example"):
    return client.post(
        "/api/auth/login",
        json={"username": "admin", "password": password},
        headers={"Origin": origin},
    )


def assert_error(response, code, status):
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"code", "message", "requestId"}
    assert body["code"] == code
    assert body["requestId"]


def test_business_endpoint_requires_session(panel):
    client, _, _ = panel
    assert_error(client.get("/api/server/status"), "not_authenticated", 401)
    assert_error(client.get("/api/auth/me"), "not_authenticated", 401)


def test_wrong_password_does_not_create_session(panel):
    client, _, _ = panel
    assert_error(login(client, "incorrect"), "invalid_credentials", 401)
    assert "panel_session" not in client.cookies
    assert_error(client.get("/api/server/status"), "not_authenticated", 401)


def test_invalid_login_body_does_not_echo_password(panel):
    client, _, _ = panel
    response = client.post(
        "/api/auth/login",
        json={"username": "x", "password": "super-secret-submitted-value"},
        headers={"Origin": "https://panel.example"},
    )
    assert_error(response, "invalid_credentials", 401)
    assert "super-secret-submitted-value" not in response.text


def test_login_cookie_and_logout_revoke_session(panel):
    client, database, _ = panel
    response = login(client)
    assert response.status_code == 200
    assert set(response.json()) == {"id", "username", "role", "canKick", "canBan", "permissions"}
    assert response.json()["username"] == "Admin"
    assert response.json()["role"] == "owner"
    assert response.json()["canKick"] is True
    assert response.json()["canBan"] is True
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=strict" in cookie
    assert "panel_session" in client.cookies
    assert client.get("/api/server/status").json() == {"ok": True}

    token = client.cookies["panel_session"]
    with sqlite3.connect(database.path) as connection:
        stored_hash = connection.execute("SELECT token_hash FROM sessions").fetchone()[0]
        password_hash = connection.execute("SELECT password_hash FROM admins").fetchone()[0]
    assert token != stored_hash
    assert password_hash.startswith("$argon2id$")
    assert token not in database.path.read_bytes().decode("utf-8", errors="ignore")
    logout = client.post("/api/auth/logout", headers={"Origin": "https://panel.example"})
    assert logout.status_code == 204
    assert_error(client.get("/api/auth/me"), "not_authenticated", 401)
    client.cookies.set("panel_session", token)
    assert_error(client.get("/api/server/status"), "not_authenticated", 401)


def test_expired_or_disabled_admin_session_is_rejected(panel):
    client, database, now = panel
    assert login(client).status_code == 200
    now[0] += timedelta(hours=9)
    assert_error(client.get("/api/auth/me"), "not_authenticated", 401)

    now[0] -= timedelta(hours=9)
    assert login(client).status_code == 200
    admin = database.get_admin_by_username("admin")
    assert admin is not None
    database.disable_admin(admin.id)
    assert_error(client.get("/api/server/status"), "not_authenticated", 401)


def test_origin_mismatch_and_login_rate_limit(panel):
    client, _, _ = panel
    assert_error(login(client, origin="https://attacker.example"), "not_authenticated", 401)
    for _ in range(5):
        assert_error(login(client, "incorrect"), "invalid_credentials", 401)
    assert_error(login(client, "incorrect"), "rate_limited", 429)
    assert "panel_session" not in client.cookies


def test_rcon_target_is_fixed_and_http_requires_private_host():
    for origin in (
        "https://user@host.example",
        "https://host.example/v1",
        "https://host.example?x=1",
        "https://host.example#fragment",
        "ftp://host.example",
    ):
        with pytest.raises(ValueError):
            RconTarget(origin=origin, bearer_secret="local-test-only")
    with pytest.raises(ValueError):
        RconTarget(origin="http://192.168.1.5:8080", bearer_secret="local-test-only")
    with pytest.raises(ValueError):
        RconTarget(
            origin="http://public.example:8080",
            bearer_secret="local-test-only",
            allow_private_http=True,
        )
    target = RconTarget(
        origin="http://192.168.1.5:8080",
        bearer_secret="local-test-only",
        allow_private_http=True,
    )
    assert "local-test-only" not in repr(target)
    assert load_settings({"WARDOGS_RCON_ORIGIN": "http://public.example", "WARDOGS_RCON_BEARER": "secret"}).rcon_target is None


def test_initial_admin_is_unique_case_insensitively(panel):
    _, database, now = panel
    assert database.get_admin_by_username("ADMIN").username == "Admin"
    with pytest.raises(ValueError, match="already exists"):
        database.create_admin("second", "$argon2id$local-test", now[0])


def test_interactive_cli_initializes_one_admin(tmp_path, monkeypatch, capsys):
    settings = PanelSettings(
        db_path=tmp_path / "cli.sqlite3",
        public_origin="http://127.0.0.1:8000",
        session_secure=False,
    )
    monkeypatch.setattr("app.cli.load_settings", lambda: settings)
    monkeypatch.setattr("builtins.input", lambda _prompt: "Admin")
    passwords = iter(["correct horse battery staple"] * 4)
    monkeypatch.setattr("app.cli.getpass", lambda _prompt: next(passwords))

    assert cli_main(["create-admin"]) == 0
    assert Database(settings.db_path).get_admin_by_username("admin") is not None
    assert cli_main(["create-admin"]) == 1
    captured = capsys.readouterr()
    assert "correct horse battery staple" not in captured.out + captured.err
