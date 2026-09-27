"""Extra Wardogs commands and reads tested only against HTTPX MockTransport."""

import json

import httpx
import pytest

from app.config import RconTarget
from app.errors import PanelError
from app.rcon.actions import ActionService, MapSelection
from app.rcon.client import RconClient
from app.rcon.reference_reads import ReferenceReadService
from app.rcon.routes import RouteName, WriteName


STEAM_ID = "76561198000000001"
SECRET = "local-test-secret"


@pytest.mark.asyncio
async def test_extra_actions_use_only_fixed_methods_paths_and_bodies():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"message": "accepted"})

    client = RconClient(
        RconTarget(origin="https://rcon.wardogs.invalid", bearer_secret=SECRET),
        transport=httpx.MockTransport(handler),
    )
    actions = ActionService(client)
    try:
        await actions.send(WriteName.UNBAN, steam_id=STEAM_ID)
        await actions.send(WriteName.KILL, steam_id=STEAM_ID)
        await actions.send(WriteName.MESSAGE, steam_id=STEAM_ID, message=" Hello ")
        await actions.send(WriteName.CHANGE_FACTION, steam_id=STEAM_ID, faction="Valkyra")
        await actions.send(WriteName.BROADCAST, message=" Match ending soon ")
        await actions.send(
            WriteName.CHANGE_MAP,
            selection=MapSelection("Narva", ("Invasion",), "Day", "North"),
        )
        await actions.send(WriteName.END_MATCH)
        await actions.send(WriteName.RESTART_MATCH)
        await actions.send(WriteName.SET_LIGHTING, lighting=" DayClear ")
    finally:
        await client.close()

    assert [(request.method, request.url.path) for request in sent] == [
        ("DELETE", f"/v1/bans/{STEAM_ID}"),
        ("POST", f"/v1/players/{STEAM_ID}/kill"),
        ("POST", f"/v1/players/{STEAM_ID}/message"),
        ("PATCH", f"/v1/players/{STEAM_ID}"),
        ("POST", "/v1/broadcast"),
        ("POST", "/v1/match/map"),
        ("POST", "/v1/match/end"),
        ("POST", "/v1/match/restart"),
        ("PUT", "/v1/world/lighting"),
    ]
    assert all(request.headers["Authorization"] == f"Bearer {SECRET}" for request in sent)
    assert json.loads(sent[2].content) == {"message": "Hello"}
    assert json.loads(sent[3].content) == {"faction": "Valkyra"}
    assert json.loads(sent[4].content) == {"message": "Match ending soon"}
    assert json.loads(sent[5].content) == {
        "map": "Narva", "experiences": ["Invasion"],
        "lighting": "Day", "zoneAlternator": "North",
    }
    assert json.loads(sent[8].content) == {"lighting": "DayClear"}


@pytest.mark.asyncio
async def test_extra_write_timeout_is_uncertain_and_never_retried():
    sent = []

    def handler(request):
        sent.append(request)
        raise httpx.ReadTimeout("simulated timeout", request=request)

    client = RconClient(
        RconTarget(origin="https://rcon.wardogs.invalid", bearer_secret=SECRET, read_retries=2),
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(PanelError) as exc:
            await ActionService(client).send(WriteName.BROADCAST, message="test")
        assert exc.value.code == "action_uncertain"
        assert len(sent) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_extra_reads_project_fields_and_reject_unbounded_parameters():
    sent = []

    def handler(request):
        sent.append((request.method, request.url.path, request.url.query))
        if request.url.path == "/v1/bans":
            return httpx.Response(200, json={"bans": [{
                "steamId": STEAM_ID, "bannedAtUtc": "2026-09-27T00:00:00Z",
                "bannedBy": "admin", "reason": "test", "internal": "drop",
            }], "internal": "drop"})
        if request.url.path == "/v1/audit":
            return httpx.Response(200, json={"entries": [{
                "timestampUtc": "2026-09-27T00:00:00Z", "peer": "127.0.0.1",
                "sessionId": "s1", "event": "login", "detail": "test", "secret": "drop",
            }]})
        if request.url.path.endswith("/experiences"):
            return httpx.Response(200, json={"experiences": ["Invasion", "AAS"]})
        if request.url.path.endswith("/alternators"):
            return httpx.Response(200, json={"alternators": [{
                "tag": "North", "displayName": "North Zone", "secret": "drop",
            }]})
        raise AssertionError("unexpected path")

    client = RconClient(
        RconTarget(origin="https://rcon.wardogs.invalid", bearer_secret=SECRET),
        transport=httpx.MockTransport(handler),
    )
    reads = ReferenceReadService(client)
    try:
        bans = await reads.fetch(RouteName.BANS)
        audit = await reads.fetch(RouteName.AUDIT, audit_limit=50)
        experiences = await reads.fetch(RouteName.MAP_EXPERIENCES, map_id="Narva")
        alternators = await reads.fetch(RouteName.MAP_ALTERNATORS, map_id="Narva")
        assert set(bans) == {"bans", "observedAt", "stale"}
        assert set(bans["bans"][0]) == {"steamId", "bannedAtUtc", "bannedBy", "reason"}
        assert set(audit["entries"][0]) == {"timestampUtc", "peer", "sessionId", "event", "detail"}
        assert experiences["items"] == [
            {"id": "Invasion", "label": "Invasion"}, {"id": "AAS", "label": "AAS"},
        ]
        assert alternators["items"] == [{"id": "North", "label": "North Zone"}]
        for route, kwargs in (
            (RouteName.AUDIT, {"audit_limit": 500}),
            (RouteName.MAP_EXPERIENCES, {"map_id": "../bans"}),
        ):
            with pytest.raises(PanelError) as exc:
                await reads.fetch(route, **kwargs)
            assert exc.value.code == "invalid_selection"
    finally:
        await client.close()
    assert sent == [
        ("GET", "/v1/bans", b""),
        ("GET", "/v1/audit", b"limit=50"),
        ("GET", "/v1/catalog/maps/Narva/experiences", b""),
        ("GET", "/v1/catalog/maps/Narva/alternators", b""),
    ]
