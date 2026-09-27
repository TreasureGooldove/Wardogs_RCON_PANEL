"""Read-only server status API."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin

router = APIRouter()


@router.get("/api/server/status", dependencies=[Depends(require_admin)])
async def get_status(request: Request) -> dict[str, Any]:
    return await request.app.state.read_service.status()
