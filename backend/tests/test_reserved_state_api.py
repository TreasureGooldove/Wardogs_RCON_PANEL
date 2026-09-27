"""Configured versus live reserved slots using local MockTransport only."""

from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import PanelSettings, RconTarget
from app.main import create_app
from app.rcon.config_doc import reserved_ids_from_text


PANEL = "https://panel.example"
RCON = "https://rcon.example.invalid"
ID_A = "76561198000000001"
ID_B = "76561198000000002"
SECRET = "private-config-canary"


def config_text(ids):
    lines = [
        "[/Script/WDGame.WDGameSession]",
        f"ServerPassword={SECRET}",
        "!DefaultReservedPlayerIds=ClearArray",
    ]
    lines.extend(f".DefaultReservedPlayerIds={steam_id}" for steam_id in ids)
    return "\n".join(lines) + "\n"


def make_panel(tmp_path, *, configured, live, apply_live=False, fail_post_read=None):
    state = {
        "text": config_text(configured),
        "live": list(live),
        "revision": "rev-1",
        "did_put": False,
    }
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/v1/capabilities":
            return httpx.Response(200, json={"routes": [
                "GET /v1/config", "GET /v1/reserved-slots", "PUT /v1/config",
            ], "config": {"writable": True}})
        if path == "/v1/config" and request.method == "GET":
            if state["did_put"] and fail_post_read == "config":
                return httpx.Response(503)
            return httpx.Response(200, json={
                "revision": state["revision"], "writable": True,
                "text": state["text"], "sections": [], "warnings": [],
            })
        if path == "/v1/reserved-slots" and request.method == "GET":
            if state["did_put"] and fail_post_read == "reserved":
                return httpx.Response(503)
            return httpx.Response(200, json={"reservedSlots": list(state["live"])})
        if path == "/v1/config" and request.method == "PUT":
            assert request.headers["if-match"] == f'"{state["revision"]}"'
            text = request.content.decode("utf-8")
            assert f"ServerPassword={SECRET}" in text
            state["text"] = text
            state["revision"] = "rev-2"
            state["did_put"] = True
            if apply_live:
                state["live"] = reserved_ids_from_text(text)
            return httpx.Response(200, json={"ok": True, "revision": "rev-2"})
        raise AssertionError(f"unexpected RCON route: {request.method} {path}")

    settings = PanelSettings(
        db_path=tmp_path / "panel.sqlite3",
        public_origin=PANEL,
        session_secure=True,
        rcon_target=RconTarget(origin=RCON, bearer_secret="local-mock-only"),
    )
    app = create_app(settings, rcon_transport=httpx.MockTransport(handler))
    app.state.auth_service.create_admin("owner", "correct horse battery staple")
    return app, state, calls


def login(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "owner", "password": "correct horse battery staple"},
        headers={"Origin": PANEL},
    )
    assert response.status_code == 200


def change_payload(snapshot, steam_id):
    return {
        "steamId": steam_id,
        "revision": snapshot["revision"],
        "targetRevision": snapshot["targetRevision"],
    }


def test_metadata_is_panel_only_and_expiry_removes_only_its_id(tmp_path):
    app, state, calls = make_panel(tmp_path, configured=[ID_A, ID_B], live=[ID_A, ID_B])
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        target = client.get("/api/server/reserved-slots").json()["targetRevision"]
        original = state["text"]
        saved = client.patch(
            "/api/server/reserved-slots/metadata",
            json={"steamId": ID_B, "reason": "活动预留", "days": 1, "targetRevision": target},
            headers={"Origin": PANEL},
        )
        assert saved.status_code == 200
        assert state["text"] == original
        assert saved.json()["metadata"][ID_B]["reason"] == "活动预留"
        assert ("PUT", "/v1/config") not in calls
        app.state.database.put_reserved_metadata(
            RCON, ID_B, "活动预留",
            (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )
        client.portal.call(app.state.reservation_expirer.run_once)
        assert reserved_ids_from_text(state["text"]) == [ID_A]
        assert app.state.database.reserved_metadata(RCON) == {}
        assert calls.count(("PUT", "/v1/config")) == 1


def test_get_reports_live_and_configured_lists_independently(tmp_path):
    app, _, calls = make_panel(tmp_path, configured=[ID_A, ID_B], live=[ID_A])
    with TestClient(app, base_url=PANEL) as client:
        assert client.get("/api/server/reserved-slots").status_code == 401
        login(client)
        response = client.get("/api/server/reserved-slots")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "reservedSlots": [ID_A],
        "configuredReservedSlots": [ID_A, ID_B],
        "pendingRestart": True,
        "revision": "rev-1",
        "writable": True,
        "targetRevision": response.json()["targetRevision"],
        "metadata": {},
    }
    assert response.json()["targetRevision"]
    assert SECRET not in response.text
    assert all(method == "GET" for method, _ in calls)


