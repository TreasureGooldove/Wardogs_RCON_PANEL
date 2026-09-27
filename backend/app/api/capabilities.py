"""Read-only capability API."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin

router = APIRouter()


@router.get("/api/server/capabilities", dependencies=[Depends(require_admin)])
async def get_capabilities(request: Request) -> dict[str, Any]:
    return await request.app.state.capability_service.get()
