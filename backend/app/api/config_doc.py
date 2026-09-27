"""Owner-only access to the server's whole configuration document."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.api.auth import require_admin
from app.errors import PanelError
from app.rcon.config_doc import read_config, send_config, valid_document_text, valid_revision
from app.rcon.config_redaction import (
    public_config_document,
    public_config_result,
    restore_config,
)
from app.rcon.routes import RouteName, WriteName, route_for, write_route_for
from app.storage.db import AdminAccount


router = APIRouter(prefix="/api/server/config", tags=["config"])


class ValidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1_048_576)
    targetRevision: str = Field(min_length=1, max_length=128)


class ApplyRequest(ValidateRequest):
    revision: str = Field(min_length=1, max_length=128)
    fullApply: StrictBool = False
    password: str = Field(min_length=1, max_length=256)


def _target(runtime: Any, revision: str) -> None:
    if revision != runtime.target_revision:
        raise PanelError("stale_server_target")


@router.get("")
async def get_config(
    request: Request,
    response: Response,
    _owner: AdminAccount = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        request.app.state.auth_service.require_permission(request, "config")
        spec = route_for(RouteName.CONFIG)
        await runtime.capabilities.require_advertised(spec.method, spec.path)
        document = await read_config(runtime.client)
        return {**public_config_document(document), "targetRevision": runtime.target_revision}


@router.post("/validate")
async def validate_config(
    payload: ValidateRequest,
    request: Request,
    response: Response,
    _owner: AdminAccount = Depends(require_admin),
) -> dict[str, Any]:
    request.app.state.auth_service.check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        request.app.state.auth_service.require_permission(request, "config")
        _target(runtime, payload.targetRevision)
        spec = write_route_for(WriteName.CONFIG_VALIDATE)
        await runtime.capabilities.require_advertised(spec.method, spec.path)
        read_spec = route_for(RouteName.CONFIG)
        await runtime.capabilities.require_advertised(read_spec.method, read_spec.path)
        current = await read_config(runtime.client)
        text = restore_config(payload.text, current["text"], current["revision"])
        result = await send_config(runtime.client, valid_document_text(text), apply=False)
        return public_config_result(result, payload.text)


@router.put("")
async def apply_config(
    payload: ApplyRequest,
    request: Request,
    response: Response,
    _owner: AdminAccount = Depends(require_admin),
) -> dict[str, Any]:
    request.app.state.auth_service.check_origin(request)
    response.headers["Cache-Control"] = "no-store"
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        request.app.state.auth_service.require_permission(request, "config")
        request.app.state.auth_service.verify_current_password(request, payload.password)
        _target(runtime, payload.targetRevision)
        spec = write_route_for(WriteName.CONFIG_APPLY)
        await runtime.capabilities.require_advertised(spec.method, spec.path)
        read_spec = route_for(RouteName.CONFIG)
        await runtime.capabilities.require_advertised(read_spec.method, read_spec.path)
        current = await read_config(runtime.client)
        revision = valid_revision(payload.revision)
        if current["revision"] != revision:
            raise PanelError("config_conflict")
        if not current["writable"]:
            raise PanelError("write_disabled")
        text = restore_config(payload.text, current["text"], revision)
        if text == current["text"] and not payload.fullApply:
            return {"ok": True, "revision": revision, "changed": [], "outcomes": []}
        validate_spec = write_route_for(WriteName.CONFIG_VALIDATE)
        await runtime.capabilities.require_advertised(validate_spec.method, validate_spec.path)
        checked = await send_config(runtime.client, valid_document_text(text), apply=False)
        if checked.get("ok") is not True or checked.get("errors"):
            return public_config_result(checked, payload.text)
        result = await send_config(
            runtime.client,
            text,
            apply=True,
            revision=revision,
            full_apply=payload.fullApply,
        )
        runtime.read_service.invalidate(RouteName.STATUS)
        runtime.read_service.invalidate(RouteName.ROTATION)
        return public_config_result(result, payload.text)
