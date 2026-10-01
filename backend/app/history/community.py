"""Observed player sessions, annotations and durable private-message receipts."""
from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import uuid4

from app.errors import PanelError


class CommunityStore:
    def __init__(self, database):
        self.db = database

    def initialize(self):
        with self.db._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS player_sessions (
                  id TEXT PRIMARY KEY, origin TEXT NOT NULL, steam_id TEXT NOT NULL,
                  name TEXT NOT NULL, started_at TEXT NOT NULL, last_seen TEXT NOT NULL,
                  ended_at TEXT, observed_seconds REAL NOT NULL DEFAULT 0,
                  join_observed INTEGER NOT NULL DEFAULT 0, end_reason TEXT);
                CREATE INDEX IF NOT EXISTS idx_player_sessions_target
                  ON player_sessions(origin, steam_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS player_annotations (
                  origin TEXT NOT NULL, steam_id TEXT NOT NULL, note TEXT NOT NULL,
                  watched INTEGER NOT NULL DEFAULT 0, updated_by TEXT NOT NULL,
                  updated_at TEXT NOT NULL, PRIMARY KEY(origin, steam_id));
                CREATE TABLE IF NOT EXISTS session_samples (
                  origin TEXT PRIMARY KEY, last_seen TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS player_aliases (
                  origin TEXT NOT NULL, steam_id TEXT NOT NULL, name TEXT NOT NULL,
                  first_seen TEXT NOT NULL,last_seen TEXT NOT NULL, PRIMARY KEY(origin,steam_id,name));
                CREATE TABLE IF NOT EXISTS private_batches (
                  id TEXT PRIMARY KEY, actor TEXT NOT NULL, origin TEXT NOT NULL,
                  revision TEXT NOT NULL, fingerprint TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS private_deliveries (
                  batch_id TEXT NOT NULL REFERENCES private_batches(id),
                  steam_id TEXT NOT NULL, name TEXT NOT NULL, message TEXT NOT NULL,
                  outcome TEXT NOT NULL DEFAULT 'queued', error TEXT, updated_at TEXT NOT NULL,
                  PRIMARY KEY(batch_id, steam_id));
            ''')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observed_players'").fetchone():
                db.execute('''INSERT OR IGNORE INTO player_aliases
                SELECT m.origin,p.steam_id,p.name,MIN(p.first_seen),MAX(p.last_seen)
                FROM observed_players p JOIN observed_matches m ON m.id=p.match_id
                GROUP BY m.origin,p.steam_id,p.name''')
            # A command in flight at a crash may have reached the game. Never replay it.
            db.execute("UPDATE private_deliveries SET outcome='uncertain',error='interrupted' WHERE outcome='processing'")
            db.execute("UPDATE private_deliveries SET outcome='skipped',error='interrupted' WHERE outcome='queued'")

    @staticmethod
    def observe(db, origin, players, stamp, trust_gap=30):
        sample = db.execute('SELECT last_seen FROM session_samples WHERE origin=?', (origin,)).fetchone()
        gap = (datetime.fromisoformat(stamp) - datetime.fromisoformat(sample['last_seen'])).total_seconds() if sample else None
        continuous = gap is not None and 0 <= gap <= trust_gap
        roster = {p['steamId']: p for p in players if p.get('steamId')}
        active = db.execute('SELECT * FROM player_sessions WHERE origin=? AND ended_at IS NULL', (origin,)).fetchall()
        existing = {}
        for row in active:
            if not continuous or row['steam_id'] not in roster:
                db.execute('UPDATE player_sessions SET ended_at=last_seen,end_reason=? WHERE id=?',
                           ('left' if continuous else 'observation_gap', row['id']))
            else:
                existing[row['steam_id']] = row
        for sid, player in roster.items():
            db.execute('''INSERT INTO player_aliases VALUES(?,?,?,?,?)
                ON CONFLICT(origin,steam_id,name) DO UPDATE SET last_seen=excluded.last_seen''',
                (origin,sid,player['name'],stamp,stamp))
            old = existing.get(sid)
            if old:
                seconds = max(0, (datetime.fromisoformat(stamp) - datetime.fromisoformat(old['last_seen'])).total_seconds())
                db.execute('UPDATE player_sessions SET name=?,last_seen=?,observed_seconds=observed_seconds+? WHERE id=?',
                           (player['name'], stamp, seconds, old['id']))
            else:
                db.execute('INSERT INTO player_sessions(id,origin,steam_id,name,started_at,last_seen,join_observed) VALUES(?,?,?,?,?,?,?)',
                           (str(uuid4()), origin, sid, player['name'], stamp, stamp, int(continuous)))
        db.execute('INSERT INTO session_samples VALUES(?,?) ON CONFLICT(origin) DO UPDATE SET last_seen=excluded.last_seen', (origin, stamp))

    def dossier(self, origin, sid):
        with self.db._connect() as db:
            aliases = db.execute('''SELECT name,first_seen,last_seen FROM player_aliases
                WHERE origin=? AND steam_id=? ORDER BY last_seen DESC LIMIT 100''', (origin, sid)).fetchall()
            sessions = db.execute('SELECT * FROM player_sessions WHERE origin=? AND steam_id=? ORDER BY started_at DESC LIMIT 100', (origin, sid)).fetchall()
            seconds = db.execute('SELECT SUM(observed_seconds) FROM player_sessions WHERE origin=? AND steam_id=?', (origin, sid)).fetchone()[0]
            annotation = db.execute('SELECT note,watched,updated_by,updated_at FROM player_annotations WHERE origin=? AND steam_id=?', (origin, sid)).fetchone()
            actions = db.execute('''SELECT a.action,a.outcome,a.reason,a.created_at,a.request_id,u.username actor
                FROM moderation_audit a JOIN admins u ON u.id=a.admin_id
                WHERE a.target_origin=? AND a.steam_id=? ORDER BY a.id DESC LIMIT 100''', (origin, sid)).fetchall()
        return {'steamId': sid, 'aliases': [dict(r) for r in aliases],
                'sessions': [dict(r) for r in sessions], 'observedPlaytimeSeconds': seconds,
                'annotation': dict(annotation) if annotation else {'note': '', 'watched': False},
                'actions': [dict(r) for r in actions], 'scope': 'panel_observations'}

    def annotate(self, origin, sid, note, watched, actor):
        stamp = datetime.now(UTC).isoformat()
        with self.db._connect() as db:
            db.execute('''INSERT INTO player_annotations VALUES(?,?,?,?,?,?)
                ON CONFLICT(origin,steam_id) DO UPDATE SET note=excluded.note,watched=excluded.watched,
                updated_by=excluded.updated_by,updated_at=excluded.updated_at''', (origin, sid, note, int(watched), actor, stamp))

    def batch(self, batch_id, actor, origin, revision, roster, message):
        fingerprint = sha256(json.dumps([origin, revision, sorted(p['steamId'] for p in roster), message], ensure_ascii=False).encode()).hexdigest()
        stamp = datetime.now(UTC).isoformat()
        with self.db._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM private_batches WHERE id=?', (batch_id,)).fetchone()
            if old:
                if old['actor'] != actor or old['fingerprint'] != fingerprint:
                    raise PanelError('bot_request_conflict')
                return False
            busy = db.execute("SELECT COUNT(*) FROM private_deliveries d JOIN private_batches b ON b.id=d.batch_id WHERE b.origin=? AND d.outcome IN ('queued','processing')", (origin,)).fetchone()[0]
            if busy:
                raise PanelError('rate_limited')
            db.execute('INSERT INTO private_batches VALUES(?,?,?,?,?,?)', (batch_id, actor, origin, revision, fingerprint, stamp))
            db.executemany('INSERT INTO private_deliveries(batch_id,steam_id,name,message,updated_at) VALUES(?,?,?,?,?)',
                           [(batch_id, p['steamId'], p['name'], message, stamp) for p in roster])
            return True

    def batch_status(self, batch_id, origin):
        with self.db._connect() as db:
            row = db.execute('SELECT id,actor,revision,created_at FROM private_batches WHERE id=? AND origin=?', (batch_id, origin)).fetchone()
            if row is None:
                return None
            deliveries = db.execute('SELECT steam_id,name,outcome,error,updated_at FROM private_deliveries WHERE batch_id=? ORDER BY steam_id', (batch_id,)).fetchall()
        return {**dict(row), 'items': [dict(r) for r in deliveries],
                'complete': all(r['outcome'] not in ('queued', 'processing') for r in deliveries)}

    def outcome(self, batch_id, sid, outcome, error=None):
        with self.db._connect() as db:
            db.execute('UPDATE private_deliveries SET outcome=?,error=?,updated_at=? WHERE batch_id=? AND steam_id=?',
                       (outcome, error, datetime.now(UTC).isoformat(), batch_id, sid))

    def preview_sessions(self, origin, start, end):
        with self.db._connect() as db:
            rows = db.execute('SELECT * FROM player_sessions WHERE origin=? AND started_at>=? AND started_at<=? ORDER BY started_at,id LIMIT 10001', (origin, start, end)).fetchall()
        return [dict(r) for r in rows]
