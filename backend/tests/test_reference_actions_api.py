"""Owner action API and reference reads with only local MockTransport traffic."""

import json
import sqlite3

from fastapi.testclient import TestClient
import httpx

from app.config import PanelSettings, RconTarget
from app.main import create_app


PANEL_ORIGIN = "https://panel.example"
STEAM_ID = "76561198000000001"
PASSWORD = "owner-test-password"
SUB_PASSWORD = "subuser-test-password"
ALL_ROUTES = [
    "GET /v1/bans", "GET /v1/audit",
    "GET /v1/catalog/maps/{id}/experiences",
    "GET /v1/catalog/maps/{id}/alternators",
    "GET /v1/status", "GET /v1/players",
    "GET /v1/catalog/lightings",
    "DELETE /v1/bans/{steamId}",
    "POST /v1/players/{steamId}/kill",
    "POST /v1/players/{steamId}/message",
    "PATCH /v1/players/{steamId}",
    "POST /v1/broadcast", "POST /v1/match/map",
    "POST /v1/match/end", "POST /v1/match/restart",
    "PUT /v1/world/lighting",
]


def make_app(tmp_path, *, routes=None, on_write=None):
    sent = []

    def handler(request):
        sent.append(request)
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"routes": ALL_ROUTES if routes is None else routes})
        if request.url.path == "/v1/bans":
            return httpx.Response(200, json={"bans": [{
                "steamId": STEAM_ID, "bannedAtUtc": "2026-09-27T00:00:00Z",
                "bannedBy": "owner", "reason": None,
            }]})
        if request.url.path == "/v1/audit":
            return httpx.Response(200, json={"entries": [{
                "timestampUtc": "2026-09-27T00:00:00Z", "peer": "127.0.0.1",
                "sessionId": "local", "event": "test", "detail": "local only",
            }]})
        if request.url.path == "/v1/catalog/maps/Narva/experiences":
            return httpx.Response(200, json={"experiences": ["Invasion"]})
        if request.url.path == "/v1/catalog/maps/Narva/alternators":
            return httpx.Response(200, json={"alternators": [{
                "tag": "North", "displayName": "North Zone",
            }]})
        if request.url.path == "/v1/status":
            return httpx.Response(200, json={"factionScores": [
                {"name": "Lonestar", "score": 0},
                {"name": "Valkyra", "score": 0},
                {"name": "Manticore", "score": 0},
            ]})
        if request.url.path == "/v1/players":
            return httpx.Response(200, json={"players": []})
        if request.url.path == "/v1/catalog/lightings":
            return httpx.Response(200, json={"lightings": ["DayClear", "Night"]})
        if on_write is not None:
            return on_write(request)
        return httpx.Response(200, json={"message": "accepted"})

    settings = PanelSettings(
        db_path=tmp_path / "panel.sqlite3",
        public_origin=PANEL_ORIGIN,
        session_secure=True,
        rcon_target=RconTarget(
            origin="https://rcon.wardogs.invalid", bearer_secret="local-test-secret"
        ),
    )
    app = create_app(settings, rcon_transport=httpx.MockTransport(handler))
    app.state.auth_service.create_admin("Owner", PASSWORD)
    return app, settings, sent


def login(client, username="owner", password=PASSWORD):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password},
        headers={"Origin": PANEL_ORIGIN},
    )
    assert response.status_code == 200


