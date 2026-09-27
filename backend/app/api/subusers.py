"""Owner-only subuser management; password hashes never leave the server."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool

from app.api.auth import require_owner
from app.errors import PanelError
from app.storage.db import AdminAccount


router = APIRouter(prefix="/api/subusers", tags=["subusers"])


class SubuserView(BaseModel):
    id: str
    username: str
    role: str
    canKick: bool
    canBan: bool
    permissions: list[str]
    disabled: bool
    createdAt: datetime
    updatedAt: datetime


def _view(account: AdminAccount) -> SubuserView:
    return SubuserView(
        id=account.id,
        username=account.username,
        role=account.role,
        canKick=account.can_kick,
        canBan=account.can_ban,
        permissions=list(account.permissions),
        disabled=account.disabled,
        createdAt=account.created_at,
        updatedAt=account.updated_at,
    )


class SubuserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=12, max_length=256)
    canKick: StrictBool = False
    canBan: StrictBool = False
    permissions: list[str] = Field(default_factory=list, max_length=32)


class SubuserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canKick: StrictBool | None = None
    canBan: StrictBool | None = None
    permissions: list[str] | None = None
    disabled: StrictBool | None = None


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: SecretStr = Field(min_length=12, max_length=256)


@router.get("", response_model=list[SubuserView])
def list_subusers(
    request: Request, _owner: AdminAccount = Depends(require_owner)
) -> list[SubuserView]:
    return [_view(account) for account in request.app.state.database.list_subusers()]


@router.post("", response_model=SubuserView, status_code=201)
def create_subuser(
    payload: SubuserCreate,
    request: Request,
    _owner: AdminAccount = Depends(require_owner),
) -> SubuserView:
    auth = request.app.state.auth_service
    auth.check_origin(request)
    try:
        account = auth.create_subuser(
            payload.username,
            payload.password.get_secret_value(),
            can_kick=payload.canKick,
            can_ban=payload.canBan,
            permissions=payload.permissions,
        )
    except ValueError as exc:
        code = "username_exists" if str(exc) == "username already exists" else "invalid_subuser"
        raise PanelError(code) from exc
    return _view(account)


@router.patch("/{account_id}", response_model=SubuserView)
async def update_subuser(
    account_id: str,
    payload: SubuserPatch,
    request: Request,
    _owner: AdminAccount = Depends(require_owner),
) -> SubuserView:
    auth = request.app.state.auth_service
    auth.check_origin(request)
    if payload.canKick is None and payload.canBan is None and payload.disabled is None and payload.permissions is None:
        raise PanelError("invalid_subuser")
    async with request.app.state.rcon_runtime.lock:
        try:
            account = auth.update_subuser(
                account_id,
                can_kick=payload.canKick,
                can_ban=payload.canBan,
                disabled=payload.disabled,
                permissions=payload.permissions,
            )
        except ValueError as exc:
            raise PanelError("invalid_subuser") from exc
    if account is None:
        raise PanelError("subuser_not_found")
    return _view(account)


@router.post("/{account_id}/reset-password", status_code=204)
async def reset_subuser_password(
    account_id: str,
    payload: PasswordReset,
    request: Request,
    _owner: AdminAccount = Depends(require_owner),
) -> None:
    auth = request.app.state.auth_service
    auth.check_origin(request)
    async with request.app.state.rcon_runtime.lock:
        try:
            account = auth.reset_subuser_password(account_id, payload.password.get_secret_value())
        except ValueError as exc:
            raise PanelError("invalid_subuser") from exc
    if account is None:
        raise PanelError("subuser_not_found")
