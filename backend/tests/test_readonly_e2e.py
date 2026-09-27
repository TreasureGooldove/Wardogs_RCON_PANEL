"""Local end-to-end proof of the panel's read-only Wardogs flow."""

import logging

from fastapi.testclient import TestClient

from app.config import PanelSettings, RconTarget
from app.main import create_app
from tests.mock_rcon import MOCK_BEARER, READ_PATHS, running_mock_rcon


def test_login_read_every_section_and_logout_with_local_mock(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    public_origin = "http://127.0.0.1:8000"

    with running_mock_rcon() as mock:
        settings = PanelSettings(
            db_path=tmp_path / "panel.sqlite3",
            public_origin=public_origin,
            session_secure=False,
            rcon_target=RconTarget(
                origin=f"http://127.0.0.1:{mock.server_port}",
                bearer_secret=MOCK_BEARER,
                allow_private_http=True,
                read_retries=0,
            ),
        )
        app = create_app(settings)
        app.state.auth_service.create_admin("PanelAdmin", "local-only-test-password")

        with TestClient(app, base_url=public_origin) as client:
            responses = []
            before_login = client.get("/api/server/status")
            responses.append(before_login)
            assert before_login.status_code == 401
            assert mock.requests == []

            login = client.post(
                "/api/auth/login",
                json={"username": "PanelAdmin", "password": "local-only-test-password"},
                headers={"Origin": public_origin},
            )
            responses.append(login)
            assert login.status_code == 200
            assert login.json()["username"] == "PanelAdmin"
            assert "panel_session" in client.cookies

            status = client.get("/api/server/status")
            players = client.get("/api/server/players")
            capabilities = client.get("/api/server/capabilities")
            maps = client.get("/api/server/catalog/maps")
            experiences = client.get("/api/server/catalog/experiences")
            lightings = client.get("/api/server/catalog/lightings")
            rotation = client.get("/api/server/rotation")
            responses.extend(
                [status, players, capabilities, maps, experiences, lightings, rotation]
            )
            assert all(response.status_code == 200 for response in responses[2:])

            assert status.json()["map"] == "Narva"
            assert status.json()["playerCount"] == 2
            assert status.json()["stale"] is False
            assert status.json()["observedAt"]

            listed_players = players.json()["players"]
            assert len(listed_players) == 2
            assert listed_players[0]["name"] == listed_players[1]["name"]
            assert listed_players[0]["steamId"] != listed_players[1]["steamId"]
            assert listed_players[1]["kills"] is None

            assert capabilities.json()["state"] == "available"
            assert all(
                capabilities.json()["features"][name]
                for name in ("status", "players", "rotation", "maps", "experiences", "lightings")
            )
            assert capabilities.json()["features"]["kick"] is False
            assert capabilities.json()["features"]["ban"] is False
            assert maps.json()["kind"] == "maps"
            assert maps.json()["items"] == [{"id": "Narva", "label": "Narva"}]
            assert experiences.json()["kind"] == "experiences"
            assert len(experiences.json()["items"]) == 2
            assert lightings.json()["kind"] == "lightings"
            assert len(lightings.json()["items"]) == 2
            assert rotation.json()["mode"] == "ordered"
            assert len(rotation.json()["items"]) == 2

            logout = client.post("/api/auth/logout", headers={"Origin": public_origin})
            responses.append(logout)
            assert logout.status_code == 204
            after_logout = client.get("/api/server/status")
            responses.append(after_logout)
            assert after_logout.status_code == 401
            assert after_logout.json()["code"] == "not_authenticated"

        assert not mock.write_attempted
        assert set(mock.requests) == {("GET", path) for path in READ_PATHS}
        assert all(method == "GET" and path in READ_PATHS for method, path in mock.requests)
        assert MOCK_BEARER not in caplog.text
        assert all(
            MOCK_BEARER not in response.text + str(response.headers)
            for response in responses
        )
