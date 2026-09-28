"""Persistent warmup policy and one-attempt reward ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.storage.db import Database


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
                    interval_mode TEXT NOT NULL CHECK(interval_mode IN ('daily','hours')),
                    interval_hours INTEGER NOT NULL,
                    notification_mode TEXT NOT NULL CHECK(notification_mode IN ('private','broadcast')),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS warmup_runs (
                    id TEXT PRIMARY KEY, origin TEXT NOT NULL, started_at TEXT NOT NULL,
                    finished_at TEXT, outcome TEXT NOT NULL, player_count INTEGER NOT NULL,
                    gift_days INTEGER NOT NULL, awarded_count INTEGER NOT NULL DEFAULT 0,
                    skipped_count INTEGER NOT NULL DEFAULT 0,
                    notification_mode TEXT NOT NULL, notification_status TEXT NOT NULL DEFAULT 'pending',
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
            """)
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
             notification_mode: str) -> dict:
        with self.db._connect() as db:
            db.execute("""UPDATE warmup_config SET origin=?,enabled=?,player_threshold=?,
                gift_days=?,interval_mode=?,interval_hours=?,notification_mode=?,updated_at=?
                WHERE id=1""", (origin, int(enabled), player_threshold, gift_days,
                interval_mode, interval_hours, notification_mode, datetime.now(UTC).isoformat()))
        return self.config()

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
              notification_mode: str, targets: list[dict], skipped_count: int) -> str:
        run_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        with self.db._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO warmup_runs
                (id,origin,started_at,outcome,player_count,gift_days,skipped_count,
                 notification_mode) VALUES (?,?,?,'attempting',?,?,?,?)""",
                (run_id, origin, now, player_count, gift_days, skipped_count,
                 notification_mode))
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
