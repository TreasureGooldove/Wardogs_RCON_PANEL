"""Diagnostic routes use local MockTransport and reveal only safe fields."""

import httpx
from fastapi.testclient import TestClient
import pytest

from app.config import PanelSettings, RconTarget
from app.main import create_app
from app.rcon.diagnostics import project_sponsor


PANEL_ORIGIN = "https://panel.example"


def make_app(tmp_path, *, advertised=None, payloads=None):
    sent = []
    paths = advertised if advertised is not None else [
        "GET /v1/health", "GET /v1/server-id", "GET /v1/sponsor"
    ]
    responses = payloads or {}

    def handler(request):
        sent.append((request.method, request.url.path))
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"routes": paths})
        if request.url.path not in responses:
            raise AssertionError(f"unexpected upstream path: {request.url.path}")
        return httpx.Response(200, json=responses[request.url.path])

    app = create_app(
        PanelSettings(
            db_path=tmp_path / "panel.sqlite3",
            public_origin=PANEL_ORIGIN,
            session_secure=True,
            rcon_target=RconTarget(
                origin="https://rcon.wardogs.invalid", bearer_secret="local-test-secret"
            ),
        ),
        rcon_transport=httpx.MockTransport(handler),
    )
    app.state.auth_service.create_admin("Owner", "owner-test-password")
    return app, sent


def login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "owner-test-password"},
        headers={"Origin": PANEL_ORIGIN},
    )
    assert response.status_code == 200


def test_diagnostics_require_login_and_project_only_bounded_fields(tmp_path):
    app, sent = make_app(tmp_path, payloads={
        "/v1/health": {
            "status": "degraded", "ok": False, "uptimeSeconds": 123,
            "connections": {"active": 6, "secret": "private"},
            "gameThreadQueue": {"inFlight": True, "depth": 4, "rejectedTotal": 12,
                                "secret": "private"},
            "bearer": "private", "adminPassword": "private",
        },
        "/v1/server-id": {"serverId": "server_42", "token": "private"},
        "/v1/sponsor": {
            "imageUrl": "https://images.example/sponsor.png", "secret": "private"
        },
    })
    with TestClient(app, base_url=PANEL_ORIGIN) as client:
        for path in ("health", "server-id", "sponsor"):
            assert client.get(f"/api/server/{path}").status_code == 401
        assert sent == []
        login(client)
        health = client.get("/api/server/health")
        server_id = client.get("/api/server/server-id")
        sponsor = client.get("/api/server/sponsor")
    assert [r.status_code for r in (health, server_id, sponsor)] == [200, 200, 200]
    assert {key: health.json()[key] for key in (
        "reachable", "reportedState", "reportedHealthy", "uptimeSeconds"
    )} == {
        "reachable": True, "reportedState": "degraded", "reportedHealthy": False,
        "uptimeSeconds": 123,
    }
    assert server_id.json()["serverId"] == "server_42"
    assert health.json()["connections"] == {"active": 6}
    assert health.json()["gameThreadQueue"] == {
        "inFlight": True, "depth": 4, "rejectedTotal": 12,
    }
    assert sponsor.json()["imageUrl"] == "https://images.example/sponsor.png"
    assert sponsor.json()["hasImage"] is True
    for response in (health, server_id, sponsor):
        data = response.json()
        assert data["stale"] is False
        assert data["observedAt"].endswith("Z")
        assert data["targetRevision"] == health.json()["targetRevision"]
        assert "private" not in response.text
    assert sent == [
        ("GET", "/v1/capabilities"), ("GET", "/v1/health"),
        ("GET", "/v1/server-id"), ("GET", "/v1/sponsor"),
    ]


def test_diagnostics_check_advertised_routes_before_target_get(tmp_path):
    app, sent = make_app(tmp_path, advertised=["GET /v1/health"], payloads={
        "/v1/health": {"ok": True},
    })
    with TestClient(app, base_url=PANEL_ORIGIN) as client:
        login(client)
        assert client.get("/api/server/health").status_code == 200
        for path in ("server-id", "sponsor"):
            response = client.get(f"/api/server/{path}")
            assert response.status_code == 501
            assert response.json()["code"] == "route_unsupported"
    assert sent == [("GET", "/v1/capabilities"), ("GET", "/v1/health")]


def test_diagnostics_reject_invalid_id_and_drop_unsafe_sponsor_image(tmp_path):
    app, sent = make_app(tmp_path, payloads={
        "/v1/health": {"status": "unknown", "uptimeSeconds": 10**1000,
                       "connections": {"active": -1},
                       "gameThreadQueue": {"inFlight": "busy", "depth": -2,
                                           "rejectedTotal": 10**1000}},
        "/v1/server-id": {"serverId": "../../private", "token": "private"},
        "/v1/sponsor": {"imageUrl": "https://images.example/a.png?token=private"},
    })
    with TestClient(app, base_url=PANEL_ORIGIN) as client:
        login(client)
        health = client.get("/api/server/health")
        invalid_id = client.get("/api/server/server-id")
        sponsor = client.get("/api/server/sponsor")
    assert health.status_code == 200
    assert health.json()["reportedState"] is None
    assert health.json()["uptimeSeconds"] is None
    assert health.json()["connections"] == {"active": None}
    assert health.json()["gameThreadQueue"] == {
        "inFlight": None, "depth": None, "rejectedTotal": None,
    }
    assert invalid_id.status_code == 502
    assert invalid_id.json()["code"] == "invalid_upstream"
    assert "private" not in invalid_id.text
    assert sponsor.status_code == 200
    assert sponsor.json()["imageUrl"] is None
    assert sponsor.json()["hasImage"] is False
    assert len(sent) == 4


def test_unset_server_id_is_a_valid_empty_server_setting():
    from app.rcon.diagnostics import project_server_id

    assert project_server_id({"serverId": ""}) == {"serverId": None}


@pytest.mark.parametrize("image_url", [
    "http://images.example/a.png",
    "https://127.0.0.1/a.png",
    "https://images.example/a.png?token=private",
    "https://user:pass@images.example/a.png",
    "https://images.example/a.png#private",
    "https://images.example/a.png\x00private",
])
def test_sponsor_image_rejects_nonpublic_or_credential_bearing_url(image_url):
    assert project_sponsor({"imageUrl": image_url}) == {"imageUrl": None, "hasImage": False}
