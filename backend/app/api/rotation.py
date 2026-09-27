"""Read-only rotation API."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin

router = APIRouter()


@router.get("/api/server/rotation", dependencies=[Depends(require_admin)])
async def get_rotation(request: Request) -> dict[str, Any]:
    return await request.app.state.read_service.rotation()
