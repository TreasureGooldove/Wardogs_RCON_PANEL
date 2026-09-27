"""Authenticated, capability-gated diagnostics with safe field projections."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin
from app.rcon.diagnostics import PROJECTORS, observed_at
from app.rcon.routes import RouteName, route_for
from app.storage.db import AdminAccount


router = APIRouter(prefix="/api/server", tags=["diagnostics"])


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
