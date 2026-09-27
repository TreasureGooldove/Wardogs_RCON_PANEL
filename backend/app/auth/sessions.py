"""Opaque Cookie sessions backed by revocable SQLite records."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
from typing import Callable

from fastapi import Request

from app.auth.passwords import hash_password, verify_password
from app.auth.rate_limit import LoginLimiter
from app.config import PanelSettings
from app.errors import PanelError
from app.storage.db import AdminAccount, Database


SESSION_COOKIE = "panel_session"
SESSION_TTL = timedelta(hours=8)
GRANTABLE_PERMISSIONS = frozenset({
    "unban", "kill", "message", "warning", "changeFaction", "broadcast",
    "changeMap", "endMatch", "restartMatch", "setLighting", "reserved",
    "config", "rules",
})


def _permissions(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if len(values) > len(GRANTABLE_PERMISSIONS) or any(
        not isinstance(value, str) or value not in GRANTABLE_PERMISSIONS for value in values
    ) or len(set(values)) != len(values):
        raise ValueError("invalid permissions")
    return tuple(sorted(values))


def _hash_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(
        self,
        database: Database,
        settings: PanelSettings,
        *,
        clock: Callable[[], datetime] | None = None,
        login_limiter: LoginLimiter | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.login_limiter = login_limiter or LoginLimiter()

    def create_admin(self, username: str, password: str) -> AdminAccount:
        return self.database.create_admin(username, hash_password(password), self.clock())

    def create_subuser(
        self, username: str, password: str, *, can_kick: bool = False, can_ban: bool = False,
        permissions: list[str] | tuple[str, ...] = (),
    ) -> AdminAccount:
        return self.database.create_subuser(
            username,
            hash_password(password),
            self.clock(),
            can_kick=can_kick,
            can_ban=can_ban,
            permissions=_permissions(permissions),
        )

    def update_subuser(
        self,
        account_id: str,
        *,
        can_kick: bool | None = None,
        can_ban: bool | None = None,
        disabled: bool | None = None,
        permissions: list[str] | tuple[str, ...] | None = None,
    ) -> AdminAccount | None:
        return self.database.update_subuser(
            account_id,
            self.clock(),
            can_kick=can_kick,
            can_ban=can_ban,
            disabled=disabled,
            permissions=_permissions(permissions) if permissions is not None else None,
        )

    def reset_subuser_password(self, account_id: str, password: str) -> AdminAccount | None:
        return self.database.reset_subuser_password(
            account_id, hash_password(password), self.clock()
        )

    def check_origin(self, request: Request) -> None:
        if request.headers.get("origin") != self.settings.public_origin:
            raise PanelError("not_authenticated")

    def login(self, username: str, password: str, client_key: str) -> tuple[AdminAccount, str]:
        now = self.clock()
        if self.login_limiter.is_limited(client_key, now):
            raise PanelError("rate_limited")
        admin = self.database.get_admin_by_username(username)
        if not verify_password(admin.password_hash if admin else None, password) or (admin and admin.disabled):
            self.login_limiter.record_failure(client_key, now)
            raise PanelError("invalid_credentials")
        assert admin is not None
        token = secrets.token_urlsafe(48)
        self.database.create_session(admin.id, _hash_token(token), now, now + SESSION_TTL)
        self.login_limiter.clear(client_key)
        return admin, token

    def require_admin(self, request: Request) -> AdminAccount:
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            raise PanelError("not_authenticated")
        admin = self.database.get_active_admin_by_token_hash(_hash_token(token), self.clock())
        if admin is None:
            raise PanelError("not_authenticated")
        return admin

    def require_owner(self, request: Request) -> AdminAccount:
        admin = self.require_admin(request)
        if admin.role != "owner":
            raise PanelError("permission_denied")
        return admin

    def require_action(self, request: Request, action: str) -> AdminAccount:
        return self.require_permission(request, action)

    def require_permission(self, request: Request, action: str) -> AdminAccount:
        admin = self.require_admin(request)
        if admin.role == "owner":
            return admin
        if action == "kick" and admin.can_kick:
            return admin
        if action == "ban" and admin.can_ban:
            return admin
        if action in GRANTABLE_PERMISSIONS and action in admin.permissions:
            return admin
        raise PanelError("permission_denied")

    def verify_current_password(self, request: Request, password: str) -> AdminAccount:
        account = self.require_admin(request)
        now = self.clock()
        client_host = request.client.host if request.client else "unknown"
        key = f"reauth:{account.id}:{client_host}"
        if self.login_limiter.is_limited(key, now):
            raise PanelError("rate_limited")
        if not verify_password(account.password_hash, password):
            self.login_limiter.record_failure(key, now)
            raise PanelError("invalid_credentials")
        self.login_limiter.clear(key)
        return account

    def logout(self, request: Request) -> None:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            self.database.revoke_session(_hash_token(token), self.clock())
