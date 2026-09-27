"""Authenticated Steam public profile lookup."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin
from app.errors import PanelError
from app.steam.service import STEAM_ID_RE, SteamProfileService


router = APIRouter(prefix="/api/steam", tags=["steam"])
MAX_REQUEST_BYTES = 4096


async def _steam_ids(request: Request) -> list[str]:
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_REQUEST_BYTES:
            raise PanelError("invalid_steam_ids")
        content.extend(chunk)
    try:
        body = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PanelError("invalid_steam_ids") from exc
    if not isinstance(body, dict) or set(body) != {"steamIds"}:
        raise PanelError("invalid_steam_ids")
    ids = body["steamIds"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
        raise PanelError("invalid_steam_ids")
    if any(not isinstance(value, str) or STEAM_ID_RE.fullmatch(value) is None for value in ids):
        raise PanelError("invalid_steam_ids")
    return ids


@router.post("/profiles", dependencies=[Depends(require_admin)])
async def profiles(request: Request) -> dict[str, list[dict[str, str | None]]]:
    request.app.state.auth_service.check_origin(request)
    ids = await _steam_ids(request)
    service: SteamProfileService = request.app.state.steam_service
    return {"profiles": await service.profiles(ids)}
