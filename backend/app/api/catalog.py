"""Read-only catalog API."""

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin

router = APIRouter()


@router.get("/api/server/catalog/{kind}", dependencies=[Depends(require_admin)])
async def get_catalog(kind: str, request: Request) -> dict[str, Any]:
    return await request.app.state.read_service.catalog(kind)
