"""Authenticated reads of local, observed history. No upstream calls."""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.auth import require_admin


router = APIRouter(prefix="/api/history", dependencies=[Depends(require_admin)])


async def _origin(request: Request) -> str:
    async with request.app.state.rcon_runtime.lock:
        target = request.app.state.rcon_runtime.client.target
        return target.origin if target else ""


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/matches")
async def matches(request: Request, response: Response,
                  limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)) -> dict:
    _no_store(response)
    return request.app.state.history_store.matches(await _origin(request), limit, offset)


@router.get("/matches/{match_id}")
async def match(match_id: str, request: Request, response: Response) -> dict:
    _no_store(response)
    try:
        UUID(match_id)
    except ValueError as exc:
        raise HTTPException(404) from exc
    result = request.app.state.history_store.match(await _origin(request), match_id)
    if result is None:
        raise HTTPException(404)
    return result


@router.get("/players")
async def players(request: Request, response: Response,
                  search: str = Query("", max_length=64),
                  limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0, le=10000)) -> dict:
    _no_store(response)
    return request.app.state.history_store.players(await _origin(request), search.strip(), limit, offset)


@router.get("/players/{steam_id}")
async def player(steam_id: str, request: Request, response: Response) -> dict:
    _no_store(response)
    if re.fullmatch(r"[1-9][0-9]{16}", steam_id) is None:
        raise HTTPException(404)
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        target = runtime.target
        result = request.app.state.history_store.player(target.origin if target else "", steam_id)
        if result is None:
            raise HTTPException(404)
        return {**result, "targetRevision": runtime.target_revision}
