"""Whole-config and reserved-slot behavior against local MockTransport only."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256

from fastapi.testclient import TestClient
import httpx
import pytest

from app.api.config_doc import router as config_router
from app.api.reserved_slots import router as reserved_router
from app.config import PanelSettings, RconTarget
from app.main import create_app
from app.rcon.config_doc import banned_ids_from_text, replace_reserved_ids, reserved_ids_from_text


PANEL = "https://panel.example"
RCON = "https://rcon.example.invalid"
STEAM_A = "76561198000000001"
STEAM_B = "76561198000000002"
TEXT = (
    "; header kept\r\n"
    "[Unknown.Section]\r\n"
    "UnknownKey=do-not-drop\r\n"
    "\r\n"
    "[/Script/WDGame.WDGameSession]\r\n"
    "ServerName=Test Server\r\n"
    f"!DefaultReservedPlayerIds=ClearArray\r\n.DefaultReservedPlayerIds={STEAM_A}\r\n"
    "# reserved comment kept\r\n"
    "ServerPassword=not-a-real-password\r\n"
    "\r\n"
    "[Another.Section]\r\n"
    "AnotherKey=also-kept\r\n"
)


def test_reserved_parser_replaces_only_target_array():
    assert reserved_ids_from_text(TEXT) == [STEAM_A]
    added = replace_reserved_ids(TEXT, [STEAM_A, STEAM_B])
    assert reserved_ids_from_text(added) == [STEAM_A, STEAM_B]
    assert "UnknownKey=do-not-drop\r\n" in added
    assert "AnotherKey=also-kept\r\n" in added
    assert "# reserved comment kept\r\n" in added
    assert "ServerPassword=not-a-real-password\r\n" in added
    assert added.count("!DefaultReservedPlayerIds=ClearArray") == 1
    assert "\n" not in added.replace("\r\n", "")
    removed = replace_reserved_ids(added, [STEAM_B])
    assert reserved_ids_from_text(removed) == [STEAM_B]
    assert "UnknownKey=do-not-drop\r\n" in removed


def test_config_arrays_accept_existing_placeholder_and_plus_bans():
    text = "[/Script/WDGame.WDGameSession]\n.DefaultReservedPlayerIds=01234567890123456\n+DefaultBannedPlayerIds=76561198000000001\n"
    assert reserved_ids_from_text(text) == ["01234567890123456"]
    assert banned_ids_from_text(text) == ["76561198000000001"]
    updated = replace_reserved_ids(text, ["01234567890123456", STEAM_A])
    assert "+DefaultBannedPlayerIds=76561198000000001" in updated


def test_reserved_parser_honors_clear_add_remove_and_repeated_sections():
    text = (
        f"[/Script/WDGame.WDGameSession]\n.DefaultReservedPlayerIds={STEAM_A}\n"
        f"+DefaultReservedPlayerIds={STEAM_B}\n-DefaultReservedPlayerIds={STEAM_A}\n"
        "[Other]\nOther=still-here\n"
        f"[/Script/WDGame.WDGameSession]\n.DefaultReservedPlayerIds={STEAM_A}\n"
    )
    assert reserved_ids_from_text(text) == [STEAM_B, STEAM_A]
    rewritten = replace_reserved_ids(text, [STEAM_A])
    assert reserved_ids_from_text(rewritten) == [STEAM_A]
    assert "[Other]\nOther=still-here\n" in rewritten


def make_panel(tmp_path, *, writable=True, advertised=True, put_result="ok"):
    state = {"revision": "rev-1", "text": TEXT, "reserved": [STEAM_A], "bans": []}
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path == "/v1/bans":
            return httpx.Response(200, json={"bans": [{"steamId": item} for item in state["bans"]]})
        if path == "/v1/capabilities":
            routes = ["GET /v1/config", "GET /v1/reserved-slots", "POST /v1/config/validate"]
            if advertised:
                routes.append("PUT /v1/config")
            return httpx.Response(200, json={"routes": routes, "config": {"writable": writable}})
        if path == "/v1/config" and request.method == "GET":
            return httpx.Response(200, json={
                "revision": state["revision"], "writable": writable,
                "text": state["text"], "sections": [], "warnings": [],
            })
        if path == "/v1/reserved-slots":
            return httpx.Response(200, json={"reservedSlots": list(state["reserved"])})
        if path == "/v1/config/validate":
            assert request.method == "POST"
            assert request.headers["content-type"].startswith("text/plain")
            return httpx.Response(200, json={
                "ok": True, "errors": [],
                "warnings": ["Password=not-a-real-password"],
            })
        if path == "/v1/config" and request.method == "PUT":
            assert request.headers["if-match"] == f'"{state["revision"]}"'
            if put_result == "timeout":
                raise httpx.ReadTimeout("local simulated timeout", request=request)
            if put_result == "conflict":
                return httpx.Response(412, json={"ok": False})
            if put_result == "oversize":
                return httpx.Response(200, content=b"x" * 2_000_000)
            state["text"] = request.content.decode("utf-8")
            state["reserved"] = reserved_ids_from_text(state["text"])
            state["revision"] = "rev-2"
            return httpx.Response(200, json={
                "ok": True, "revision": "rev-2",
                "outcomes": [{"secret": "not-a-real-password"}],
            })
        raise AssertionError(f"unexpected mock route {request.method} {path}")

    settings = PanelSettings(
        db_path=tmp_path / "panel.sqlite3",
        public_origin=PANEL,
        session_secure=True,
        rcon_target=RconTarget(origin=RCON, bearer_secret="local-only-mock-bearer"),
    )
    app = create_app(settings, rcon_transport=httpx.MockTransport(handler))
    app.state.auth_service.create_admin("owner", "correct horse battery staple")
    if not any(getattr(route, "path", None) == "/api/server/config" for route in app.routes):
        app.include_router(config_router)
    if not any(getattr(route, "path", None) == "/api/server/reserved-slots" for route in app.routes):
        app.include_router(reserved_router)
    return app, state, calls


def login(client: TestClient, username="owner", password="correct horse battery staple"):
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"Origin": PANEL},
    )
    assert response.status_code == 200


def put_calls(calls):
    return [call for call in calls if call.method == "PUT"]


def test_every_config_read_fetches_current_server_text_without_writing(tmp_path):
    app, state, calls = make_panel(tmp_path)
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        first = client.get("/api/server/config")
        assert first.headers["cache-control"] == "no-store"
        assert "ServerName=Test Server" in first.json()["text"]
        state["text"] = "[Custom.Actual]\nActualOnly=server-change\n"
        state["revision"] = "rev-new"
        second = client.get("/api/server/config")
        assert second.json()["text"] == state["text"]
        assert second.json()["revision"] == "rev-new"
        assert len([c for c in calls if c.url.path == "/v1/config"]) == 2
        assert all(c.method == "GET" for c in calls)


@pytest.mark.parametrize("operation", ["validate", "apply", "add", "remove"])
def test_revoked_owner_session_cannot_write_after_lock_entry(tmp_path, operation):
    app, _, calls = make_panel(tmp_path)
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        snapshot = client.get("/api/server/config").json()
        token = client.cookies.get("panel_session")
        assert token
        token_hash = sha256(token.encode("utf-8")).hexdigest()
        original_lock = app.state.rcon_runtime.lock

        class RevokeOnLockEntry:
            async def __aenter__(self):
                await original_lock.acquire()
                app.state.database.revoke_session(token_hash, datetime.now(timezone.utc))

            async def __aexit__(self, *_args):
                original_lock.release()

        app.state.rcon_runtime.lock = RevokeOnLockEntry()
        prior_calls = len(calls)
        if operation == "validate":
            response = client.post(
                "/api/server/config/validate",
                json={"text": TEXT, "targetRevision": snapshot["targetRevision"]},
                headers={"Origin": PANEL},
            )
        elif operation == "apply":
            response = client.put(
                "/api/server/config",
                json={
                    "text": TEXT + "; edited\r\n", "revision": snapshot["revision"],
                    "targetRevision": snapshot["targetRevision"],
                    "password": "correct horse battery staple",
                },
                headers={"Origin": PANEL},
            )
        else:
            response = client.request(
                "POST" if operation == "add" else "DELETE",
                "/api/server/reserved-slots",
                json={
                    "steamId": STEAM_B if operation == "add" else STEAM_A,
                    "revision": snapshot["revision"],
                    "targetRevision": snapshot["targetRevision"],
                },
                headers={"Origin": PANEL},
            )
        assert response.status_code == 401
        assert calls[prior_calls:] == []


def test_config_owner_only_and_revision_guard(tmp_path):
    app, state, calls = make_panel(tmp_path)
    app.state.auth_service.create_subuser("viewer", "viewer test password 123")
    with TestClient(app, base_url=PANEL) as owner:
        assert owner.get("/api/server/config").status_code == 401
        login(owner)
        config = owner.get("/api/server/config")
        assert config.status_code == 200
        assert config.headers["cache-control"] == "no-store"
        assert config.json()["revision"] == "rev-1"
        assert config.json()["targetRevision"]
        assert "not-a-real-password" not in config.json()["text"]
        assert "ServerPassword=__WD_REDACTED_rev-1__" in config.json()["text"]
        advertised = owner.get("/api/server/capabilities").json()["advertisedActions"]
        assert advertised["config"] is True
        assert advertised["reservedSlots"] is True
        assert advertised["configApply"] is True
        assert advertised["configValidate"] is True
        assert advertised["unban"] is False
        payload = {
            "text": config.json()["text"] + "; added comment\r\n",
            "revision": "old-revision",
            "targetRevision": config.json()["targetRevision"],
            "password": "correct horse battery staple",
        }
        stale = owner.put("/api/server/config", json=payload, headers={"Origin": PANEL})
        assert stale.status_code == 409
        assert stale.json()["code"] == "config_conflict"
        assert not put_calls(calls)
        payload["revision"] = "rev-1"
        wrong_origin = owner.put("/api/server/config", json=payload)
        assert wrong_origin.status_code == 401
        payload["targetRevision"] = "wrong-target"
        wrong_target = owner.put("/api/server/config", json=payload, headers={"Origin": PANEL})
        assert wrong_target.status_code == 409
        assert not put_calls(calls)
        payload["targetRevision"] = config.json()["targetRevision"]
        wrong_password = owner.put(
            "/api/server/config", json={**payload, "password": "incorrect-password"},
            headers={"Origin": PANEL},
        )
        assert wrong_password.status_code == 401
        assert not put_calls(calls)
        validated = owner.post(
            "/api/server/config/validate",
            json={"text": payload["text"], "targetRevision": payload["targetRevision"]},
            headers={"Origin": PANEL},
        )
        assert validated.status_code == 200
        assert validated.json()["ok"] is True
        assert "not-a-real-password" not in validated.text
        assert "ServerPassword=not-a-real-password" in [
            call for call in calls if call.url.path == "/v1/config/validate"
        ][-1].content.decode("utf-8")
        assert not put_calls(calls)
        applied = owner.put("/api/server/config", json=payload, headers={"Origin": PANEL})
        assert applied.status_code == 200
        assert applied.json()["revision"] == "rev-2"
        assert "not-a-real-password" not in applied.text
        assert len(put_calls(calls)) == 1
        assert state["text"] == TEXT + "; added comment\r\n"

    with TestClient(app, base_url=PANEL) as viewer:
        login(viewer, "viewer", "viewer test password 123")
        assert viewer.get("/api/server/config").status_code == 403
        reserved = viewer.get("/api/server/reserved-slots")
        assert reserved.status_code == 200
        assert reserved.json()["reservedSlots"] == [STEAM_A]
        assert viewer.post(
            "/api/server/config/validate",
            json={"text": TEXT, "targetRevision": "x"}, headers={"Origin": PANEL},
        ).status_code == 403
        assert viewer.put(
            "/api/server/config", json=payload, headers={"Origin": PANEL}
        ).status_code == 403
        reserved_payload = {
            "steamId": STEAM_B, "revision": reserved.json()["revision"],
            "targetRevision": reserved.json()["targetRevision"],
        }
        assert viewer.post(
            "/api/server/reserved-slots", json=reserved_payload, headers={"Origin": PANEL}
        ).status_code == 403
        assert viewer.request(
            "DELETE", "/api/server/reserved-slots", json=reserved_payload,
            headers={"Origin": PANEL},
        ).status_code == 403
        assert len(put_calls(calls)) == 1


def test_reserved_slot_add_remove_preserve_unrelated_config_and_check_exact_target(tmp_path):
    app, state, calls = make_panel(tmp_path)
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        snapshot = client.get("/api/server/reserved-slots")
        assert snapshot.status_code == 200
        body = snapshot.json()
        assert body["reservedSlots"] == [STEAM_A]
        assert body["revision"] == "rev-1"
        assert body["writable"] is True
        payload = {
            "steamId": STEAM_B, "revision": body["revision"],
            "targetRevision": body["targetRevision"],
        }
        bad = deepcopy(payload)
        bad["steamId"] = "bad-id"
        assert client.post(
            "/api/server/reserved-slots", json=bad, headers={"Origin": PANEL}
        ).status_code == 400
        assert not put_calls(calls)
        added = client.post(
            "/api/server/reserved-slots", json=payload, headers={"Origin": PANEL}
        )
        assert added.status_code == 200
        assert added.json()["reservedSlots"] == [STEAM_A, STEAM_B]
        assert added.json()["revision"] == "rev-2"
        assert len(put_calls(calls)) == 1
        assert "UnknownKey=do-not-drop\r\n" in state["text"]
        assert "AnotherKey=also-kept\r\n" in state["text"]
        assert "ServerPassword=not-a-real-password\r\n" in state["text"]
        assert "# reserved comment kept\r\n" in state["text"]
        duplicate = client.post(
            "/api/server/reserved-slots",
            json={**payload, "revision": "rev-2"}, headers={"Origin": PANEL},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "reserved_exists"
        assert len(put_calls(calls)) == 1
        stale = client.request(
            "DELETE",
            "/api/server/reserved-slots", json=payload, headers={"Origin": PANEL}
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "config_conflict"
        assert len(put_calls(calls)) == 1
        removed = client.request(
            "DELETE", "/api/server/reserved-slots",
            json={**payload, "revision": "rev-2"}, headers={"Origin": PANEL},
        )
        assert removed.status_code == 200
        assert removed.json()["reservedSlots"] == [STEAM_A]
        assert len(put_calls(calls)) == 2


@pytest.mark.parametrize("put_result,expected_code", [
    ("timeout", "action_uncertain"),
    ("conflict", "config_conflict"),
    ("oversize", "action_uncertain"),
])
def test_config_write_uncertain_or_remote_conflict_never_retries(tmp_path, put_result, expected_code):
    app, _, calls = make_panel(tmp_path, put_result=put_result)
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        config = client.get("/api/server/config").json()
        response = client.put(
            "/api/server/config",
            json={
                "text": TEXT + "; local edit\r\n", "revision": config["revision"],
                "targetRevision": config["targetRevision"],
                "password": "correct horse battery staple",
            },
            headers={"Origin": PANEL},
        )
        assert response.json()["code"] == expected_code
        assert len(put_calls(calls)) == 1


def test_capability_or_writable_gate_denies_config_writes(tmp_path):
    for advertised, writable in ((False, True), (True, False)):
        app, _, calls = make_panel(tmp_path / f"{advertised}-{writable}", advertised=advertised, writable=writable)
        with TestClient(app, base_url=PANEL) as client:
            login(client)
            config = client.get("/api/server/config").json()
            response = client.put(
                "/api/server/config",
                json={
                    "text": TEXT + "; local edit\r\n", "revision": config["revision"],
                    "targetRevision": config["targetRevision"],
                    "password": "correct horse battery staple",
                },
                headers={"Origin": PANEL},
            )
            assert response.status_code in {403, 501}
            assert not put_calls(calls)


def test_missing_config_ban_array_disables_feature_and_all_config_puts(tmp_path):
    app, state, calls = make_panel(tmp_path)
    state['bans'] = [STEAM_B, '00000000000000000']
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        document = client.get('/api/server/config').json()
        assert document['writable'] is False
        assert document['consistency'] == {
            'ok': False, 'reason': 'ban_list_mismatch',
            'configuredBannedCount': 0, 'liveBannedCount': 1,
        }
        payload = {'text': document['text'], 'targetRevision': document['targetRevision']}
        validate = client.post('/api/server/config/validate', json=payload, headers={'Origin': PANEL})
        assert validate.status_code == 503
        assert validate.json()['code'] == 'config_interface_inconsistent'
        applied = client.put('/api/server/config', json={**payload, 'revision': document['revision'],
            'password': 'correct horse battery staple'}, headers={'Origin': PANEL})
        assert applied.status_code == 503
        reserved = client.post('/api/server/reserved-slots', json={
            'steamId': STEAM_B, 'revision': document['revision'],
            'targetRevision': document['targetRevision']}, headers={'Origin': PANEL})
        assert reserved.status_code == 503
        assert reserved.json()['code'] == 'config_interface_inconsistent'
        assert not put_calls(calls)
        assert not [c for c in calls if c.url.path == '/v1/config/validate']


def test_duplicate_config_bans_match_unique_live_list_and_keep_original_lines(tmp_path):
    app, state, calls = make_panel(tmp_path)
    state['bans'] = [STEAM_B]
    state['text'] = state['text'].replace('ServerName=Test Server',
        f'.DefaultBannedPlayerIds={STEAM_B}\r\n+DefaultBannedPlayerIds={STEAM_B}\r\nServerName=Test Server')
    with TestClient(app, base_url=PANEL) as client:
        login(client)
        document = client.get('/api/server/config').json()
        assert document['writable'] is True
        assert document['consistency']['ok'] is True
        assert document['consistency']['configuredBannedCount'] == 1
        assert f'.DefaultBannedPlayerIds={STEAM_B}' in document['text']
        assert f'+DefaultBannedPlayerIds={STEAM_B}' in document['text']
        assert all(c.method == 'GET' for c in calls)