@pytest.mark.parametrize(
    "operation,initial_config,initial_live,target,expected_config",
    [
        ("POST", [ID_A], [ID_A], ID_B, [ID_A, ID_B]),
        ("DELETE", [ID_A, ID_B], [ID_A, ID_B], ID_B, [ID_A]),
    ],
)
def test_successful_change_keeps_live_state_distinct(
    tmp_path, operation, initial_config, initial_live, target, expected_config
):
    app, state, calls = make_panel(
        tmp_path, configured=initial_config, live=initial_live, apply_live=False
    )
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        before = client.get("/api/server/reserved-slots").json()
        result = client.request(
            operation, "/api/server/reserved-slots",
            json=change_payload(before, target), headers={"Origin": PANEL},
        )
        after = client.get("/api/server/reserved-slots")
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json() == {
        "ok": True,
        "reservedSlots": initial_live,
        "configuredReservedSlots": expected_config,
        "pendingRestart": True,
        "revision": "rev-2",
        "targetRevision": before["targetRevision"],
        "metadata": {target: {"reason": "", "expiresAt": None, "status": "active"}} if operation == "POST" else {},
    }
    assert after.json()["reservedSlots"] == initial_live
    assert after.json()["configuredReservedSlots"] == expected_config
    assert after.json()["pendingRestart"] is True
    assert reserved_ids_from_text(state["text"]) == expected_config
    assert SECRET not in result.text + after.text
    assert calls.count(("PUT", "/v1/config")) == 1
    write_index = calls.index(("PUT", "/v1/config"))
    assert calls[write_index + 1:write_index + 3] == [
        ("GET", "/v1/config"), ("GET", "/v1/reserved-slots")
    ]


def test_successful_apply_can_also_become_live_immediately(tmp_path):
    app, _, calls = make_panel(tmp_path, configured=[ID_A], live=[ID_A], apply_live=True)
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        before = client.get("/api/server/reserved-slots").json()
        result = client.post(
            "/api/server/reserved-slots", json=change_payload(before, ID_B),
            headers={"Origin": PANEL},
        )
    assert result.status_code == 200
    assert result.json()["reservedSlots"] == [ID_A, ID_B]
    assert result.json()["configuredReservedSlots"] == [ID_A, ID_B]
    assert result.json()["pendingRestart"] is False
    assert calls.count(("PUT", "/v1/config")) == 1


@pytest.mark.parametrize("failed_side", ["config", "reserved"])
def test_read_failure_after_successful_write_does_not_invite_retry(tmp_path, failed_side):
    app, _, calls = make_panel(
        tmp_path, configured=[ID_A], live=[ID_A], fail_post_read=failed_side
    )
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        before = client.get("/api/server/reserved-slots").json()
        result = client.post(
            "/api/server/reserved-slots", json=change_payload(before, ID_B),
            headers={"Origin": PANEL},
        )
    assert result.status_code == 200
    assert result.json()["ok"] is True
    assert result.json()["pendingRestart"] is None
    assert result.json()["revision"] == "rev-2"
    if failed_side == "config":
        assert result.json()["configuredReservedSlots"] is None
        assert result.json()["reservedSlots"] == [ID_A]
    else:
        assert result.json()["configuredReservedSlots"] == [ID_A, ID_B]
        assert result.json()["reservedSlots"] is None
    assert calls.count(("PUT", "/v1/config")) == 1


def test_origin_and_target_revision_guard_still_prevent_writes(tmp_path):
    app, _, calls = make_panel(tmp_path, configured=[ID_A], live=[ID_A])
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        before = client.get("/api/server/reserved-slots").json()
        payload = change_payload(before, ID_B)
        wrong_origin = client.post("/api/server/reserved-slots", json=payload)
        stale = client.post(
            "/api/server/reserved-slots", json={**payload, "targetRevision": "wrong"},
            headers={"Origin": PANEL},
        )
    assert (wrong_origin.status_code, wrong_origin.json()["code"]) == (401, "not_authenticated")
    assert (stale.status_code, stale.json()["code"]) == (409, "stale_server_target")
    assert ("PUT", "/v1/config") not in calls


def test_invalid_reserved_ids_in_upstream_config_are_redacted(tmp_path):
    app, state, _ = make_panel(tmp_path, configured=[ID_A], live=[ID_A])
    state["text"] = config_text(["invalid-id"])
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        response = client.get("/api/server/reserved-slots")
    assert (response.status_code, response.json()["code"]) == (502, "invalid_upstream")
    assert SECRET not in response.text
