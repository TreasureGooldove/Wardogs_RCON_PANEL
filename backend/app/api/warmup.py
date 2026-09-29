"""Owner-only warmup reward policy and audit."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictInt, model_validator

from app.api.auth import require_owner
from app.errors import PanelError
from app.warmup.store import DEFAULT_NOTIFICATION_TEXT


router = APIRouter(prefix="/api/warmup", dependencies=[Depends(require_owner)])


class WarmupConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targetRevision: str = Field(min_length=1, max_length=128)
    enabled: StrictBool
    playerThreshold: StrictInt = Field(ge=1, le=100)
    resetThreshold: StrictInt = Field(ge=0, le=99)
    resetMinutes: StrictInt = Field(ge=1, le=180)
    giftDays: StrictInt = Field(ge=1, le=3650)
    intervalMode: Literal["daily", "hours"] = "daily"  # Legacy clients; both limits apply.
    intervalHours: StrictInt = Field(ge=1, le=720)
    notificationMode: Literal["private", "broadcast"]
    notificationText: str = Field(default=DEFAULT_NOTIFICATION_TEXT, min_length=1, max_length=200)
    password: SecretStr | None = None

    @model_validator(mode="after")
    def valid_notification_text(self):
        if self.resetThreshold >= self.playerThreshold:
            raise ValueError("回落人数必须小于目标人数")
        self.notificationText = self.notificationText.strip()
        rendered = self.notificationText.replace("{x}", str(self.giftDays))
        if not self.notificationText or len(rendered) > 200 or any(
            ord(char) < 32 or ord(char) == 127 for char in rendered
        ):
            raise ValueError("通知内容不能为空、包含换行或超过 200 字")
        return self


class AcknowledgeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targetRevision: str = Field(min_length=1, max_length=128)
    password: SecretStr


def _public(request: Request) -> dict:
    runtime = request.app.state.rcon_runtime
    target = runtime.target
    origin = target.origin if target else ""
    config = request.app.state.warmup_store.config()
    return {
        "enabled": config["enabled"] and config["origin"] == origin,
        "playerThreshold": config["player_threshold"],
        "resetThreshold": config["reset_threshold"],
        "resetMinutes": config["reset_minutes"],
        "giftDays": config["gift_days"],
        "intervalMode": config["interval_mode"],
        "intervalHours": config["interval_hours"],
        "notificationMode": config["notification_mode"],
        "notificationText": config["notification_text"],
        "updatedAt": config["updated_at"],
        "targetRevision": runtime.target_revision,
        "collectorEnabled": request.app.state.settings.history_enabled,
        "configured": target is not None,
        "runs": request.app.state.warmup_store.recent(origin),
        **request.app.state.warmup_engine.status(origin),
    }


@router.get("")
async def get_warmup(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    async with request.app.state.rcon_runtime.lock:
        return _public(request)


@router.get("/status")
async def get_warmup_status(request: Request, response: Response) -> dict:
    """Lightweight status for the visible settings page; never contacts RCON."""
    response.headers["Cache-Control"] = "no-store"
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        origin = runtime.target.origin if runtime.target else ""
        config = request.app.state.warmup_store.config()
        status = request.app.state.warmup_engine.status(origin)
        return {
            "observedPlayers": status["observedPlayers"],
            "playerThreshold": config["player_threshold"],
            "cyclePhase": status["cyclePhase"],
            "nextDetectionAt": status["nextDetectionAt"],
            "attentionRequired": status["attentionRequired"],
            "lastRunId": status["lastRun"]["id"] if status["lastRun"] else None,
        }


@router.put("")
async def save_warmup(payload: WarmupConfigBody, request: Request,
                      response: Response) -> dict:
    request.app.state.auth_service.check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        if runtime.target is None or payload.targetRevision != runtime.target_revision:
            raise PanelError("stale_server_target")
        if payload.enabled:
            if not request.app.state.settings.history_enabled or payload.password is None:
                raise PanelError("invalid_selection")
            request.app.state.auth_service.verify_current_password(
                request, payload.password.get_secret_value())
        request.app.state.warmup_store.save(
            runtime.target.origin,
            enabled=payload.enabled,
            player_threshold=payload.playerThreshold,
            reset_threshold=payload.resetThreshold,
            reset_minutes=payload.resetMinutes,
            gift_days=payload.giftDays,
            interval_mode=payload.intervalMode,
            interval_hours=payload.intervalHours,
            notification_mode=payload.notificationMode,
            notification_text=payload.notificationText,
        )
        return _public(request)


@router.post("/runs/{run_id}/acknowledge")
async def acknowledge_warmup(run_id: str, payload: AcknowledgeBody,
                             request: Request, response: Response) -> dict:
    request.app.state.auth_service.check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        if runtime.target is None or payload.targetRevision != runtime.target_revision:
            raise PanelError("stale_server_target")
        request.app.state.auth_service.verify_current_password(
            request, payload.password.get_secret_value())
        if not request.app.state.warmup_store.acknowledge(runtime.target.origin, run_id):
            raise PanelError("invalid_selection")
        return _public(request)
