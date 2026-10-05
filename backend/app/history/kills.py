"""Authenticated game-feed records; never infer individual kills from score totals."""
from datetime import UTC, datetime, timedelta
import json
from pathlib import PurePosixPath


def map_key(value):
    return PurePosixPath((value or '').replace('\\', '/')).name.split('.')[-1].casefold()


class KillStore:
    def __init__(self, database):
        self.db = database

    def initialize(self):
        with self.db._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS kill_rounds (
                  origin TEXT NOT NULL, instance_id TEXT NOT NULL, game_match_id TEXT NOT NULL,
                  map TEXT NOT NULL, local_match_id TEXT, first_received TEXT NOT NULL,
                  last_received TEXT NOT NULL, PRIMARY KEY(origin,instance_id,game_match_id));
                CREATE TABLE IF NOT EXISTS kill_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT NOT NULL, instance_id TEXT NOT NULL,
                  game_match_id TEXT NOT NULL, event_id TEXT NOT NULL, received_at TEXT NOT NULL,
                  event_time REAL, map TEXT NOT NULL, killer_id TEXT, killer_name TEXT,
                  victim_id TEXT, victim_name TEXT, cause TEXT, distance_meters REAL, tags TEXT,
                  UNIQUE(origin,instance_id,event_id));
                CREATE INDEX IF NOT EXISTS idx_kill_events_round ON kill_events(origin,instance_id,game_match_id,id DESC);
                CREATE TABLE IF NOT EXISTS kill_alert_settings (
                  origin TEXT PRIMARY KEY, settings TEXT NOT NULL);
            ''')
            if 'victim_position' not in {r[1] for r in db.execute('PRAGMA table_info(kill_events)')}:
                db.execute('ALTER TABLE kill_events ADD COLUMN victim_position TEXT')

    @staticmethod
    def _associate(db, origin, instance, game_match, map_name, stamp):
        # RCON exposes inferred round boundaries, not the feed's authoritative matchId.
        # Leave ambiguous rounds unattached rather than merge two game matches.
        current = db.execute('SELECT id,map,last_seen,first_seen FROM observed_matches WHERE origin=? AND ended_seen IS NULL ORDER BY first_seen DESC LIMIT 1', (origin,)).fetchone()
        if not current or map_key(current['map']) != map_key(map_name):
            return None
        gap = (datetime.fromisoformat(stamp) - datetime.fromisoformat(current['last_seen'])).total_seconds()
        if gap < 0 or gap > 30:
            return None
        other = db.execute('SELECT 1 FROM kill_rounds WHERE origin=? AND local_match_id=? AND NOT(instance_id=? AND game_match_id=?)', (origin, current['id'], instance, game_match)).fetchone()
        return None if other else current['id']

    def ingest(self, origin, instance, events, at=None, *, include_inserted=False):
        stamp = (at or datetime.now(UTC)).isoformat()
        inserted = 0
        inserted_events = []
        with self.db._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for event in events:
                tags = event.get('contextTags')
                count = db.execute('''INSERT OR IGNORE INTO kill_events
                  (origin,instance_id,game_match_id,event_id,received_at,event_time,map,killer_id,killer_name,
                   victim_id,victim_name,cause,distance_meters,tags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (origin, instance, event['matchId'], event['eventId'], stamp, event.get('eventTime'), event.get('mapName', ''),
                   event.get('killerSteamId'), event.get('killerName'), event.get('victimSteamId'), event.get('victimName'),
                   event.get('cause'), event['distance'] / 100 if event.get('distance') is not None else None,
                   json.dumps(tags) if tags is not None else None)).rowcount
                if not count:
                    continue
                position = event.get('victimPositionMeters')
                if position is not None:
                    db.execute('UPDATE kill_events SET victim_position=? WHERE origin=? AND instance_id=? AND event_id=?',
                               (json.dumps([position['x'], position['y'], position['z']]), origin, instance, event['eventId']))
                inserted += count
                linked = self._associate(db, origin, instance, event['matchId'], event.get('mapName', ''), stamp)
                if include_inserted:
                    inserted_events.append({**event, 'localMatch': linked})
                db.execute('''INSERT INTO kill_rounds VALUES(?,?,?,?,?,?,?)
                  ON CONFLICT(origin,instance_id,game_match_id) DO UPDATE SET last_received=excluded.last_received,
                  local_match_id=COALESCE(kill_rounds.local_match_id,excluded.local_match_id)''',
                  (origin, instance, event['matchId'], event.get('mapName', ''), linked, stamp, stamp))
        result = {'accepted': inserted, 'duplicates': len(events) - inserted}
        if include_inserted:
            result['insertedEvents'] = inserted_events
        return result

    def register_item_round(self, origin, instance, event):
        """Associate a timestamped producer item event with the current observed round."""
        stamp = datetime.now(UTC).isoformat()
        with self.db._connect() as db:
            linked = self._associate(db, origin, instance, event['matchId'], event['mapName'], stamp)
            if linked:
                db.execute('''INSERT INTO kill_rounds VALUES(?,?,?,?,?,?,?)
                  ON CONFLICT(origin,instance_id,game_match_id) DO UPDATE SET last_received=excluded.last_received,
                  local_match_id=COALESCE(kill_rounds.local_match_id,excluded.local_match_id)''',
                  (origin, instance, event['matchId'], event['mapName'], linked, stamp, stamp))
        return linked

    def query(self, origin, match_id='current', limit=50, offset=0, game_match=None, instance=None):
        with self.db._connect() as db:
            # Feed can arrive before the first RCON snapshot. Reconcile only a
            # unique map/time candidate; never overwrite an existing association.
            pending=db.execute('SELECT * FROM kill_rounds WHERE origin=? AND local_match_id IS NULL ORDER BY last_received DESC LIMIT 100',(origin,)).fetchall()
            for feed in pending:
                first=datetime.fromisoformat(feed['first_received'])
                last=datetime.fromisoformat(feed['last_received'])
                candidates=db.execute('SELECT id,map FROM observed_matches WHERE origin=? AND first_seen<=? AND last_seen>=?',
                    (origin,(first+timedelta(seconds=30)).isoformat(),(last-timedelta(seconds=30)).isoformat())).fetchall()
                candidates=[r for r in candidates if map_key(r['map'])==map_key(feed['map'])]
                if len(candidates)!=1:continue
                mid=candidates[0]['id']
                occupied=db.execute('SELECT 1 FROM kill_rounds WHERE origin=? AND local_match_id=?',(origin,mid)).fetchone()
                if not occupied:
                    db.execute('UPDATE kill_rounds SET local_match_id=? WHERE origin=? AND instance_id=? AND game_match_id=? AND local_match_id IS NULL',
                        (mid,origin,feed['instance_id'],feed['game_match_id']))
            if match_id == 'current':
                active = db.execute('SELECT id,last_seen FROM observed_matches WHERE origin=? AND ended_seen IS NULL ORDER BY first_seen DESC LIMIT 1', (origin,)).fetchone()
                match_id = active['id'] if active and (datetime.now(UTC)-datetime.fromisoformat(active['last_seen'])).total_seconds() <= 30 else None
            where = 'e.origin=?'
            args = [origin]
            if game_match and instance:
                where += ' AND e.game_match_id=? AND e.instance_id=?'
                args.extend([game_match, instance])
            elif match_id != 'all':
                where += ' AND r.local_match_id=?'
                args.append(match_id)
            base = '''FROM kill_events e JOIN kill_rounds r ON r.origin=e.origin AND r.instance_id=e.instance_id AND r.game_match_id=e.game_match_id WHERE '''+where
            total = db.execute('SELECT COUNT(*) '+base, args).fetchone()[0]
            rows = db.execute('SELECT e.*,r.local_match_id '+base+' ORDER BY e.id DESC LIMIT ? OFFSET ?', (*args, limit, offset)).fetchall()
            last = db.execute('SELECT MAX(received_at) FROM kill_events WHERE origin=?', (origin,)).fetchone()[0]
            unlinked = db.execute('SELECT COUNT(*) FROM kill_rounds WHERE origin=? AND local_match_id IS NULL', (origin,)).fetchone()[0]
            rounds = db.execute('SELECT instance_id,game_match_id,map,local_match_id,first_received,last_received FROM kill_rounds WHERE origin=? ORDER BY last_received DESC LIMIT 100', (origin,)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item.pop('origin')
            item['tags'] = json.loads(item['tags']) if item['tags'] is not None else None
            items.append(item)
        return {'items': items, 'total': total, 'lastReceivedAt': last, 'matchId': match_id,
                'unlinkedRounds': unlinked, 'rounds': [dict(r) for r in rounds],
                'association': 'observed_map_and_round', 'scope': 'received_game_events'}

    def settings(self, origin):
        with self.db._connect() as db:
            row = db.execute('SELECT settings FROM kill_alert_settings WHERE origin=?', (origin,)).fetchone()
        return json.loads(row[0]) if row else {'windowMinutes': 10, 'minimumKills': 20, 'killsPerMinute': 5, 'headshotPercent': 90}

    def save_settings(self, origin, settings):
        with self.db._connect() as db:
            db.execute('INSERT INTO kill_alert_settings VALUES(?,?) ON CONFLICT(origin) DO UPDATE SET settings=excluded.settings', (origin, json.dumps(settings)))

    def alerts(self, origin, now=None):
        now = now or datetime.now(UTC)
        settings = self.settings(origin)
        start = (now - timedelta(minutes=settings['windowMinutes'])).isoformat()
        with self.db._connect() as db:
            rows = db.execute('''SELECT * FROM kill_events WHERE origin=? AND received_at>=?
                AND killer_id IS NOT NULL ORDER BY id LIMIT 50001''', (origin, start)).fetchall()
        groups = {}
        for row in rows[:50000]:
            tags = json.loads(row['tags']) if row['tags'] else None
            suffixes = [tag.split('.')[-1].lower() for tag in tags or []]
            if row['killer_id'] == row['victim_id'] or 'suicide' in suffixes:
                continue
            key = (row['killer_id'], row['instance_id'], row['game_match_id'])
            group = groups.setdefault(key, {'steamId': row['killer_id'], 'name': row['killer_name'], 'gameMatchId': row['game_match_id'], 'kills': 0, 'headshots': 0, 'knownTags': 0, 'times': []})
            group['kills'] += 1
            if tags is not None:
                group['knownTags'] += 1
                group['headshots'] += int('headshot' in suffixes)
            if row['event_time'] is not None:
                group['times'].append(row['event_time'])
        alerts = []
        for group in groups.values():
            times = group.pop('times')
            duration = max(times)-min(times) if len(times) == group['kills'] else None
            rate = (group['kills']-1)*60/duration if duration is not None and duration >= 60 else None
            headshot = group['headshots']*100/group['knownTags'] if group['knownTags'] else None
            reasons = []
            if group['kills'] >= settings['minimumKills'] and rate is not None and rate >= settings['killsPerMinute']:
                reasons.append('kill_rate')
            if group['knownTags'] >= settings['minimumKills'] and headshot is not None and headshot >= settings['headshotPercent']:
                reasons.append('headshot_share')
            if reasons:
                alerts.append({**group, 'killsPerMinute': round(rate, 2) if rate is not None else None,
                               'headshotPercent': round(headshot, 2) if headshot is not None else None, 'reasons': reasons})
        return {'items': alerts, 'settings': settings, 'updatedAt': now.isoformat(),
                'sampleCount': min(len(rows), 50000), 'truncated': len(rows)>50000, 'advisoryOnly': True}
