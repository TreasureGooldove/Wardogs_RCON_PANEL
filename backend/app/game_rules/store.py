"""Durable rule settings, evidence de-duplication and single-attempt receipts."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4

from .models import FactionSettings, ItemSettings


class GameRulesStore:
    def __init__(self, database):
        self.db = database
        with database._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS game_rule_settings (
                  origin TEXT NOT NULL, kind TEXT NOT NULL, revision TEXT NOT NULL,
                  actor TEXT NOT NULL, generation TEXT NOT NULL, enabled_at TEXT NOT NULL,
                  settings TEXT NOT NULL, blocked INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY(origin,kind));
                CREATE TABLE IF NOT EXISTS game_rule_evidence (
                  fingerprint TEXT PRIMARY KEY, origin TEXT NOT NULL, source TEXT NOT NULL,
                  instance TEXT NOT NULL, game_match TEXT NOT NULL, local_match TEXT,
                  steam_id TEXT, cause TEXT, item_id TEXT, received_at TEXT NOT NULL,
                  occurred_at TEXT);
                CREATE INDEX IF NOT EXISTS game_rule_evidence_recent ON game_rule_evidence(origin,received_at);
                CREATE TABLE IF NOT EXISTS game_rule_receipts (
                  id TEXT PRIMARY KEY, origin TEXT NOT NULL, kind TEXT NOT NULL,
                  generation TEXT NOT NULL, steam_id TEXT NOT NULL, details TEXT NOT NULL,
                  outcome TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS game_rule_receipts_recent ON game_rule_receipts(origin,kind,created_at);
            ''')
            db.execute("UPDATE game_rule_settings SET blocked=1 WHERE EXISTS (SELECT 1 FROM game_rule_receipts r WHERE r.origin=game_rule_settings.origin AND r.kind=game_rule_settings.kind AND r.generation=game_rule_settings.generation AND r.outcome='processing')")
            db.execute("UPDATE game_rule_receipts SET outcome='uncertain',error='operation_interrupted' WHERE outcome='processing'")

    def config(self, origin, kind):
        with self.db._connect() as db:
            row = db.execute('SELECT * FROM game_rule_settings WHERE origin=? AND kind=?', (origin, kind)).fetchone()
        return dict(row) if row else None

    def save(self, origin, kind, revision, actor, settings):
        with self.db._connect() as db:
            db.execute('''INSERT INTO game_rule_settings VALUES(?,?,?,?,?,?,?,0)
                ON CONFLICT(origin,kind) DO UPDATE SET revision=excluded.revision,actor=excluded.actor,
                generation=excluded.generation,enabled_at=excluded.enabled_at,settings=excluded.settings,blocked=0''',
                (origin, kind, revision, actor, str(uuid4()), datetime.now(UTC).isoformat(), json.dumps(settings)))

    def view(self, origin, kind, revision):
        config = self.config(origin, kind)
        model = FactionSettings if kind == 'factions' else ItemSettings
        settings = model.model_validate_json(config['settings']).model_dump() if config else model().model_dump()
        with self.db._connect() as db:
            receipts = [dict(r) for r in db.execute('SELECT id,steam_id,details,outcome,error,created_at FROM game_rule_receipts WHERE origin=? AND kind=? ORDER BY created_at DESC,id DESC LIMIT 100', (origin, kind))]
        for receipt in receipts:
            receipt['details'] = json.loads(receipt['details'])
        return {'settings': settings, 'active': bool(config and config['revision'] == revision and settings['enabled'] and not config['blocked']),
                'blocked': bool(config and config['blocked']), 'requiresRearm': bool(config and settings['enabled'] and config['revision'] != revision),
                'receipts': receipts}

    def evidence(self, origin, source, instance, event_id, game_match, local_match, steam_id, cause=None, item_id=None, occurred_at=None):
        # IDs remain consumed even after settings change, restart or rule disable.
        key = sha256(json.dumps([origin, instance, source, event_id]).encode()).hexdigest()
        stamp = datetime.now(UTC).isoformat()
        with self.db._connect() as db:
            inserted = db.execute('INSERT OR IGNORE INTO game_rule_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (key, origin, source, instance, game_match, local_match, steam_id, cause, item_id, stamp, occurred_at)).rowcount
        return {'fingerprint': key, 'source': source, 'localMatch': local_match, 'steamId': steam_id,
                'cause': cause, 'itemId': item_id, 'receivedAt': stamp, 'occurredAt': occurred_at} if inserted else None

    def evidence_view(self, origin):
        with self.db._connect() as db:
            causes = [dict(r) for r in db.execute('SELECT cause,COUNT(*) AS samples,MAX(received_at) AS lastReceivedAt FROM game_rule_evidence WHERE origin=? AND source=? AND cause IS NOT NULL GROUP BY cause ORDER BY lastReceivedAt DESC LIMIT 100', (origin, 'kill_cause'))]
            extension = db.execute("SELECT MAX(received_at) FROM game_rule_evidence WHERE origin=? AND source='item_used'", (origin,)).fetchone()[0]
        return {'causes': causes, 'lastItemUsedAt': extension}

    def claim(self, origin, kind, config, sid, details, cooldown, *, per_player=False):
        stamp = datetime.now(UTC)
        with self.db._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            live = db.execute('SELECT generation,blocked FROM game_rule_settings WHERE origin=? AND kind=?', (origin, kind)).fetchone()
            if not live or live['generation'] != config['generation'] or live['blocked']:
                return None
            sql = 'SELECT 1 FROM game_rule_receipts WHERE origin=? AND kind=? AND created_at>?'
            args = [origin, kind, (stamp - timedelta(seconds=cooldown)).isoformat()]
            if per_player:
                sql += ' AND steam_id=?'
                args.append(sid)
            if db.execute(sql, args).fetchone():
                return None
            key = str(uuid4())
            db.execute('INSERT INTO game_rule_receipts VALUES(?,?,?,?,?,?,?,NULL,?)',
                (key, origin, kind, config['generation'], sid, json.dumps(details), 'processing', stamp.isoformat()))
        return key

    def finish(self, key, outcome, error=None):
        with self.db._connect() as db:
            db.execute('UPDATE game_rule_receipts SET outcome=?,error=? WHERE id=?', (outcome, error, key))
            if outcome == 'uncertain':
                db.execute('''UPDATE game_rule_settings SET blocked=1 WHERE EXISTS
                    (SELECT 1 FROM game_rule_receipts r WHERE r.id=? AND r.origin=game_rule_settings.origin
                     AND r.kind=game_rule_settings.kind AND r.generation=game_rule_settings.generation)''', (key,))
