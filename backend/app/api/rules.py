"""Owner-controlled rules messenger configuration and delivery inspection."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.api.auth import require_admin
from app.errors import PanelError
from app.rcon.actions import valid_steam_id
from app.rules.engine import render_parts, validate_template


router = APIRouter(prefix="/api/rules", dependencies=[Depends(require_admin)])


class RulesConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targetRevision: str = Field(min_length=1, max_length=128)
    enabled: StrictBool
    firstText: str = Field(max_length=2000)
    secondText: str = Field(max_length=2000)
    delaySeconds: StrictInt = Field(ge=0, le=600)
    gapSeconds: StrictInt = Field(ge=0, le=120)
    cooldownMinutes: StrictInt = Field(ge=1, le=1440)
    maxPerRound: StrictInt = Field(ge=1, le=10)


class ManualBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steamId: str
    targetRevision: str = Field(min_length=1, max_length=128)


def _public(config: dict) -> dict:
    return {
        "configuredOrigin": config["origin"],
        "enabled": config["enabled"],
        "firstText": config["first_text"],
        "secondText": config["second_text"],
        "delaySeconds": config["delay_seconds"],
        "gapSeconds": config["gap_seconds"],
        "cooldownMinutes": config["cooldown_minutes"],
        "maxPerRound": config["max_per_round"],
        "updatedAt": config["updated_at"],
    }


@router.get("")
async def get_rules(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        request.app.state.auth_service.require_permission(request, "rules")
        config = request.app.state.rules_store.config()
        target = runtime.target
        return {**_public(config), "currentOrigin": target.origin if target else "",
                "targetRevision": runtime.target_revision,
                "collectorEnabled": request.app.state.settings.history_enabled,
                **request.app.state.rules_engine.status()}


@router.put("")
async def save_rules(payload: RulesConfigBody, request: Request) -> dict:
    request.app.state.auth_service.check_origin(request)
    try:
        first = validate_template(payload.firstText, required=True)
        second = validate_template(payload.secondText, required=False)
        render_parts(first, "X" * 64, "76561190000000001", 999)
        if second:
            render_parts(second, "X" * 64, "76561190000000001", 999)
    except ValueError as exc:
        raise PanelError("invalid_selection") from exc
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        request.app.state.auth_service.require_permission(request, "rules")
        target = runtime.target
        if target is None or payload.targetRevision != runtime.target_revision:
            raise PanelError("stale_server_target")
        if payload.enabled and not request.app.state.settings.history_enabled:
            raise PanelError("invalid_selection")
        config = request.app.state.rules_store.save(
            target.origin, enabled=payload.enabled, first_text=first, second_text=second,
            delay_seconds=payload.delaySeconds, gap_seconds=payload.gapSeconds,
            cooldown_minutes=payload.cooldownMinutes, max_per_round=payload.maxPerRound,
        )
        request.app.state.rules_engine.reset()
        return {**_public(config), "currentOrigin": target.origin,
                "targetRevision": runtime.target_revision,
                "collectorEnabled": request.app.state.settings.history_enabled,
                **request.app.state.rules_engine.status()}


@router.get("/deliveries")
async def deliveries(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        request.app.state.auth_service.require_permission(request, "rules")
        target = runtime.target
        return {"items": request.app.state.rules_store.recent(target.origin if target else "")}


@router.post("/manual")
async def manual(payload: ManualBody, request: Request) -> dict:
    request.app.state.auth_service.check_origin(request)
    request.app.state.auth_service.require_permission(request, "rules")
    steam_id = valid_steam_id(payload.steamId)
    return await request.app.state.rules_engine.send_manual(steam_id, payload.targetRevision)
