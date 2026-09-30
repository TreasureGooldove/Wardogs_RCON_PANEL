"""Authenticated panel release information."""

from fastapi import APIRouter, Depends, Request, Response
from app.api.auth import require_admin

router = APIRouter(prefix="/api/panel", dependencies=[Depends(require_admin)])


@router.get("/updates")
async def updates(request: Request, response: Response, refresh: bool = False):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.release_checker.check(refresh=refresh)
