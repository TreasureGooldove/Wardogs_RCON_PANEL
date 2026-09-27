"""Administrator login and revocable Cookie session routes."""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.auth.sessions import AuthService, SESSION_COOKIE, SESSION_TTL
from app.storage.db import AdminAccount


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=1)


class AdminView(BaseModel):
    id: str
    username: str
    role: str
    canKick: bool
    canBan: bool
    permissions: list[str]


def _view(admin: AdminAccount) -> AdminView:
    return AdminView(
        id=admin.id,
        username=admin.username,
        role=admin.role,
        canKick=admin.can_kick,
        canBan=admin.can_ban,
        permissions=list(admin.permissions),
    )


def require_admin(request: Request) -> AdminAccount:
    """Shared business-route dependency, bound by the application entry point."""
    return request.app.state.auth_service.require_admin(request)


def require_owner(request: Request) -> AdminAccount:
    return request.app.state.auth_service.require_owner(request)


def build_auth_router(auth: AuthService) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])

    @router.post("/login", response_model=AdminView)
    def login(payload: LoginRequest, request: Request, response: Response) -> AdminView:
        auth.check_origin(request)
        client_key = request.client.host if request.client else "unknown"
        admin, token = auth.login(payload.username, payload.password.get_secret_value(), client_key)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=int(SESSION_TTL.total_seconds()),
            path="/",
            secure=auth.settings.session_secure,
            httponly=True,
            samesite="strict",
        )
        return _view(admin)

    @router.post("/logout", status_code=204)
    def logout(request: Request, response: Response, _admin: AdminAccount = Depends(auth.require_admin)) -> None:
        auth.check_origin(request)
        auth.logout(request)
        response.delete_cookie(SESSION_COOKIE, path="/", secure=auth.settings.session_secure, httponly=True, samesite="strict")

    @router.get("/me", response_model=AdminView)
    def me(admin: AdminAccount = Depends(auth.require_admin)) -> AdminView:
        return _view(admin)

    return router
