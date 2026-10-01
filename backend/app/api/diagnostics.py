"""Authenticated, capability-gated diagnostics with safe field projections."""

from typing import Any
import asyncio
import json
from time import monotonic
from urllib.parse import urlsplit

import httpx

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin
from app.api.auth import require_owner
from app.updates import APP_VERSION
from app.rcon.diagnostics import PROJECTORS, observed_at
from app.rcon.routes import RouteName, route_for
from app.storage.db import AdminAccount


router = APIRouter(prefix="/api/server", tags=["diagnostics"])


async def probe_bot_gateway(origin: str, token: str | None, *, transport=None) -> dict[str, Any]:
    """Read the protected schema; never send a game operation or follow redirects."""
    if not token:
        return {"state": "unconfigured", "httpStatus": None, "latencyMs": None}
    started = monotonic()
    result: dict[str, Any] = {"state": "unreachable", "httpStatus": None, "latencyMs": None}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5, connect=2), trust_env=False,
                                     follow_redirects=False, transport=transport) as client:
            async with client.stream("GET", origin + "/api/bot/openapi.json",
                                     headers={"Authorization": "Bearer " + token}) as response:
                result["httpStatus"] = response.status_code
                if response.status_code in (401, 403):
                    result["state"] = "auth_rejected"
                elif response.status_code != 200:
                    result["state"] = "http_error"
                else:
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 131072:
                            result["state"] = "invalid_response"
                            break
                    else:
                        schema = json.loads(data)
                        expected = schema.get("paths", {}) if isinstance(schema, dict) else {}
                        result["state"] = "available" if isinstance(expected, dict) and "/api/bot/players/{steamId}/stats" in expected and "/api/bot/bans" in expected else "invalid_response"
    except (httpx.HTTPError, ValueError, TypeError):
        # Do not return exception text, which can contain URLs or credentials.
        result["state"] = "unreachable" if result["httpStatus"] is None else "invalid_response"
    result["latencyMs"] = round((monotonic() - started) * 1000)
    return result


@router.get("/bot-api-status")
async def get_bot_api_status(request: Request, _owner: AdminAccount = Depends(require_owner)) -> dict[str, Any]:
    settings = request.app.state.settings
    gateway = settings.bot_public_origin or settings.public_origin
    roles = [("read", settings.bot_read_token, ["personal:read"]),
             ("management", settings.bot_admin_token, ["personal:read", "players:read", "bans:write"])]
    probes = await asyncio.gather(*(probe_bot_gateway(gateway, token) for _, token, _ in roles))
    return {"apiVersion": "1", "panelVersion": APP_VERSION, "gatewayOrigin": gateway,
            "encrypted": urlsplit(gateway).scheme == "https", "observedAt": observed_at(),
            "probeSource": "panel_server", "probePath": "/api/bot/openapi.json",
            "credentials": [{"role": role, "configured": bool(token), "permissions": permissions if token else [], **result}
                            for (role, token, permissions), result in zip(roles, probes)]}


async def _read(request: Request, route: RouteName) -> dict[str, Any]:
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        spec = route_for(route)
        await runtime.capabilities.require_advertised(spec.method, spec.path)
        raw = await runtime.client.request(route)
        return {
            **PROJECTORS[route](raw),
            "observedAt": observed_at(),
            "stale": False,
            "targetRevision": runtime.target_revision,
        }


@router.get("/health")
async def get_health(
    request: Request, _admin: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    return await _read(request, RouteName.HEALTH)


@router.get("/server-id")
async def get_server_id(
    request: Request, _admin: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    return await _read(request, RouteName.SERVER_ID)


@router.get("/sponsor")
async def get_sponsor(
    request: Request, _admin: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    return await _read(request, RouteName.SPONSOR)
