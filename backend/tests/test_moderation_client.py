"""Local-only safety contracts for the two fixed Wardogs moderation writes."""

import json
import logging

import httpx
import pytest

from app.config import RconTarget
from app.errors import PanelError
from app.rcon.client import RconClient
from app.rcon.routes import WRITE_ROUTES, WriteName


SECRET = "local-test-canary-do-not-log"
STEAM_ID = "76561198000000001"


def client_for(handler) -> RconClient:
    return RconClient(
        RconTarget(
            origin="https://wardogs.invalid",
            bearer_secret=SECRET,
            read_retries=2,
        ),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_only_fixed_kick_and_ban_posts_are_sent(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"message": "ok"})

    client = client_for(handler)
    try:
        await client.moderate(WriteName.KICK, STEAM_ID, "rule breach")
        await client.moderate(WriteName.BAN, STEAM_ID, "repeat breach")
        assert WRITE_ROUTES[WriteName.KICK].path == "/v1/players/{steamId}/kick"
        assert WRITE_ROUTES[WriteName.BAN].path == "/v1/bans"
        assert [(request.method, request.url.path) for request in requests] == [
            ("POST", f"/v1/players/{STEAM_ID}/kick"),
            ("POST", "/v1/bans"),
        ]
        assert json.loads(requests[0].content) == {"reason": "rule breach"}
        assert json.loads(requests[1].content) == {
            "steamId": STEAM_ID,
            "reason": "repeat breach",
        }
        assert all(request.headers["Authorization"] == f"Bearer {SECRET}" for request in requests)
        assert SECRET not in caplog.text
        assert STEAM_ID not in caplog.text
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invalid_target_or_action_never_sends_network_request() -> None:
    requests: list[httpx.Request] = []
    client = client_for(lambda request: requests.append(request) or httpx.Response(200, json={}))
    try:
        with pytest.raises(PanelError) as invalid:
            await client.moderate(WriteName.KICK, "../bad")
        assert invalid.value.code == "invalid_moderation_target"
        with pytest.raises(PanelError) as unsupported:
            await client.moderate("/v1/config", STEAM_ID)  # type: ignore[arg-type]
        assert unsupported.value.code == "action_unsupported"
        with pytest.raises(PanelError) as other_action:
            await client.moderate(WriteName.UNBAN, STEAM_ID)
        assert other_action.value.code == "action_unsupported"
        assert requests == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_write_timeout_is_uncertain_and_never_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("simulated lost response", request=request)

    client = client_for(handler)
    try:
        with pytest.raises(PanelError) as uncertain:
            await client.moderate(WriteName.BAN, STEAM_ID)
        assert uncertain.value.code == "action_uncertain"
        assert attempts == 1
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(302, headers={"Location": "https://elsewhere.invalid/collect"}), "action_rejected"),
        (httpx.Response(500, json={"error": "failed"}), "action_uncertain"),
        (httpx.Response(200, json={"ok": False}), "action_rejected"),
    ],
)
async def test_rejection_or_uncertainty_never_follows_redirect_or_retries(response, expected) -> None:
    requests: list[httpx.Request] = []
    client = client_for(lambda request: requests.append(request) or response)
    try:
        with pytest.raises(PanelError) as error:
            await client.moderate(WriteName.KICK, STEAM_ID)
        assert error.value.code == expected
        assert len(requests) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_success_response_with_null_error_is_accepted() -> None:
    requests: list[httpx.Request] = []
    client = client_for(
        lambda request: requests.append(request)
        or httpx.Response(200, json={"error": None, "message": "Kicked"})
    )
    try:
        await client.moderate(WriteName.KICK, STEAM_ID)
        assert len(requests) == 1
    finally:
        await client.close()
