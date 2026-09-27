"""Persistent configuration and one-attempt delivery ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.storage.db import Database


DEFAULT_FIRST = (
    "禁挂、禁BUG（如悍马丢物）、禁恶意报点。\n"
    "查实外挂永Ban 发现使用BUG第一次警告第二次永ban"
)
DEFAULT_SECOND = (
    "压家限制，为使用反载武器限制（白圈内及外扩1.5km内误差100米）实际地图会有变动，禁止压家为最高优先级：\n"
    "仅限区域内使用：防空密集阵、阵地防空弹、反载地雷、火箭筒、C4/IED、单兵便携防空。\n"
    "圈外禁止攻击无武装车辆（带武器的车均为武装车辆）\n"
    "武装车辆在圈外1.5km开始允许开火（自行火炮、迫击炮含魔鬼鱼不受限制），禁止压家。（允许自行火炮使用单兵防空自卫，具体视情况而定）\n"
    "违规：首犯警告，再犯Ban 7天。"
)


class RulesStore:
    def __init__(self, database: Database) -> None:
        self.db = database

    def initialize(self) -> None:
        with self.db._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rules_config (
                    id INTEGER PRIMARY KEY CHECK(id=1), origin TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    first_text TEXT NOT NULL, second_text TEXT NOT NULL,
                    delay_seconds INTEGER NOT NULL, gap_seconds INTEGER NOT NULL,
                    cooldown_minutes INTEGER NOT NULL, max_per_round INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rules_deliveries (
                    id TEXT PRIMARY KEY, origin TEXT NOT NULL, steam_id TEXT NOT NULL,
                    player_name TEXT NOT NULL, batch_id TEXT NOT NULL,
                    stage INTEGER NOT NULL, part INTEGER NOT NULL,
                    outcome TEXT NOT NULL, created_at TEXT NOT NULL,
                    finished_at TEXT, error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_rules_delivery_cooldown
                    ON rules_deliveries(origin, steam_id, stage, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_rules_delivery_recent
                    ON rules_deliveries(origin, created_at DESC);
            """)
            db.execute("""INSERT OR IGNORE INTO rules_config
                (id,origin,enabled,first_text,second_text,delay_seconds,gap_seconds,
                 cooldown_minutes,max_per_round,updated_at)
                VALUES (1,'',0,?,?,?,?,?,?,?)""",
                (DEFAULT_FIRST, DEFAULT_SECOND, 60, 10, 30, 3, datetime.now(UTC).isoformat()),
            )

    def config(self) -> dict:
        with self.db._connect() as db:
            row = db.execute("SELECT * FROM rules_config WHERE id=1").fetchone()
        assert row is not None
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        return result

    def save(self, origin: str, *, enabled: bool, first_text: str, second_text: str,
             delay_seconds: int, gap_seconds: int, cooldown_minutes: int,
             max_per_round: int) -> dict:
        with self.db._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE rules_config SET origin=?,enabled=?,first_text=?,
                second_text=?,delay_seconds=?,gap_seconds=?,cooldown_minutes=?,
                max_per_round=?,updated_at=? WHERE id=1""",
                (origin, int(enabled), first_text, second_text, delay_seconds,
                 gap_seconds, cooldown_minutes, max_per_round, datetime.now(UTC).isoformat()),
            )
        return self.config()

    def recent_first_attempt(self, origin: str, steam_id: str) -> datetime | None:
        with self.db._connect() as db:
            row = db.execute("""SELECT created_at FROM rules_deliveries
                WHERE origin=? AND steam_id=? AND stage=1
                ORDER BY created_at DESC LIMIT 1""", (origin, steam_id)).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def begin(self, origin: str, steam_id: str, name: str, batch_id: str,
              stage: int, part: int) -> str:
        delivery_id = str(uuid4())
        with self.db._connect() as db:
            db.execute("""INSERT INTO rules_deliveries
                (id,origin,steam_id,player_name,batch_id,stage,part,outcome,created_at)
                VALUES (?,?,?,?,?,?,?,'attempting',?)""",
                (delivery_id, origin, steam_id, name, batch_id, stage, part,
                 datetime.now(UTC).isoformat()),
            )
        return delivery_id

    def finish(self, delivery_id: str, outcome: str, error_code: str | None = None) -> None:
        with self.db._connect() as db:
            db.execute("""UPDATE rules_deliveries SET outcome=?,finished_at=?,error_code=?
                WHERE id=?""", (outcome, datetime.now(UTC).isoformat(), error_code, delivery_id))

    def recent(self, origin: str, limit: int = 50) -> list[dict]:
        with self.db._connect() as db:
            rows = db.execute("""SELECT id,steam_id,player_name,stage,part,outcome,
                created_at,finished_at,error_code FROM rules_deliveries
                WHERE origin=? ORDER BY created_at DESC LIMIT ?""", (origin, limit)).fetchall()
        return [dict(row) for row in rows]
