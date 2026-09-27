"""Authenticated, fixed-path player moderation with no automatic write retry."""

import logging
import re

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import require_admin
from app.errors import PanelError
from app.rcon.routes import WriteName
from app.storage.db import AdminAccount


router = APIRouter()
_LOG = logging.getLogger(__name__)
_STEAM_ID = re.compile(r"^[1-9][0-9]{16}$")


class ModerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steamId: str
    reason: str = Field(default="", max_length=200)
    targetRevision: str | None = None


def _target(value: str) -> str:
    if _STEAM_ID.fullmatch(value) is None:
        raise PanelError("invalid_moderation_target")
    return value


def _reason(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) > 200 or any(ord(char) < 32 for char in cleaned):
        raise PanelError("invalid_moderation_reason")
    return cleaned


def _audit(
    request: Request,
    admin: AdminAccount,
    action: WriteName,
    steam_id: str,
    reason: str,
    outcome: str,
    target_revision: str,
    target_origin: str,
) -> None:
    try:
        request.app.state.database.append_moderation_audit(
            admin.id,
            action.value,
            steam_id,
            outcome,
            reason,
            request.state.request_id,
            target_revision,
            target_origin,
        )
    except Exception:
        # An audit storage failure must not turn a completed action into a
        # retryable error in the browser.
        _LOG.exception("Moderation audit append failed: action=%s outcome=%s", action.value, outcome)


async def _perform(
    request: Request,
    admin: AdminAccount,
    action: WriteName,
    steam_id: str,
    reason: str,
    target_revision: str | None,
) -> dict[str, bool]:
    request.app.state.auth_service.check_origin(request)
    request.app.state.auth_service.require_action(request, action.value)
    steam_id = _target(steam_id)
    reason = _reason(reason)
    runtime = request.app.state.rcon_runtime
    matched_revision: str | None = None
    matched_origin: str | None = None
    try:
        async with runtime.lock:
            request.app.state.auth_service.require_action(request, action.value)
            if not target_revision or target_revision != runtime.target_revision:
                raise PanelError("stale_server_target")
            matched_revision = runtime.target_revision
            matched_origin = runtime.client.target.origin if runtime.client.target else None
            await runtime.capabilities.require_write(action)
            await runtime.client.moderate(action, steam_id, reason)
            runtime.invalidate_players_unlocked()
    except PanelError as exc:
        if matched_revision is not None and matched_origin is not None:
            if exc.code == "action_uncertain":
                _audit(request, admin, action, steam_id, reason, "uncertain", matched_revision, matched_origin)
            elif exc.code not in {"rcon_unconfigured", "rcon_unavailable", "rcon_timeout"}:
                _audit(request, admin, action, steam_id, reason, "rejected", matched_revision, matched_origin)
        raise
    assert matched_revision is not None and matched_origin is not None
    _audit(request, admin, action, steam_id, reason, "accepted", matched_revision, matched_origin)
    return {"ok": True}


@router.post("/api/server/kicks")
async def kick_player(
    payload: ModerationRequest,
    request: Request,
    admin: AdminAccount = Depends(require_admin),
) -> dict[str, bool]:
    return await _perform(request, admin, WriteName.KICK, payload.steamId, payload.reason, payload.targetRevision)


@router.post("/api/server/bans")
async def ban_player(
    payload: ModerationRequest,
    request: Request,
    admin: AdminAccount = Depends(require_admin),
) -> dict[str, bool]:
    return await _perform(request, admin, WriteName.BAN, payload.steamId, payload.reason, payload.targetRevision)
