"""SQLite administrator and session storage."""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4


@dataclass(frozen=True)
class AdminAccount:
    id: str
    username: str
    password_hash: str = field(repr=False)
    disabled: bool
    created_at: datetime
    updated_at: datetime
    role: str = "owner"
    can_kick: bool = True
    can_ban: bool = True
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ServerSettingsRecord:
    name: str
    origin: str
    bearer_ciphertext: str = field(repr=False)
    allow_public_http: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def normalize_username(username: str) -> str:
    normalized = username.strip().casefold()
    if not 3 <= len(normalized) <= 64:
        raise ValueError("administrator username must be 3 to 64 characters")
    return normalized


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must have timezone")
    return value.astimezone(timezone.utc)


def _read_admin(row: sqlite3.Row) -> AdminAccount:
    return AdminAccount(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        disabled=bool(row["disabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        role=row["role"],
        can_kick=row["role"] == "owner" or bool(row["can_kick"]),
        can_ban=row["role"] == "owner" or bool(row["can_ban"]),
        permissions=tuple(json.loads(row["permissions_json"])),
    )


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS rcon_connection_control (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    paused INTEGER NOT NULL CHECK(paused IN (0, 1)),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admins (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0 CHECK(disabled IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'owner' CHECK(role IN ('owner', 'subuser')),
                    can_kick INTEGER NOT NULL DEFAULT 0 CHECK(can_kick IN (0, 1)),
                    can_ban INTEGER NOT NULL DEFAULT 0 CHECK(can_ban IN (0, 1))
                    ,permissions_json TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    admin_id TEXT NOT NULL REFERENCES admins(id),
                    token_hash TEXT NOT NULL UNIQUE,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_admin_id ON sessions(admin_id);
                CREATE TABLE IF NOT EXISTS server_settings (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    name TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    bearer_ciphertext TEXT NOT NULL,
                    allow_public_http INTEGER NOT NULL CHECK(allow_public_http IN (0, 1)),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS moderation_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id TEXT NOT NULL REFERENCES admins(id),
                    action TEXT NOT NULL,
                    steam_id TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(outcome IN ('accepted', 'rejected', 'uncertain')),
                    reason TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    target_revision TEXT NOT NULL DEFAULT '',
                    target_origin TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_moderation_audit_created_at
                    ON moderation_audit(created_at);
                CREATE TABLE IF NOT EXISTS reserved_slot_metadata (
                    origin TEXT NOT NULL,
                    steam_id TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    expires_at TEXT,
                    status TEXT NOT NULL CHECK(status IN ('active', 'processing', 'attention')),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(origin, steam_id)
                );
                CREATE INDEX IF NOT EXISTS idx_reserved_slot_due
                    ON reserved_slot_metadata(status, expires_at);
                """
            )
            admin_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(admins)")
            }
            if "role" not in admin_columns:
                connection.execute(
                    "ALTER TABLE admins ADD COLUMN role TEXT NOT NULL DEFAULT 'owner' "
                    "CHECK(role IN ('owner', 'subuser'))"
                )
            if "can_kick" not in admin_columns:
                connection.execute(
                    "ALTER TABLE admins ADD COLUMN can_kick INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(can_kick IN (0, 1))"
                )
            if "can_ban" not in admin_columns:
                connection.execute(
                    "ALTER TABLE admins ADD COLUMN can_ban INTEGER NOT NULL DEFAULT 0 "
                    "CHECK(can_ban IN (0, 1))"
                )
            if "permissions_json" not in admin_columns:
                connection.execute(
                    "ALTER TABLE admins ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '[]'"
                )
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_admins_owner ON admins(role) WHERE role = 'owner'")
            connection.execute("UPDATE admins SET can_kick = 1, can_ban = 1 WHERE role = 'owner'")
            audit_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(moderation_audit)")
            }
            if "target_revision" not in audit_columns:
                connection.execute(
                    "ALTER TABLE moderation_audit ADD COLUMN target_revision TEXT NOT NULL DEFAULT ''"
                )
            if "target_origin" not in audit_columns:
                connection.execute(
                    "ALTER TABLE moderation_audit ADD COLUMN target_origin TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_moderation_audit_warning_target "
                "ON moderation_audit(target_origin, steam_id, action, id DESC)"
            )

    def connection_control(self) -> dict:
        with self._connect() as connection:
            row = connection.execute('SELECT paused, updated_at FROM rcon_connection_control WHERE id=1').fetchone()
        return {'paused': bool(row['paused']), 'updatedAt': row['updated_at']} if row else {'paused': False, 'updatedAt': None}

    def set_connection_paused(self, paused: bool) -> dict:
        stamp = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute('INSERT INTO rcon_connection_control VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET paused=excluded.paused,updated_at=excluded.updated_at', (int(paused), stamp))
        return {'paused': paused, 'updatedAt': stamp}

    def reserved_metadata(self, origin: str) -> dict[str, dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT steam_id, reason, expires_at, status FROM reserved_slot_metadata "
                "WHERE origin = ? ORDER BY steam_id", (origin,)
            ).fetchall()
        return {
            row["steam_id"]: {
                "reason": row["reason"], "expiresAt": row["expires_at"],
                "status": row["status"],
            }
            for row in rows
        }

    def put_reserved_metadata(
        self, origin: str, steam_id: str, reason: str, expires_at: str | None,
        *, status: str = "active",
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO reserved_slot_metadata
                   (origin, steam_id, reason, expires_at, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(origin, steam_id) DO UPDATE SET
                     reason=excluded.reason, expires_at=excluded.expires_at,
                     status=excluded.status, updated_at=excluded.updated_at""",
                (origin, steam_id, reason, expires_at, status,
                 datetime.now(timezone.utc).isoformat()),
            )

    def remove_reserved_metadata(self, origin: str, steam_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM reserved_slot_metadata WHERE origin = ? AND steam_id = ?",
                (origin, steam_id),
            )

    def due_reserved_slots(self, origin: str, now: datetime) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT steam_id FROM reserved_slot_metadata "
                "WHERE origin = ? AND status = 'active' AND expires_at IS NOT NULL "
                "AND expires_at <= ? ORDER BY expires_at LIMIT 20",
                (origin, _utc(now).isoformat()),
            ).fetchall()
        return [row["steam_id"] for row in rows]

    def mark_reserved_status(self, origin: str, steam_id: str, old: str, new: str) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                "UPDATE reserved_slot_metadata SET status = ?, updated_at = ? "
                "WHERE origin = ? AND steam_id = ? AND status = ?",
                (new, datetime.now(timezone.utc).isoformat(), origin, steam_id, old),
            )
            return result.rowcount == 1

    def get_server_settings(self) -> ServerSettingsRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM server_settings WHERE id = 1").fetchone()
        if row is None:
            return None
        return ServerSettingsRecord(
            name=row["name"],
            origin=row["origin"],
            bearer_ciphertext=row["bearer_ciphertext"],
            allow_public_http=bool(row["allow_public_http"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def put_server_settings(
        self,
        name: str,
        origin: str,
        bearer_ciphertext: str,
        allow_public_http: bool,
        now: datetime,
    ) -> ServerSettingsRecord:
        timestamp = _utc(now).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO server_settings
                   (id, name, origin, bearer_ciphertext, allow_public_http, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       name=excluded.name,
                       origin=excluded.origin,
                       bearer_ciphertext=excluded.bearer_ciphertext,
                       allow_public_http=excluded.allow_public_http,
                       updated_at=excluded.updated_at""",
                (name, origin, bearer_ciphertext, int(allow_public_http), timestamp),
            )
        return ServerSettingsRecord(name, origin, bearer_ciphertext, allow_public_http, _utc(now))

    def append_moderation_audit(
        self,
        admin_id: str,
        action: str,
        steam_id: str,
        outcome: str,
        reason: str,
        request_id: str,
        target_revision: str = "",
        target_origin: str = "",
    ) -> None:
        if outcome not in {"accepted", "rejected", "uncertain"} or len(reason) > 200:
            raise ValueError("invalid moderation audit entry")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO moderation_audit
                   (admin_id, action, steam_id, outcome, reason, request_id, created_at, target_revision, target_origin)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    admin_id,
                    action,
                    steam_id,
                    outcome,
                    reason,
                    request_id,
                    datetime.now(timezone.utc).isoformat(),
                    target_revision,
                    target_origin,
                ),
            )

    def warning_history(self, steam_id: str, target_origin: str, *, limit: int = 20) -> tuple[int, list[dict]]:
        """Accepted count and recent local warning attempts for one target."""
        if not 1 <= limit <= 100:
            raise ValueError("invalid warning history limit")
        with self._connect() as connection:
            count = connection.execute(
                """SELECT COUNT(*) FROM moderation_audit
                   WHERE action = 'warning' AND outcome = 'accepted'
                     AND steam_id = ? AND target_origin = ?""",
                (steam_id, target_origin),
            ).fetchone()[0]
            rows = connection.execute(
                """SELECT audit.id, audit.reason, audit.outcome, audit.created_at,
                          admins.username AS actor
                   FROM moderation_audit AS audit
                   JOIN admins ON admins.id = audit.admin_id
                   WHERE audit.action = 'warning' AND audit.steam_id = ?
                     AND audit.target_origin = ?
                   ORDER BY audit.id DESC LIMIT ?""",
                (steam_id, target_origin, limit),
            ).fetchall()
        return count, [
            {
                "id": row["id"],
                "reason": row["reason"],
                "actor": row["actor"],
                "outcome": row["outcome"],
                "createdAt": row["created_at"].replace("+00:00", "Z"),
            }
            for row in rows
        ]

    def create_admin(self, username: str, password_hash: str, now: datetime) -> AdminAccount:
        username = username.strip()
        key = normalize_username(username)
        timestamp = _utc(now).isoformat()
        admin_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM admins LIMIT 1").fetchone() is not None:
                raise ValueError("the initial administrator already exists")
            connection.execute(
                """INSERT INTO admins
                   (id, username, username_key, password_hash, disabled, created_at, updated_at,
                    role, can_kick, can_ban)
                   VALUES (?, ?, ?, ?, 0, ?, ?, 'owner', 1, 1)""",
                (admin_id, username, key, password_hash, timestamp, timestamp),
            )
        admin = self.get_admin_by_id(admin_id)
        assert admin is not None
        return admin

    def create_subuser(
        self,
        username: str,
        password_hash: str,
        now: datetime,
        *,
        can_kick: bool = False,
        can_ban: bool = False,
        permissions: tuple[str, ...] = (),
    ) -> AdminAccount:
        username = username.strip()
        key = normalize_username(username)
        timestamp = _utc(now).isoformat()
        account_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM admins WHERE username_key = ?", (key,)
            ).fetchone() is not None:
                raise ValueError("username already exists")
            connection.execute(
                """INSERT INTO admins
                   (id, username, username_key, password_hash, disabled, created_at, updated_at,
                    role, can_kick, can_ban, permissions_json)
                   VALUES (?, ?, ?, ?, 0, ?, ?, 'subuser', ?, ?, ?)""",
                (account_id, username, key, password_hash, timestamp, timestamp,
                 int(can_kick), int(can_ban), json.dumps(permissions)),
            )
        account = self.get_admin_by_id(account_id)
        assert account is not None
        return account

    def list_subusers(self) -> list[AdminAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM admins WHERE role = 'subuser' ORDER BY created_at, username_key"
            ).fetchall()
        return [_read_admin(row) for row in rows]

    def update_subuser(
        self,
        account_id: str,
        now: datetime,
        *,
        can_kick: bool | None = None,
        can_ban: bool | None = None,
        disabled: bool | None = None,
        permissions: tuple[str, ...] | None = None,
    ) -> AdminAccount | None:
        timestamp = _utc(now).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM admins WHERE id = ? AND role = 'subuser'", (account_id,)
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """UPDATE admins SET can_kick = ?, can_ban = ?, disabled = ?,
                   permissions_json = ?, updated_at = ?
                   WHERE id = ? AND role = 'subuser'""",
                (
                    int(can_kick if can_kick is not None else row["can_kick"]),
                    int(can_ban if can_ban is not None else row["can_ban"]),
                    int(disabled if disabled is not None else row["disabled"]),
                    json.dumps(permissions) if permissions is not None else row["permissions_json"],
                    timestamp,
                    account_id,
                ),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE admin_id = ? AND revoked_at IS NULL",
                (timestamp, account_id),
            )
        return self.get_admin_by_id(account_id)

    def reset_subuser_password(
        self, account_id: str, password_hash: str, now: datetime
    ) -> AdminAccount | None:
        timestamp = _utc(now).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT 1 FROM admins WHERE id = ? AND role = 'subuser'", (account_id,)
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE admins SET password_hash = ?, updated_at = ? WHERE id = ?",
                (password_hash, timestamp, account_id),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE admin_id = ? AND revoked_at IS NULL",
                (timestamp, account_id),
            )
        return self.get_admin_by_id(account_id)

    def get_admin_by_id(self, admin_id: str) -> AdminAccount | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM admins WHERE id = ?", (admin_id,)).fetchone()
        return _read_admin(row) if row else None

    def get_admin_by_username(self, username: str) -> AdminAccount | None:
        key = username.strip().casefold()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM admins WHERE username_key = ?", (key,)).fetchone()
        return _read_admin(row) if row else None

    def disable_admin(self, admin_id: str, now: datetime | None = None) -> None:
        timestamp = _utc(now or datetime.now(timezone.utc)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE admins SET disabled = 1, updated_at = ? WHERE id = ?",
                (timestamp, admin_id),
            )
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE admin_id = ? AND revoked_at IS NULL",
                (timestamp, admin_id),
            )

    def create_session(self, admin_id: str, token_hash: str, now: datetime, expires_at: datetime) -> str:
        session_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT 1 FROM admins WHERE id = ? AND disabled = 0", (admin_id,)
            ).fetchone()
            if active is None:
                raise ValueError("administrator is unavailable")
            connection.execute(
                """INSERT INTO sessions
                   (id, admin_id, token_hash, issued_at, expires_at, revoked_at)
                   VALUES (?, ?, ?, ?, ?, NULL)""",
                (session_id, admin_id, token_hash, _utc(now).isoformat(), _utc(expires_at).isoformat()),
            )
        return session_id

    def get_active_admin_by_token_hash(self, token_hash: str, now: datetime) -> AdminAccount | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT admins.* FROM sessions
                   JOIN admins ON admins.id = sessions.admin_id
                   WHERE sessions.token_hash = ?
                     AND sessions.revoked_at IS NULL
                     AND sessions.expires_at > ?
                     AND admins.disabled = 0""",
                (token_hash, _utc(now).isoformat()),
            ).fetchone()
        return _read_admin(row) if row else None

    def revoke_session(self, token_hash: str, now: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (_utc(now).isoformat(), token_hash),
            )
