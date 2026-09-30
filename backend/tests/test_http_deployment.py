"""Public HTTP login and transport defaults, using only temporary local state."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app


@pytest.mark.parametrize("scheme,secure", [("http", False), ("https", True)])
def test_public_origin_login_and_startup_warning(tmp_path, caplog, scheme, secure):
    origin = f"{scheme}://panel.example.invalid:23333"
    settings = load_settings({"PANEL_PUBLIC_ORIGIN": origin,
                              "PANEL_DB_PATH": str(tmp_path / "panel.sqlite3"),
                              "PANEL_HISTORY_ENABLED": "false"})
    assert settings.session_secure is secure
    app = create_app(settings)
    app.state.auth_service.create_admin("admin", "local test password only")
    with caplog.at_level(logging.WARNING), TestClient(app, base_url=origin) as client:
        response = client.post("/api/auth/login", headers={"Origin": origin},
                               json={"username": "admin", "password": "local test password only"})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert ("secure" in cookie) is secure
        assert client.get("/api/auth/me").status_code == 200
        assert client.post("/api/auth/logout", headers={"Origin": "http://attacker.invalid"}).status_code == 401
        assert client.get("/api/auth/me").status_code == 200
        assert client.post("/api/auth/logout", headers={"Origin": origin}).status_code == 204
    assert ("您未部署在https版本 请留意数据安全" in caplog.text) is (not secure)


@pytest.mark.parametrize("origin,flag", [("http://panel.example.invalid", "true"),
                                        ("https://panel.example.invalid", "false")])
def test_explicit_cookie_transport_mismatch_is_rejected(origin, flag):
    with pytest.raises(ValueError):
        load_settings({"PANEL_PUBLIC_ORIGIN": origin, "PANEL_SESSION_SECURE": flag})
