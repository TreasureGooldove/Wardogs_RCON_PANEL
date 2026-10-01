"""Reserved slots backed by the current server config document."""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import require_admin
from app.errors import PanelError
from app.rcon.config_doc import (
    read_config,
    read_reserved,
    replace_reserved_ids,
    reserved_ids_from_text,
    send_config,
    valid_revision,
    valid_steam_id,
)
from app.rcon.routes import RouteName, WriteName, route_for, write_route_for
from app.storage.db import AdminAccount


router = APIRouter(prefix="/api/server/reserved-slots", tags=["reserved-slots"])


class ReservedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steamId: str = Field(min_length=17, max_length=17)
    revision: str = Field(min_length=1, max_length=128)
    targetRevision: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=200)
    days: int | None = Field(default=None, ge=1, le=3650)


class ReservedMetadataChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steamId: str = Field(min_length=17, max_length=17)
    reason: str = Field(max_length=200)
    days: int | None = Field(default=None, ge=1, le=3650)
    targetRevision: str = Field(min_length=1, max_length=128)


def _configured_ids(document: dict[str, Any]) -> list[str]:
    try:
        return reserved_ids_from_text(document["text"])
    except PanelError as exc:
        raise PanelError("invalid_upstream") from exc


async def _read_state(runtime: Any, database: Any) -> dict[str, Any]:
    config_spec = route_for(RouteName.CONFIG)
    reserved_spec = route_for(RouteName.RESERVED_SLOTS)
    await runtime.capabilities.require_advertised(config_spec.method, config_spec.path)
    await runtime.capabilities.require_advertised(reserved_spec.method, reserved_spec.path)
    document = await read_config(runtime.client)
    slots = await read_reserved(runtime.client)
    configured_slots = _configured_ids(document)
    try:
        await runtime.capabilities.require_advertised(
            write_route_for(WriteName.CONFIG_APPLY).method,
            write_route_for(WriteName.CONFIG_APPLY).path,
        )
    except PanelError as exc:
        if exc.code not in {"action_unsupported", "write_disabled"}:
            raise
        writable = False
    else:
        writable = document["writable"]
    return {
        "reservedSlots": slots,
        "configuredReservedSlots": configured_slots,
        "pendingRestart": set(slots) != set(configured_slots),
        "revision": document["revision"],
        "writable": writable,
        "targetRevision": runtime.target_revision,
        "metadata": database.reserved_metadata(runtime.client.target.origin),
    }


@router.get("")
async def get_reserved_slots(
    request: Request,
    response: Response,
    _account: AdminAccount = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        return await _read_state(runtime, request.app.state.database)


async def _change_slots(
    payload: ReservedChange, request: Request, *, add: bool
) -> dict[str, Any]:
    request.app.state.auth_service.check_origin(request)
    steam_id = valid_steam_id(payload.steamId)
    revision = valid_revision(payload.revision)
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        request.app.state.auth_service.require_permission(request, "reserved")
        if payload.targetRevision != runtime.target_revision:
            raise PanelError("stale_server_target")
        config_spec = route_for(RouteName.CONFIG)
        apply_spec = write_route_for(WriteName.CONFIG_APPLY)
        await runtime.capabilities.require_advertised(config_spec.method, config_spec.path)
        await runtime.capabilities.require_advertised(apply_spec.method, apply_spec.path)
        document = await read_config(runtime.client)
        if document["revision"] != revision:
            raise PanelError("config_conflict")
        if not document["writable"]:
            raise PanelError("write_disabled")
        ids = _configured_ids(document)
        if add:
            if steam_id in ids:
                raise PanelError("reserved_exists")
            next_ids = [*ids, steam_id]
        else:
            if steam_id not in ids:
                raise PanelError("reserved_missing")
            next_ids = [item for item in ids if item != steam_id]
        updated = replace_reserved_ids(document["text"], next_ids)
        result = await send_config(
            runtime.client, updated, apply=True, revision=revision
        )
        origin = runtime.client.target.origin
        if add:
            expires_at = (
                (datetime.now(timezone.utc) + timedelta(days=payload.days)).isoformat()
                if payload.days is not None else None
            )
            request.app.state.database.put_reserved_metadata(
                origin, steam_id, payload.reason.strip(), expires_at
            )
        else:
            request.app.state.database.remove_reserved_metadata(origin, steam_id)
        runtime.read_service.invalidate(RouteName.ROTATION)
        # PUT reports that the config was accepted, but does not guarantee the
        # currently running reserved-slot list changed. Refresh both with GETs.
        try:
            refreshed = await read_config(runtime.client)
            configured_slots: list[str] | None = _configured_ids(refreshed)
        except PanelError:
            refreshed = None
            configured_slots = None
        try:
            live_slots: list[str] | None = await read_reserved(runtime.client)
        except PanelError:
            live_slots = None
        return {
            "ok": True,
            "reservedSlots": live_slots,
            "configuredReservedSlots": configured_slots,
            "pendingRestart": (
                set(live_slots) != set(configured_slots)
                if live_slots is not None and configured_slots is not None
                else None
            ),
            "revision": refreshed["revision"] if refreshed is not None else result.get("revision"),
            "targetRevision": runtime.target_revision,
            "metadata": request.app.state.database.reserved_metadata(origin),
        }


@router.patch("/metadata")
async def update_reserved_metadata(
    payload: ReservedMetadataChange,
    request: Request,
    response: Response,
    _account: AdminAccount = Depends(require_admin),
) -> dict[str, Any]:
    request.app.state.auth_service.check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    steam_id = valid_steam_id(payload.steamId)
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        request.app.state.auth_service.require_permission(request, "reserved")
        if payload.targetRevision != runtime.target_revision:
            raise PanelError("stale_server_target")
        document = await read_config(runtime.client)
        if steam_id not in _configured_ids(document):
            raise PanelError("reserved_missing")
        expires_at = (
            (datetime.now(timezone.utc) + timedelta(days=payload.days)).isoformat()
            if payload.days is not None else None
        )
        request.app.state.database.put_reserved_metadata(
            runtime.client.target.origin, steam_id, payload.reason.strip(), expires_at
        )
        return await _read_state(runtime, request.app.state.database)


@router.post("")
async def add_reserved_slot(
    payload: ReservedChange,
    request: Request,
    response: Response,
    _owner: AdminAccount = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await _change_slots(payload, request, add=True)


@router.delete("")
async def remove_reserved_slot(
    payload: ReservedChange,
    request: Request,
    response: Response,
    _owner: AdminAccount = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await _change_slots(payload, request, add=False)
