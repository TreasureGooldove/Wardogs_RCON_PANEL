"""Experimental evidence rules. Unknown data never implies cheating."""
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from math import dist

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class Rule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool = False
    seconds: int = Field(default=10, ge=1, le=300)
    distanceMeters: float = Field(default=30, ge=0, le=10000, allow_inf_nan=False)
    players: int = Field(default=5, ge=2, le=100)
    headshots: int = Field(default=3, ge=0, le=100)
    headshotPercent: float = Field(default=90, ge=0, le=100, allow_inf_nan=False)

    @model_validator(mode='after')
    def coherent(self):
        if self.headshots > self.players:
            raise ValueError('headshots must not exceed players')
        return self


class AntiSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool = False
    action: str = Field(default='alert', pattern=r'^(alert|kick|ban)$')
    shot: Rule = Field(default_factory=Rule)
    cluster: Rule = Field(default_factory=Rule)
    burst: Rule = Field(default_factory=Rule)
    headshot: Rule = Field(default_factory=lambda: Rule(seconds=60, players=20, headshots=0))
    longshot: Rule = Field(default_factory=lambda: Rule(distanceMeters=300))


class AntiStore:
    def __init__(self, database):
        self.db = database
        with database._connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS anticheat_settings (
              origin TEXT PRIMARY KEY, revision TEXT NOT NULL, actor TEXT NOT NULL,
              enabled_at TEXT NOT NULL, settings TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS anticheat_receipts (
              fingerprint TEXT PRIMARY KEY, origin TEXT NOT NULL, player TEXT NOT NULL,
              action TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL,
              created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS anticheat_findings (
              fingerprint TEXT PRIMARY KEY, origin TEXT NOT NULL, evidence TEXT NOT NULL,
              created_at TEXT NOT NULL);
            ''')
        # A restart must never repeat an in-flight punishment.
        with database._connect() as db:
            db.execute("UPDATE anticheat_receipts SET outcome='uncertain' WHERE outcome='processing'")

    def config(self, origin):
        with self.db._connect() as db:
            row = db.execute('SELECT * FROM anticheat_settings WHERE origin=?', (origin,)).fetchone()
        return dict(row) if row else None

    def save(self, origin, revision, actor, settings):
        with self.db._connect() as db:
            db.execute('INSERT INTO anticheat_settings VALUES(?,?,?,?,?) ON CONFLICT(origin) DO UPDATE SET revision=excluded.revision,actor=excluded.actor,enabled_at=excluded.enabled_at,settings=excluded.settings',
                       (origin, revision, actor, datetime.now(UTC).isoformat(), json.dumps(settings)))

    def view(self, origin, revision):
        config = self.config(origin)
        settings = AntiSettings.model_validate(json.loads(config['settings'])).model_dump() if config else AntiSettings().model_dump()
        active = bool(config and config['revision'] == revision and settings['enabled'])
        with self.db._connect() as db:
            receipts = [dict(r) for r in db.execute('SELECT * FROM anticheat_receipts WHERE origin=? ORDER BY created_at DESC LIMIT 100', (origin,))]
            findings = [json.loads(r[0]) for r in db.execute('SELECT evidence FROM anticheat_findings WHERE origin=? ORDER BY created_at DESC LIMIT 100', (origin,))]
            samples = db.execute('SELECT COUNT(*),COUNT(victim_position) FROM kill_events WHERE origin=?', (origin,)).fetchone()
        return {'settings': settings, 'active': active, 'items': findings,
                'coordinateSamples': samples[1], 'eventSamples': samples[0],
                'receipts': receipts, 'coordinateContract': 'victimPositionMeters: {x,y,z}; world coordinates in meters',
                'advisory': 'thresholds are suspicion signals, not proof of cheating'}

    def detect(self, origin, config):
        settings = AntiSettings.model_validate(json.loads(config['settings'])).model_dump()
        if not settings['enabled']:
            return []
        since = max(config['enabled_at'], (datetime.now(UTC) - timedelta(seconds=315)).isoformat())
        with self.db._connect() as db:
            rows = db.execute('SELECT * FROM kill_events WHERE origin=? AND received_at>=? ORDER BY id DESC LIMIT 5001',
                              (origin, since)).fetchall()
        if len(rows) > 5000:
            return []  # Truncated evidence is never sufficient for punishment.
        groups = defaultdict(list)
        for row in rows:
            if not row['killer_id'] or not row['victim_id'] or row['killer_id'] == row['victim_id'] or row['event_time'] is None:
                continue
            tags = json.loads(row['tags']) if row['tags'] else None
            if tags and 'suicide' in [t.split('.')[-1].lower() for t in tags]:
                continue
            groups[(row['instance_id'], row['game_match_id'], row['killer_id'])].append(dict(row))
        findings = []
        for (instance, match, sid), events in groups.items():
            events.sort(key=lambda e: e['event_time'])
            # Delayed or old batches must not punish an unrelated current player.
            if (datetime.now(UTC) - datetime.fromisoformat(events[-1]['received_at'])).total_seconds() > 15:
                continue
            for kind in ('shot', 'cluster', 'burst', 'headshot', 'longshot'):
                rule = settings[kind]
                if not rule['enabled']:
                    continue
                end = events[-1]['event_time']
                window = [e for e in events if end - rule['seconds'] <= e['event_time'] <= end]
                if kind in ('shot', 'longshot'):
                    window = [e for e in window if e['distance_meters'] is not None and
                              (e['distance_meters'] <= rule['distanceMeters'] if kind == 'shot' else e['distance_meters'] >= rule['distanceMeters'])]
                if kind == 'cluster':
                    positioned = [e for e in window if e.get('victim_position')]
                    if len(positioned) > 100:
                        continue  # Bound spatial comparisons; fail closed under overload.
                    # All pairwise victim distances <= X: not an approximate centre radius.
                    candidates = []
                    for anchor in positioned:
                        cluster = [anchor]
                        for e in positioned:
                            if e is anchor:
                                continue
                            if all(dist(json.loads(e['victim_position']), json.loads(p['victim_position'])) <= rule['distanceMeters'] for p in cluster):
                                cluster.append(e)
                        candidates.append(cluster)
                    window = max(candidates, key=len, default=[])
                # Count different victims once; repeated spawn kills cannot inflate evidence.
                unique = {e['victim_id']: e for e in window}
                window = list(unique.values())
                heads = sum('headshot' in [t.split('.')[-1].lower() for t in json.loads(e['tags']) or []] for e in window if e['tags'])
                known = sum(e['tags'] is not None for e in window)
                if len(window) < rule['players'] or heads < rule['headshots']:
                    continue
                if kind == 'headshot' and (known != len(window) or heads * 100 / known < rule['headshotPercent']):
                    continue
                ids = sorted(e['event_id'] for e in window)
                fingerprint = sha256(json.dumps([origin, instance, match, sid, kind, ids]).encode()).hexdigest()
                findings.append({'fingerprint': fingerprint, 'steamId': sid, 'name': events[-1]['killer_name'],
                                 'rule': kind, 'players': len(window), 'headshots': heads, 'seconds': rule['seconds'],
                                 'distanceMeters': rule['distanceMeters'], 'headshotPercent': heads * 100 / known if known else None,
                                 'eventIds': ids, 'instanceId': instance, 'matchId': match})
        return findings[:100]

    def record(self, origin, findings):
        with self.db._connect() as db:
            db.executemany('INSERT OR IGNORE INTO anticheat_findings VALUES(?,?,?,?)',
                [(f['fingerprint'], origin, json.dumps(f), datetime.now(UTC).isoformat()) for f in findings])

    def claim(self, origin, finding, action):
        with self.db._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Once per player per round, across overlapping rules and process restarts.
            key = sha256(json.dumps([origin, finding['instanceId'], finding['matchId'], finding['steamId']]).encode()).hexdigest()
            count = db.execute('INSERT OR IGNORE INTO anticheat_receipts VALUES(?,?,?,?,?,?,?)',
                               (key, origin, finding['steamId'], action, 'processing', json.dumps(finding), datetime.now(UTC).isoformat())).rowcount
        return key if count else None

    def finish(self, key, outcome):
        with self.db._connect() as db:
            db.execute('UPDATE anticheat_receipts SET outcome=? WHERE fingerprint=?', (outcome, key))
