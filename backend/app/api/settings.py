"""Authenticated configuration for the one managed Wardogs server."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.api.auth import require_owner
from app.storage.db import AdminAccount


router = APIRouter(prefix="/api/server/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    origin: str = Field(min_length=1, max_length=2048)
    bearer: SecretStr | None = None
    allowPublicHttp: bool = False


@router.get("")
async def get_settings(
    request: Request, _admin: AdminAccount = Depends(require_owner)
) -> dict[str, Any]:
    return await request.app.state.rcon_runtime.view()


@router.put("")
async def put_settings(
    payload: SettingsUpdate,
    request: Request,
    _admin: AdminAccount = Depends(require_owner),
) -> dict[str, Any]:
    request.app.state.auth_service.check_origin(request)
    bearer = payload.bearer.get_secret_value() if payload.bearer is not None else None
    return await request.app.state.rcon_runtime.save(
        name=payload.name,
        origin=payload.origin,
        bearer=bearer,
        allow_public_http=payload.allowPublicHttp,
    )
