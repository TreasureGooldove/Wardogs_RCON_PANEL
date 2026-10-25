"""Owner-confirmed, durable one-shot team shuffles; interrupted jobs never resume."""
import asyncio
from datetime import UTC, datetime, timedelta
import json
from secrets import SystemRandom
from uuid import uuid4

from app.errors import PanelError
from app.rcon.actions import ActionService, valid_steam_id
from app.rcon.players import normalize_players
from app.rcon.routes import RouteName, WriteName, write_route_for
from app.rcon.status import normalize_status
from .models import FACTIONS, FactionSettings, faction_name

PAUSE_SECONDS = 0.5


def roster_state(players):
    result = {}
    for player in players:
        try:
            sid = valid_steam_id(player.get('steamId'))
        except PanelError:
            raise PanelError('invalid_upstream') from None
        team = faction_name(player.get('faction'))
        if team is None or sid in result:
            raise PanelError('invalid_upstream')
        result[sid] = team
    return result


def shuffle_plan(players, settings):
    current = roster_state(players)
    limits = settings.limits.model_dump()
    opened = [name for name in FACTIONS if limits[name] != 0]
    if len(opened) != 2 or not 2 <= len(current) <= 200:
        raise PanelError('shuffle_two_factions')
    first, second = opened
    n = len(current)
    low = max(0, n - (limits[second] if limits[second] > 0 else n))
    high = min(n, limits[first] if limits[first] > 0 else n)
    if low > high:
        raise PanelError('shuffle_capacity')
    random = SystemRandom()
    size = random.choice(list({max(low, min(high, n // 2)), max(low, min(high, (n + 1) // 2))}))
    ids = list(current)
    random.shuffle(ids)
    targets = {sid: first if index < size else second for index, sid in enumerate(ids)}
    # Avoid a no-op when an already-balanced roster happens to draw itself.
    if all(current[sid] == target for sid, target in targets.items()) and 0 < size < n:
        targets[ids[0]], targets[ids[-1]] = targets[ids[-1]], targets[ids[0]]
    names = {p['steamId']: p.get('name') or p['steamId'] for p in players}
    return [{'steamId': sid, 'name': names[sid], 'from': current[sid], 'to': targets[sid],
             'outcome': 'unchanged' if current[sid] == targets[sid] else 'queued', 'error': None} for sid in ids]


class ShuffleStore:
    def __init__(self, database):
        self.db = database
        with database._connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS faction_shuffles (
                id TEXT PRIMARY KEY, origin TEXT NOT NULL, actor TEXT NOT NULL,
                revision TEXT NOT NULL, generation TEXT NOT NULL, phase TEXT NOT NULL,
                expires TEXT NOT NULL, plan TEXT NOT NULL, context TEXT NOT NULL, error TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS faction_shuffle_running
                ON faction_shuffles(origin) WHERE phase='running';''')
            for row in db.execute("SELECT id,plan FROM faction_shuffles WHERE phase='running'").fetchall():
                plan = json.loads(row['plan'])
                for player in plan:
                    if player['outcome'] in ('queued', 'processing'):
                        player['outcome'] = 'uncertain' if player['outcome'] == 'processing' else 'skipped'
                        player['error'] = 'interrupted'
                db.execute("UPDATE faction_shuffles SET phase='uncertain',plan=?,error='interrupted' WHERE id=?",
                           (json.dumps(plan), row['id']))

    def active(self, origin):
        with self.db._connect() as db:
            return db.execute("SELECT 1 FROM faction_shuffles WHERE origin=? AND phase='running'", (origin,)).fetchone() is not None

    def latest(self, origin):
        with self.db._connect() as db:
            row = db.execute("SELECT id FROM faction_shuffles WHERE origin=? ORDER BY (phase='running') DESC,rowid DESC LIMIT 1", (origin,)).fetchone()
        return self.public(self.get(row['id'], origin)) if row else None

    def get(self, key, origin):
        with self.db._connect() as db:
            row = db.execute('SELECT * FROM faction_shuffles WHERE id=? AND origin=?', (key, origin)).fetchone()
        if not row:
            raise PanelError('shuffle_stale')
        result = dict(row)
        result['plan'], result['context'] = json.loads(result['plan']), json.loads(result['context'])
        return result

    def preview(self, origin, actor, revision, generation, plan, context):
        key = str(uuid4())
        expires = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
        with self.db._connect() as db:
            db.execute("DELETE FROM faction_shuffles WHERE phase='preview' AND expires<?", (datetime.now(UTC).isoformat(),))
            db.execute('INSERT INTO faction_shuffles VALUES(?,?,?,?,?,?,?,?,?,NULL)',
                       (key, origin, actor, revision, generation, 'preview', expires, json.dumps(plan), json.dumps(context)))
        return self.get(key, origin)

    def start(self, job):
        with self.db._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM faction_shuffles WHERE origin=? AND phase='running'", (job['origin'],)).fetchone():
                raise PanelError('shuffle_busy')
            return db.execute("UPDATE faction_shuffles SET phase='running' WHERE id=? AND phase='preview'", (job['id'],)).rowcount == 1

    def update(self, job):
        with self.db._connect() as db:
            db.execute('UPDATE faction_shuffles SET phase=?,plan=?,error=? WHERE id=?',
                       (job['phase'], json.dumps(job['plan']), job['error'], job['id']))

    @staticmethod
    def public(job):
        return {key: job[key] for key in ('id', 'phase', 'expires', 'plan', 'error')}


async def fresh_sample(runtime):
    runtime.ensure_running()
    for method, path in (('GET', '/v1/players'), ('GET', '/v1/status')):
        await runtime.capabilities.require_advertised(method, path)
    status = normalize_status(await runtime.client.request(RouteName.STATUS))
    players = normalize_players(await runtime.client.request(RouteName.PLAYERS))['players']
    return players, status


def context_for(status):
    return {'map': status.get('map'), 'experiences': status.get('experiences'),
            'matchSeconds': status.get('matchSeconds')}


def verify_context(original, status):
    if not original['map'] or original['map'] != status.get('map') or original['experiences'] != status.get('experiences'):
        raise PanelError('shuffle_stale')
    before, now = original.get('matchSeconds'), status.get('matchSeconds')
    if before is not None and (now is None or now < before):
        raise PanelError('shuffle_stale')


async def deliver_shuffle(app, key, origin):
    store, runtime = app.state.shuffle_store, app.state.rcon_runtime
    job = store.get(key, origin)
    expected = {p['steamId']: p['from'] for p in job['plan']}
    try:
        for player in job['plan']:
            if player['outcome'] != 'queued':
                continue
            async with runtime.lock:
                runtime.ensure_running()
                actor = app.state.database.get_admin_by_id(job['actor'])
                if actor is None or actor.disabled or actor.role != 'owner':
                    raise PanelError('permission_denied')
                if runtime.target is None or runtime.target.origin != origin or runtime.target_revision != job['revision']:
                    raise PanelError('stale_server_target')
                rules = app.state.game_rules_store.config(origin, 'factions')
                if not rules or rules['generation'] != job['generation']:
                    raise PanelError('shuffle_stale')
                players, status = await fresh_sample(runtime)
                verify_context(job['context'], status)
                if roster_state(players) != expected:
                    raise PanelError('shuffle_stale')
                names = {faction_name(row['name']): row['name'] for row in status.get('factionScores') or []}
                if player['to'] not in names:
                    raise PanelError('invalid_upstream')
                spec = write_route_for(WriteName.CHANGE_FACTION)
                await runtime.capabilities.require_advertised(spec.method, spec.path)
                runtime.ensure_running()
                player['outcome'] = 'processing'
                store.update(job)  # Durable single-attempt claim before the write.
                try:
                    await ActionService(runtime.client).send(WriteName.CHANGE_FACTION,
                        steam_id=player['steamId'], faction=names[player['to']])
                except PanelError as exc:
                    player['outcome'] = 'uncertain' if exc.code == 'action_uncertain' else 'rejected'
                    player['error'] = exc.code
                    raise
                player['outcome'] = 'accepted'
                expected[player['steamId']] = player['to']
                store.update(job)
                app.state.database.append_moderation_audit(job['actor'], 'shuffleFaction', player['steamId'],
                    'accepted', json.dumps({'from': player['from'], 'to': player['to']}), key, job['revision'], origin)
                runtime.invalidate_players_unlocked()
                runtime.read_service.invalidate(RouteName.STATUS)
            await asyncio.sleep(PAUSE_SECONDS)
        # Acknowledgement is not proof the game applied every transfer.
        async with runtime.lock:
            runtime.ensure_running()
            if runtime.target is None or runtime.target.origin != origin or runtime.target_revision != job['revision']:
                raise PanelError('stale_server_target')
            players, status = await fresh_sample(runtime)
            verify_context(job['context'], status)
            rules = app.state.game_rules_store.config(origin, 'factions')
            if runtime.target_revision != job['revision'] or not rules or rules['generation'] != job['generation'] or roster_state(players) != expected:
                raise PanelError('shuffle_stale')
        job['phase'] = 'completed'
    except BaseException as exc:
        job['phase'] = 'uncertain' if any(p['outcome'] in ('accepted', 'processing', 'uncertain') for p in job['plan']) else 'stopped'
        job['error'] = exc.code if isinstance(exc, PanelError) else 'interrupted'
        for player in job['plan']:
            if player['outcome'] in ('queued', 'processing'):
                player['outcome'] = 'uncertain' if player['outcome'] == 'processing' else 'skipped'
                player['error'] = job['error']
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
    finally:
        store.update(job)
        app.state.database.append_moderation_audit(job['actor'], 'shuffleFactions', '',
            'accepted' if job['phase'] == 'completed' else 'uncertain' if job['phase'] == 'uncertain' else 'rejected',
            job['error'] or '', key, job['revision'], origin)
        app.state.game_rules_engine.reset()