def test_reference_reads_are_login_only_bounded_and_versioned(tmp_path):
    app, _, sent = make_app(tmp_path)
    with TestClient(app, base_url=PANEL_ORIGIN) as owner:
        assert owner.get("/api/server/bans").status_code == 401
        assert sent == []
        login(owner)
        created = owner.post(
            "/api/subusers",
            json={"username": "Viewer", "password": SUB_PASSWORD},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert created.status_code == 201
        with TestClient(app, base_url=PANEL_ORIGIN) as subuser:
            login(subuser, "viewer", SUB_PASSWORD)
            bans = subuser.get("/api/server/bans")
            audit = subuser.get("/api/server/audit?limit=50")
            experiences = subuser.get("/api/server/catalog/maps/experiences?map=Narva")
            alternators = subuser.get("/api/server/catalog/maps/alternators?map=Narva")
            assert [response.status_code for response in (bans, audit, experiences, alternators)] == [200] * 4, [
                response.text for response in (bans, audit, experiences, alternators)
            ]
            assert bans.json()["bans"][0]["steamId"] == STEAM_ID
            assert audit.json()["entries"][0]["event"] == "test"
            assert experiences.json()["items"] == [{"id": "Invasion", "label": "Invasion"}]
            assert alternators.json()["items"] == [{"id": "North", "label": "North Zone"}]
            revision = bans.json()["targetRevision"]
            assert revision == audit.json()["targetRevision"] == experiences.json()["targetRevision"]
            assert bans.json()["stale"] is False
            before = len(sent)
            bad_limit = subuser.get("/api/server/audit?limit=500")
            bad_map = subuser.get("/api/server/catalog/maps/experiences?map=..%2Fbans")
            assert bad_limit.status_code == bad_map.status_code == 400
            assert len(sent) == before
    assert [request.url.query for request in sent if request.url.path == "/v1/audit"] == [b"limit=50"]


def test_owner_actions_require_origin_revision_and_advertised_route(tmp_path):
    app, _, sent = make_app(tmp_path, routes=["GET /v1/bans"])
    with TestClient(app, base_url=PANEL_ORIGIN) as owner:
        login(owner)
        revision = owner.get("/api/server/bans").json()["targetRevision"]
        before = len(sent)
        no_origin = owner.post(
            "/api/server/broadcast", json={"message": "test", "targetRevision": revision}
        )
        stale = owner.post(
            "/api/server/broadcast", json={"message": "test", "targetRevision": "stale"},
            headers={"Origin": PANEL_ORIGIN},
        )
        unavailable = owner.post(
            "/api/server/broadcast", json={"message": "test", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert no_origin.status_code == 401
        assert stale.status_code == 409
        assert unavailable.status_code == 501
        assert unavailable.json()["code"] == "action_unsupported"
        assert len(sent) == before


def test_extra_actions_owner_only_and_fixed_payloads(tmp_path):
    app, settings, sent = make_app(tmp_path)
    with TestClient(app, base_url=PANEL_ORIGIN) as owner:
        login(owner)
        revision = owner.get("/api/server/bans").json()["targetRevision"]
        assert owner.post(
            "/api/subusers",
            json={"username": "Moderator", "password": SUB_PASSWORD,
                  "canKick": True, "canBan": True},
            headers={"Origin": PANEL_ORIGIN},
        ).status_code == 201
        with TestClient(app, base_url=PANEL_ORIGIN) as subuser:
            login(subuser, "moderator", SUB_PASSWORD)
            before = len(sent)
            denied = subuser.post(
                "/api/server/broadcast",
                json={"message": "test", "targetRevision": revision},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert denied.status_code == 403
            assert denied.json()["code"] == "permission_denied"
            assert len(sent) == before

        requests = [
            ("/api/server/unbans", {"steamId": STEAM_ID}, "DELETE", f"/v1/bans/{STEAM_ID}"),
            ("/api/server/kills", {"steamId": STEAM_ID}, "POST", f"/v1/players/{STEAM_ID}/kill"),
            ("/api/server/messages", {"steamId": STEAM_ID, "message": "hello"}, "POST", f"/v1/players/{STEAM_ID}/message"),
            ("/api/server/broadcast", {"message": "hello all"}, "POST", "/v1/broadcast"),
            ("/api/server/match/end", {}, "POST", "/v1/match/end"),
            ("/api/server/match/restart", {}, "POST", "/v1/match/restart"),
            ("/api/server/match/map", {"map": "Narva", "experiences": ["Invasion"],
                                        "lighting": "Day", "zoneAlternator": "North"},
             "POST", "/v1/match/map"),
        ]
        for api_path, body, method, upstream_path in requests:
            response = owner.post(
                api_path, json={**body, "targetRevision": revision},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert response.status_code == 200, (api_path, response.text)
            assert response.json() == {"ok": True}
            assert (sent[-1].method, sent[-1].url.path) == (method, upstream_path)
        assert json.loads(sent[-1].content) == {
            "map": "Narva", "experiences": ["Invasion"],
            "lighting": "Day", "zoneAlternator": "North",
        }

    with sqlite3.connect(settings.db_path) as connection:
        rows = connection.execute("SELECT action, outcome FROM moderation_audit ORDER BY id").fetchall()
    assert rows == [
        ("unban", "accepted"), ("kill", "accepted"), ("message", "accepted"),
        ("broadcast", "accepted"), ("endMatch", "accepted"),
        ("restartMatch", "accepted"), ("changeMap", "accepted"),
    ]


def test_set_lighting_requires_owner_origin_revision_and_capability(tmp_path):
    app, settings, sent = make_app(tmp_path)
    with TestClient(app, base_url=PANEL_ORIGIN) as owner:
        login(owner)
        revision = owner.get("/api/server/bans").json()["targetRevision"]
        created = owner.post(
            "/api/subusers", json={"username": "Viewer", "password": SUB_PASSWORD},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert created.status_code == 201
        with TestClient(app, base_url=PANEL_ORIGIN) as viewer:
            login(viewer, "viewer", SUB_PASSWORD)
            before = len(sent)
            denied = viewer.put(
                "/api/server/world/lighting",
                json={"lighting": "DayClear", "targetRevision": revision},
                headers={"Origin": PANEL_ORIGIN},
            )
            assert denied.status_code == 403
            assert len(sent) == before
        before = len(sent)
        no_origin = owner.put(
            "/api/server/world/lighting",
            json={"lighting": "DayClear", "targetRevision": revision},
        )
        stale = owner.put(
            "/api/server/world/lighting",
            json={"lighting": "DayClear", "targetRevision": "old"},
            headers={"Origin": PANEL_ORIGIN},
        )
        invalid = owner.put(
            "/api/server/world/lighting",
            json={"lighting": "\n", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert no_origin.status_code == 401
        assert stale.status_code == 409
        assert invalid.status_code == 400
        assert invalid.json()["code"] == "invalid_selection"
        assert len(sent) == before
        assert owner.get("/api/server/status").status_code == 200
        assert owner.get("/api/server/status").status_code == 200
        assert sum(request.url.path == "/v1/status" for request in sent) == 1
        unavailable_choice = owner.put(
            "/api/server/world/lighting",
            json={"lighting": "Unlisted", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert unavailable_choice.status_code == 400
        assert unavailable_choice.json()["code"] == "invalid_selection"
        assert all(request.method == "GET" for request in sent)
        accepted = owner.put(
            "/api/server/world/lighting",
            json={"lighting": "DayClear", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert accepted.status_code == 200
        assert accepted.json() == {"ok": True}
        assert (sent[-1].method, sent[-1].url.path) == ("PUT", "/v1/world/lighting")
        assert json.loads(sent[-1].content) == {"lighting": "DayClear"}
        assert owner.get("/api/server/status").status_code == 200
        assert sum(request.url.path == "/v1/status" for request in sent) == 2
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute(
            "SELECT action, outcome FROM moderation_audit ORDER BY id"
        ).fetchall() == [
            ("setLighting", "rejected"), ("setLighting", "rejected"),
            ("setLighting", "accepted"),
        ]


def test_set_lighting_missing_capability_and_timeout_never_retry(tmp_path):
    app, _, sent = make_app(tmp_path, routes=["GET /v1/bans"])
    with TestClient(app, base_url=PANEL_ORIGIN) as owner:
        login(owner)
        revision = owner.get("/api/server/bans").json()["targetRevision"]
        before = len(sent)
        unsupported = owner.put(
            "/api/server/world/lighting",
            json={"lighting": "DayClear", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert unsupported.status_code == 501
        assert unsupported.json()["code"] == "action_unsupported"
        assert len(sent) == before

    writes = []

    def on_write(request):
        writes.append(request)
        raise httpx.ReadTimeout("simulated timeout", request=request)

    app, settings, _ = make_app(tmp_path / "timeout", on_write=on_write)
    with TestClient(app, base_url=PANEL_ORIGIN) as owner:
        login(owner)
        revision = owner.get("/api/server/bans").json()["targetRevision"]
        uncertain = owner.put(
            "/api/server/world/lighting",
            json={"lighting": "DayClear", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert uncertain.status_code == 502
        assert uncertain.json()["code"] == "action_uncertain"
        assert [(r.method, r.url.path) for r in writes] == [("PUT", "/v1/world/lighting")]
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute(
            "SELECT action, outcome FROM moderation_audit ORDER BY id"
        ).fetchall() == [("setLighting", "uncertain")]


def test_faction_change_reports_partial_respawn_result_without_retry(tmp_path):
    posts = []

    def on_write(request):
        posts.append((request.method, request.url.path))
        if request.url.path.endswith("/kill"):
            raise httpx.ReadTimeout("simulated timeout", request=request)
        return httpx.Response(200, json={"message": "moved"})

    app, settings, sent = make_app(tmp_path, on_write=on_write)
    with TestClient(app, base_url=PANEL_ORIGIN) as owner:
        login(owner)
        revision = owner.get("/api/server/bans").json()["targetRevision"]
        bad_faction = owner.post(
            "/api/server/factions",
            json={"steamId": STEAM_ID, "faction": "Unknown", "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert bad_faction.status_code == 400
        assert posts == []
        changed = owner.post(
            "/api/server/factions",
            json={"steamId": STEAM_ID, "faction": "Valkyra",
                  "respawn": True, "targetRevision": revision},
            headers={"Origin": PANEL_ORIGIN},
        )
        assert changed.status_code == 200
        assert changed.json() == {
            "ok": True, "respawned": None,
            "respawnUncertain": True, "respawnError": "action_uncertain",
        }
        assert posts == [
            ("PATCH", f"/v1/players/{STEAM_ID}"),
            ("POST", f"/v1/players/{STEAM_ID}/kill"),
        ]
        assert json.loads(next(request for request in sent if request.method == "PATCH").content) == {
            "faction": "Valkyra",
        }
    with sqlite3.connect(settings.db_path) as connection:
        rows = connection.execute("SELECT action, outcome FROM moderation_audit ORDER BY id").fetchall()
    assert rows == [
        ("changeFaction", "rejected"), ("changeFaction", "accepted"),
        ("kill", "uncertain"),
    ]
