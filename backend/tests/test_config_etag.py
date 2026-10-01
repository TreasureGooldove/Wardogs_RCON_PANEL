"""Config read compatibility; all responses are fictitious and local."""
import asyncio

import httpx
import pytest

from app.config import RconTarget
from app.errors import PanelError
from app.rcon.client import RconClient
from app.rcon.config_doc import read_config


@pytest.mark.parametrize("etag", ['"revision-7"', 'W/"revision-7"', 'revision-7'])
def test_config_revision_falls_back_to_etag_without_rebuilding_text(etag):
    text = '; preserved\r\n[Unknown]\r\nValue=unchanged\r\n'
    def handler(request):
        assert request.method == "GET" and request.url.path == "/v1/config"
        assert request.headers["cache-control"] == "no-cache"
        return httpx.Response(200, headers={"ETag": etag}, json={
            "text": text, "writable": False, "sections": [], "warnings": []})
    async def run():
        client = RconClient(RconTarget(origin="https://rcon.example.invalid", bearer_secret="fake-test-key"), transport=httpx.MockTransport(handler))
        try:
            doc = await read_config(client)
            assert doc["revision"] == "revision-7"
            assert doc["text"] == text and doc["writable"] is False
        finally:
            await client.close()
    asyncio.run(run())


@pytest.mark.parametrize("revision,writable,expected", [
    ("body-revision", True, "body-revision"),
    (None, None, None),
])
def test_body_revision_has_priority_and_absent_write_flag_is_rejected(revision, writable, expected):
    def handler(request):
        return httpx.Response(200, headers={"ETag": '"header-revision"'}, json={
            "revision": revision, "text": "[Test]\nX=1\n", "writable": writable,
            "sections": [], "warnings": []})
    async def run():
        client = RconClient(RconTarget(origin="https://rcon.example.invalid", bearer_secret="fake-test-key"), transport=httpx.MockTransport(handler))
        try:
            if expected is None:
                with pytest.raises(PanelError):
                    await read_config(client)
            else:
                assert (await read_config(client))["revision"] == expected
        finally:
            await client.close()
    asyncio.run(run())
