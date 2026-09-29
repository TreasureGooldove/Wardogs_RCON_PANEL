"""Persistent warmup policy and one-attempt reward ledger."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.storage.db import Database


DEFAULT_NOTIFICATION_TEXT = "感谢您的暖服支持！您已获赠{x}天预留位。"


class WarmupStore:
    def __init__(self, database: Database) -> None:
        self.db = database

    def initialize(self) -> None:
        with self.db._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS warmup_config (
                    id INTEGER PRIMARY KEY CHECK(id=1), origin TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    player_threshold INTEGER NOT NULL, gift_days INTEGER NOT NULL,
                    reset_threshold INTEGER NOT NULL DEFAULT 10,
                    reset_minutes INTEGER NOT NULL DEFAULT 10,
                    interval_mode TEXT NOT NULL CHECK(interval_mode IN ('daily','hours')),
                    interval_hours INTEGER NOT NULL,
                    notification_mode TEXT NOT NULL CHECK(notification_mode IN ('private','broadcast')),
                    notification_text TEXT NOT NULL DEFAULT '感谢您的暖服支持！您已获赠{x}天预留位。',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS warmup_runs (
                    id TEXT PRIMARY KEY, origin TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT, outcome TEXT NOT NULL, player_count INTEGER NOT NULL,
                    gift_days INTEGER NOT NULL, awarded_count INTEGER NOT NULL DEFAULT 0,
                    skipped_count INTEGER NOT NULL DEFAULT 0,
                    notification_mode TEXT NOT NULL, notification_status TEXT NOT NULL DEFAULT 'pending',
                    notification_text TEXT NOT NULL DEFAULT '感谢您的暖服支持！您已获赠{x}天预留位。',
                    detail TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_warmup_runs_origin
                    ON warmup_runs(origin, started_at DESC);
                CREATE TABLE IF NOT EXISTS warmup_targets (
                    run_id TEXT NOT NULL REFERENCES warmup_runs(id),
                    steam_id TEXT NOT NULL, player_name TEXT NOT NULL,
                    action TEXT NOT NULL, expires_at TEXT,
                    notification_status TEXT NOT NULL DEFAULT 'pending',
                    PRIMARY KEY(run_id, steam_id)
                );
                CREATE TABLE IF NOT EXISTS warmup_cycle (
                    origin TEXT PRIMARY KEY,
                    phase TEXT NOT NULL CHECK(phase IN ('waiting_low','armed')),
                    low_since TEXT,
                    last_sample_at TEXT
                );
            """)
            for table in ("warmup_config", "warmup_runs"):
                columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                if "notification_text" not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN notification_text TEXT NOT NULL "
                               "DEFAULT '感谢您的暖服支持！您已获赠{x}天预留位。'")
            config_columns = {row["name"] for row in db.execute("PRAGMA table_info(warmup_config)")}
            if "reset_threshold" not in config_columns:
                db.execute("ALTER TABLE warmup_config ADD COLUMN reset_threshold INTEGER NOT NULL DEFAULT 10")
                db.execute("UPDATE warmup_config SET reset_threshold=max(0, CAST(player_threshold / 2 AS INTEGER))")
            if "reset_minutes" not in config_columns:
                db.execute("ALTER TABLE warmup_config ADD COLUMN reset_minutes INTEGER NOT NULL DEFAULT 10")
            db.execute("""INSERT OR IGNORE INTO warmup_config
                (id,origin,enabled,player_threshold,gift_days,interval_mode,
                 interval_hours,notification_mode,updated_at)
                VALUES (1,'',0,20,1,'daily',24,'private',?)""",
                (datetime.now(UTC).isoformat(),))
            # A restart can interrupt a config PUT. Never resume it automatically.
            db.execute("""UPDATE warmup_runs SET outcome='attention',
                detail='面板重启时写入结果未知，需人工核查'
                WHERE outcome='attempting'""")

    def config(self) -> dict:
        with self.db._connect() as db:
            row = db.execute("SELECT * FROM warmup_config WHERE id=1").fetchone()
        assert row is not None
        return {**dict(row), "enabled": bool(row["enabled"])}

    def save(self, origin: str, *, enabled: bool, player_threshold: int,
             gift_days: int, interval_mode: str, interval_hours: int,
             notification_mode: str, notification_text: str = DEFAULT_NOTIFICATION_TEXT,
             reset_threshold: int = 0, reset_minutes: int = 10) -> dict:
        with self.db._connect() as db:
            previous = db.execute("SELECT origin,enabled,player_threshold,reset_threshold,reset_minutes "
                                  "FROM warmup_config WHERE id=1").fetchone()
            db.execute("""UPDATE warmup_config SET origin=?,enabled=?,player_threshold=?,
                gift_days=?,interval_mode=?,interval_hours=?,notification_mode=?,
                notification_text=?,reset_threshold=?,reset_minutes=?,updated_at=?
                WHERE id=1""", (origin, int(enabled), player_threshold, gift_days,
                'daily', interval_hours, notification_mode, notification_text,
                reset_threshold, reset_minutes,
                datetime.now(UTC).isoformat()))
            if (previous is None or previous["origin"] != origin or
                (enabled and not previous["enabled"]) or
                previous["player_threshold"] != player_threshold or
                previous["reset_threshold"] != reset_threshold or
                previous["reset_minutes"] != reset_minutes):
                db.execute("""INSERT INTO warmup_cycle(origin,phase,low_since,last_sample_at)
                    VALUES (?,'waiting_low',NULL,NULL) ON CONFLICT(origin) DO UPDATE SET
                    phase='waiting_low',low_since=NULL,last_sample_at=NULL""", (origin,))
        return self.config()

    def cycle(self, origin: str) -> dict:
        with self.db._connect() as db:
            row = db.execute("SELECT phase,low_since,last_sample_at FROM warmup_cycle WHERE origin=?",
                             (origin,)).fetchone()
        return dict(row) if row else {"phase": "waiting_low", "low_since": None,
                                      "last_sample_at": None}

    def advance_cycle(self, origin: str, count: int, *, reset_threshold: int,
                      reset_minutes: int, player_threshold: int,
                      now: datetime, first_sample: bool = False) -> bool:
        """Consume one complete roster; true only for a fresh low-to-high crossing."""
        stamp = now.isoformat()
        with self.db._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT phase,low_since,last_sample_at FROM warmup_cycle WHERE origin=?",
                             (origin,)).fetchone()
            phase = row["phase"] if row else "waiting_low"
            low_since = row["low_since"] if row else None
            last_sample = row["last_sample_at"] if row else None
            if first_sample and count >= player_threshold:
                phase, low_since = "waiting_low", None
            elif count <= reset_threshold:
                if phase != "armed":
                    gap = (now - datetime.fromisoformat(last_sample).astimezone(UTC)
                           if last_sample else None)
                    contiguous = gap is not None and timedelta(0) <= gap <= timedelta(seconds=15)
                    if not contiguous or low_since is None:
                        low_since = stamp
                    if now - datetime.fromisoformat(low_since).astimezone(UTC) >= timedelta(minutes=reset_minutes):
                        phase, low_since = "armed", None
            elif count >= player_threshold:
                crossed = phase == "armed"
                phase, low_since = "waiting_low", None
                db.execute("""INSERT INTO warmup_cycle(origin,phase,low_since,last_sample_at)
                    VALUES (?,?,?,?) ON CONFLICT(origin) DO UPDATE SET
                    phase=excluded.phase,low_since=excluded.low_since,
                    last_sample_at=excluded.last_sample_at""",
                    (origin, phase, low_since, stamp))
                return crossed
            elif phase != "armed":
                low_since = None
            db.execute("""INSERT INTO warmup_cycle(origin,phase,low_since,last_sample_at)
                VALUES (?,?,?,?) ON CONFLICT(origin) DO UPDATE SET
                phase=excluded.phase,low_since=excluded.low_since,
                last_sample_at=excluded.last_sample_at""",
                (origin, phase, low_since, stamp))
        return False

    def latest(self, origin: str) -> dict | None:
        with self.db._connect() as db:
            row = db.execute("""SELECT * FROM warmup_runs WHERE origin=?
                ORDER BY started_at DESC LIMIT 1""", (origin,)).fetchone()
        return dict(row) if row else None

    def blocked(self, origin: str) -> bool:
        with self.db._connect() as db:
            row = db.execute("""SELECT 1 FROM warmup_runs WHERE origin=?
                AND outcome IN ('attempting','attention') LIMIT 1""", (origin,)).fetchone()
        return row is not None

    def begin(self, origin: str, player_count: int, gift_days: int,
              notification_mode: str, notification_text: str,
              targets: list[dict], skipped_count: int) -> str:
        run_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        with self.db._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO warmup_runs
                (id,origin,started_at,outcome,player_count,gift_days,skipped_count,
                 notification_mode,notification_text) VALUES (?,?,?,'attempting',?,?,?,?,?)""",
                (run_id, origin, now, player_count, gift_days, skipped_count,
                 notification_mode, notification_text))
            db.executemany("""INSERT INTO warmup_targets
                (run_id,steam_id,player_name,action,expires_at) VALUES (?,?,?,?,?)""",
                [(run_id, item["steam_id"], item["name"], item["action"],
                  item["expires_at"]) for item in targets])
        return run_id

    def finish_grant(self, run_id: str, origin: str, targets: list[dict],
                     *, outcome: str = "accepted", detail: str = "") -> None:
        now = datetime.now(UTC).isoformat()
        with self.db._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT outcome FROM warmup_runs WHERE id=? AND origin=?",
                             (run_id, origin)).fetchone()
            if row is None or row["outcome"] != "attempting":
                raise ValueError("warmup run is no longer attempting")
            for item in targets:
                db.execute("""INSERT INTO reserved_slot_metadata
                    (origin,steam_id,reason,expires_at,status,updated_at)
                    VALUES (?,?,'暖服奖励',?,'active',?)
                    ON CONFLICT(origin,steam_id) DO UPDATE SET
                    expires_at=excluded.expires_at,status='active',updated_at=excluded.updated_at""",
                    (origin, item["steam_id"], item["expires_at"], now))
            db.execute("""UPDATE warmup_runs SET outcome=?,finished_at=?,
                awarded_count=?,detail=? WHERE id=? AND outcome='attempting'""",
                (outcome, now, len(targets), detail, run_id))

    def finish_failure(self, run_id: str, *, uncertain: bool, detail: str) -> None:
        with self.db._connect() as db:
            db.execute("""UPDATE warmup_runs SET outcome=?,finished_at=?,detail=?
                WHERE id=? AND outcome='attempting'""",
                ('attention' if uncertain else 'rejected', datetime.now(UTC).isoformat(),
                 detail[:120], run_id))

    def notification(self, run_id: str, status: str, steam_id: str | None = None) -> None:
        with self.db._connect() as db:
            if steam_id is None:
                db.execute("UPDATE warmup_runs SET notification_status=? WHERE id=?",
                           (status, run_id))
            else:
                db.execute("""UPDATE warmup_targets SET notification_status=?
                    WHERE run_id=? AND steam_id=?""", (status, run_id, steam_id))

    def recent(self, origin: str, limit: int = 20) -> list[dict]:
        with self.db._connect() as db:
            rows = db.execute("""SELECT * FROM warmup_runs WHERE origin=?
                ORDER BY started_at DESC LIMIT ?""", (origin, limit)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                targets = db.execute("""SELECT steam_id,player_name,action,expires_at,
                    notification_status FROM warmup_targets WHERE run_id=?
                    ORDER BY player_name""", (item["id"],)).fetchall()
                item["targets"] = [dict(target) for target in targets]
                result.append(item)
        return result

    def acknowledge(self, origin: str, run_id: str) -> bool:
        with self.db._connect() as db:
            cursor = db.execute("""UPDATE warmup_runs SET outcome='acknowledged',
                detail='管理员已人工核查；不会补发本轮奖励'
                WHERE id=? AND origin=? AND outcome='attention'""", (run_id, origin))
            return cursor.rowcount == 1
