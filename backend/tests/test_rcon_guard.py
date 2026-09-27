"""The panel can only issue documented, fixed read-only Wardogs requests."""

import asyncio
import logging

import httpx
import pytest

from app.config import RconTarget
from app.errors import PanelError
from app.rcon.client import RconClient
from app.rcon.routes import ROUTES, RouteName


ORIGIN = "https://wardogs.invalid"
SECRET = "local-test-canary-do-not-log"
ALLOWED_PATHS = {
    "/v1/capabilities",
    "/v1/status",
    "/v1/players",
    "/v1/rotation",
    "/v1/catalog/maps",
    "/v1/catalog/experiences",
    "/v1/catalog/lightings",
    "/v1/config",
    "/v1/reserved-slots",
    "/v1/bans",
    "/v1/audit",
    "/v1/sponsor",
    "/v1/server-id",
    "/v1/health",
    "/v1/catalog/maps/{id}/experiences",
    "/v1/catalog/maps/{id}/alternators",
}


def target() -> RconTarget:
    return RconTarget(origin=ORIGIN, bearer_secret=SECRET)


def test_exact_get_whitelist() -> None:
    assert len(ROUTES) == len(ALLOWED_PATHS)
    assert {route.path for route in ROUTES.values()} == ALLOWED_PATHS
    assert {route.method for route in ROUTES.values()} == {"GET"}
    assert set(ROUTES) == set(RouteName)


@pytest.mark.asyncio
async def test_cannot_select_path_target_method_or_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    client = RconClient(target(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(PanelError) as exc:
            await client.request("/v1/bans")  # type: ignore[arg-type]
        assert exc.value.code == "route_unsupported"
        with pytest.raises(PanelError):
            await client.request("https://elsewhere.invalid/v1/status")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            await client.request(RouteName.STATUS, method="POST")  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            await client.request(RouteName.STATUS, path="/v1/bans")  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            await client.request(RouteName.STATUS, query={"x": "y"})  # type: ignore[call-arg]
        assert requests == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_redirect_is_not_followed_and_secret_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.invalid/collect"})

    caplog.set_level(logging.DEBUG)
    client = RconClient(target(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(PanelError) as exc:
            await client.request(RouteName.STATUS)
        assert exc.value.code == "rcon_unavailable"
        assert len(requests) == 1
        assert requests[0].method == "GET"
        assert requests[0].url.host == "wardogs.invalid"
        assert requests[0].headers["Authorization"] == f"Bearer {SECRET}"
        assert SECRET not in str(exc.value)
        assert SECRET not in caplog.text
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_response_size_limit_is_enforced() -> None:
    payload = b"x" * 64
    constrained = RconTarget(origin=ORIGIN, bearer_secret=SECRET, max_response_bytes=32)
    client = RconClient(
        constrained,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload)),
    )
    try:
        with pytest.raises(PanelError) as exc:
            await client.request(RouteName.STATUS)
        assert exc.value.code == "invalid_upstream"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_only_bounded_get_network_errors_are_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, json={"ok": True})

    client = RconClient(target(), transport=httpx.MockTransport(handler))
    try:
        assert await client.request(RouteName.STATUS) == {"ok": True}
        assert attempts == 2
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "rcon_auth_failed"),
        (403, "rcon_auth_failed"),
        (404, "route_unsupported"),
        (429, "rcon_rate_limited"),
        (503, "rcon_unavailable"),
    ],
)
async def test_upstream_status_is_mapped_without_response_body(
    status: int, expected: str
) -> None:
    client = RconClient(
        target(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text=f"secret response {SECRET}")
        ),
    )
    try:
        with pytest.raises(PanelError) as exc:
            await client.request(RouteName.STATUS)
        assert exc.value.code == expected
        assert SECRET not in str(exc.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_malformed_json_or_encoding_is_invalid_upstream() -> None:
    responses = (
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(b"not-gzip")),
    )
    for response in responses:
        client = RconClient(target(), transport=httpx.MockTransport(lambda _: response))
        try:
            with pytest.raises(PanelError) as exc:
                await client.request(RouteName.STATUS)
            assert exc.value.code == "invalid_upstream"
        finally:
            await client.close()
