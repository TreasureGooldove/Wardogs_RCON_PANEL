"""Read-only online player API."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin

router = APIRouter()


@router.get("/api/server/players", dependencies=[Depends(require_admin)])
async def get_players(request: Request) -> dict[str, Any]:
    return await request.app.state.read_service.players()
