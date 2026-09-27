"""SQLite history derived from fresh status and player snapshots."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from app.storage.db import Database


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _row(row: object) -> dict:
    result = dict(row)
    for key in ("experiences", "scores"):
        if key in result:
            result[key] = json.loads(result[key]) if result[key] else None
    return result


class HistoryStore:
    def __init__(self, database: Database) -> None:
        self.db = database

    def initialize(self) -> None:
        with self.db._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS observed_matches (
                    id TEXT PRIMARY KEY, origin TEXT NOT NULL, map TEXT,
                    experiences TEXT, lighting TEXT, first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL, ended_seen TEXT, end_reason TEXT,
                    scores TEXT, rotation_index INTEGER, score_tick INTEGER,
                    match_seconds INTEGER, sample_count INTEGER NOT NULL DEFAULT 1,
                    peak_players INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_observed_matches_origin_time
                    ON observed_matches(origin, first_seen DESC);
                CREATE TABLE IF NOT EXISTS observed_players (
                    match_id TEXT NOT NULL REFERENCES observed_matches(id),
                    steam_id TEXT NOT NULL, name TEXT NOT NULL, faction TEXT,
                    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                    kills INTEGER, deaths INTEGER, cash INTEGER, ping_ms INTEGER,
                    sample_count INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (match_id, steam_id)
                );
                CREATE INDEX IF NOT EXISTS idx_observed_players_steam
                    ON observed_players(steam_id, match_id);
            """)

    def record(self, origin: str, status: dict, players: dict, at: datetime | None = None) -> str:
        """Commit one fresh pair atomically. No missing or stale response reaches this method."""
        stamp = (at or datetime.now(UTC)).astimezone(UTC).isoformat()
        roster = players["players"]
        rotation = (status.get("rotation") or {}).get("nowIndex")
        tick = (status.get("scoreTick") or {}).get("current")
        seconds = status.get("matchSeconds")
        scores = status.get("factionScores")
        with self.db._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM observed_matches WHERE ended_seen IS NULL ORDER BY first_seen DESC LIMIT 1"
            ).fetchone()
            reason = None
            if previous is not None:
                gap = (datetime.fromisoformat(stamp) - datetime.fromisoformat(previous["last_seen"])).total_seconds()
                old_scores = json.loads(previous["scores"]) if previous["scores"] else None
                old_total = sum(item.get("score", 0) for item in old_scores or [])
                new_total = sum(item.get("score", 0) for item in scores or [])
                if previous["origin"] != origin:
                    reason = "target_changed"
                elif gap > 120:
                    reason = "observation_gap"
                elif previous["map"] != status.get("map") or previous["experiences"] != _json(status.get("experiences")):
                    reason = "map_changed"
                elif rotation is not None and previous["rotation_index"] is not None and rotation != previous["rotation_index"]:
                    reason = "rotation_changed"
                elif seconds is not None and previous["match_seconds"] is not None and previous["match_seconds"] - seconds > 30:
                    reason = "timer_reset"
                elif old_total >= 20 and new_total <= old_total // 3:
                    reason = "scores_reset"
                elif tick is not None and previous["score_tick"] is not None and previous["score_tick"] - tick > 10:
                    reason = "tick_reset"
            if previous is None or reason:
                if previous is not None:
                    db.execute(
                        "UPDATE observed_matches SET ended_seen = ?, end_reason = ? WHERE id = ?",
                        (stamp, reason, previous["id"]),
                    )
                match_id = str(uuid4())
                db.execute("""INSERT INTO observed_matches
                    (id,origin,map,experiences,lighting,first_seen,last_seen,scores,
                     rotation_index,score_tick,match_seconds,peak_players)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (match_id, origin, status.get("map"), _json(status.get("experiences")),
                     status.get("lighting"), stamp, stamp, _json(scores), rotation,
                     tick, seconds, len(roster)),
                )
            else:
                match_id = previous["id"]
                db.execute("""UPDATE observed_matches SET last_seen = ?, scores = ?,
                    rotation_index = ?, score_tick = ?, match_seconds = ?,
                    sample_count = sample_count + 1,
                    peak_players = MAX(peak_players, ?) WHERE id = ?""",
                    (stamp, _json(scores), rotation, tick, seconds, len(roster), match_id),
                )
            for player in roster:
                steam_id = player.get("steamId")
                if not steam_id:
                    continue
                db.execute("""INSERT INTO observed_players
                    (match_id,steam_id,name,faction,first_seen,last_seen,kills,deaths,cash,ping_ms)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(match_id,steam_id) DO UPDATE SET
                    name=excluded.name, faction=excluded.faction, last_seen=excluded.last_seen,
                    kills=excluded.kills, deaths=excluded.deaths, cash=excluded.cash,
                    ping_ms=excluded.ping_ms, sample_count=sample_count+1""",
                    (match_id, steam_id, player["name"], player.get("faction"), stamp, stamp,
                     player.get("kills"), player.get("deaths"), player.get("cash"), player.get("pingMs")),
                )
        return match_id

    def matches(self, origin: str, limit: int, offset: int) -> dict:
        with self.db._connect() as db:
            total = db.execute("SELECT COUNT(*) FROM observed_matches WHERE origin=?", (origin,)).fetchone()[0]
            rows = db.execute("""SELECT m.*, (SELECT COUNT(*) FROM observed_players p
                WHERE p.match_id=m.id) AS player_count FROM observed_matches m
                WHERE m.origin=? ORDER BY m.first_seen DESC LIMIT ? OFFSET ?""",
                (origin, limit, offset)).fetchall()
        return {"total": total, "items": [_row(row) for row in rows]}

    def match(self, origin: str, match_id: str) -> dict | None:
        with self.db._connect() as db:
            row = db.execute("SELECT * FROM observed_matches WHERE origin=? AND id=?", (origin, match_id)).fetchone()
            if row is None:
                return None
            players = db.execute("""SELECT steam_id,name,faction,first_seen,last_seen,
                kills,deaths,cash,ping_ms,sample_count FROM observed_players
                WHERE match_id=? ORDER BY faction, name COLLATE NOCASE""", (match_id,)).fetchall()
        return {**_row(row), "players": [dict(player) for player in players]}

    def players(self, origin: str, search: str, limit: int, offset: int) -> dict:
        escaped = search.replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        base = """FROM observed_players p JOIN observed_matches m ON m.id=p.match_id
            WHERE m.origin=? AND (p.steam_id LIKE ? ESCAPE '\\' OR p.name LIKE ? ESCAPE '\\')"""
        args = (origin, pattern, pattern)
        with self.db._connect() as db:
            total = db.execute("SELECT COUNT(DISTINCT p.steam_id) " + base, args).fetchone()[0]
            rows = db.execute("""SELECT p.steam_id, MAX(p.name) AS name,
                MIN(p.first_seen) AS first_seen, MAX(p.last_seen) AS last_seen,
                COUNT(DISTINCT p.match_id) AS match_count """ + base +
                " GROUP BY p.steam_id ORDER BY last_seen DESC LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()
        return {"total": total, "items": [dict(row) for row in rows]}

    def player(self, origin: str, steam_id: str) -> dict | None:
        with self.db._connect() as db:
            rows = db.execute("""SELECT p.*, m.map, m.first_seen AS match_first_seen,
                m.last_seen AS match_last_seen, m.ended_seen, m.end_reason
                FROM observed_players p JOIN observed_matches m ON m.id=p.match_id
                WHERE m.origin=? AND p.steam_id=? ORDER BY m.first_seen DESC LIMIT 100""",
                (origin, steam_id)).fetchall()
        if not rows:
            return None
        return {"steamId": steam_id, "name": rows[0]["name"],
                "matches": [dict(row) for row in rows]}
