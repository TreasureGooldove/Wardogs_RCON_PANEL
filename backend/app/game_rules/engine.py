"""Single-attempt actions on fresh observations; never edit ServerSettings.ini."""
from datetime import UTC, datetime
import json
import logging
from secrets import choice
from time import monotonic

from app.errors import PanelError
from app.history.kills import map_key
from app.rcon.actions import ActionService
from app.rcon.players import normalize_players
from app.rcon.routes import RouteName, WriteName, write_route_for
from .models import FACTIONS, FactionSettings, ItemSettings, faction_name

_LOG = logging.getLogger(__name__)


def faction_plan(players, settings):
    """Choose a transfer that improves capacity excess, then population balance."""
    counts = dict.fromkeys(FACTIONS, 0)
    for player in players:
        name = faction_name(player.get('faction'))
        if name is None:
            return counts, None, 'unknown_faction'
        counts[name] += 1
    if len(players) < settings.minimumPlayers:
        return counts, None, 'below_minimum'
    limits = settings.limits.model_dump()
    excess = sum(max(0, counts[name] - limits[name]) for name in FACTIONS if limits[name])
    imbalance = max(counts.values()) - min(counts.values())
    if not excess and (not settings.balanceEnabled or imbalance <= settings.maxDifference):
        return counts, None, 'within_limits'
    def score(values):
        return (sum(max(0, values[name] - limits[name]) for name in FACTIONS if limits[name]),
                sum(value * value for value in values.values()) if settings.balanceEnabled else 0)
    choices = []
    for source in FACTIONS:
        for target in FACTIONS:
            if source == target or not counts[source] or (limits[target] and counts[target] >= limits[target]):
                continue
            if not excess and counts[source] - counts[target] <= settings.maxDifference:
                continue
            changed = {**counts, source: counts[source] - 1, target: counts[target] + 1}
            if score(changed) < score(counts):
                choices.append((score(changed), counts[target], source, target))
    if not choices:
        return counts, None, 'no_destination'
    best_rank = min((item[0], item[1]) for item in choices)
    best = choice([item for item in choices if (item[0], item[1]) == best_rank])
    return counts, (best[2], best[3]), 'over_limit' if excess else 'unbalanced'


class GameRulesEngine:
    def __init__(self, runtime, store):
        self.runtime, self.store = runtime, store
        self.last_factions = {'counts': dict.fromkeys(FACTIONS, 0), 'state': 'no_samples', 'observedAt': None}
        self.observed_revision = None
        self._generation = None
        self._violation = None
        self._since = 0.0
        self._last_sample = 0.0
        self._next_factions = 0.0
        self._joined = {}
        self._paused = set()

    def reset(self):
        self._generation = self._violation = None
        self._since = self._last_sample = 0.0
        self._next_factions = 0.0
        self._joined = {}
        self.observed_revision = None
        self.last_factions = {'counts': dict.fromkeys(FACTIONS, 0), 'state': 'no_samples', 'observedAt': None}

    def ready(self, kind):
        runtime = self.runtime
        if runtime.target is None:
            return None
        config = self.store.config(runtime.target.origin, kind)
        if not config or config['blocked'] or (kind, config['generation']) in self._paused or config['revision'] != runtime.target_revision:
            return None
        if not json.loads(config['settings'])['enabled']:
            return None
        actor = self.store.db.get_admin_by_id(config['actor'])
        return config if actor and not actor.disabled and actor.role == 'owner' else None

    async def action(self, kind, config, sid, details, cooldown, action, faction=None):
        runtime = self.runtime
        origin = runtime.target.origin
        # Write permission, actor state and target revision checked immediately before claim.
        if self.ready(kind) is None:
            return
        spec = write_route_for(action)
        await runtime.capabilities.require_advertised(spec.method, spec.path)
        key = self.store.claim(origin, kind, config, sid, details, cooldown, per_player=kind == 'items')
        if not key:
            return
        outcome, error = 'uncertain', None
        try:
            await ActionService(runtime.client).send(action, steam_id=sid, faction=faction)
            outcome = 'accepted'
            runtime.invalidate_players_unlocked()
            runtime.read_service.invalidate(RouteName.STATUS)
        except PanelError as exc:
            outcome = 'uncertain' if exc.code == 'action_uncertain' else 'rejected'
            error = exc.code
        except Exception:
            error = 'internal_error'
        finally:
            # A crash or failed audit must leave a claim; it must never resend the action.
            try:
                if outcome == 'uncertain':
                    self._paused.add((kind, config['generation']))
                self.store.finish(key, outcome, error)
                self.store.db.append_moderation_audit(config['actor'], 'gameRule_' + action.value, sid, outcome,
                    json.dumps(details, ensure_ascii=False), key, runtime.target_revision, origin)
            except Exception:
                self._paused.add((kind, config['generation']))
                try:
                    self.store.finish(key, 'uncertain', 'audit_completion_failed')
                except Exception:
                    pass  # The durable claim and in-process pause still prevent another attempt.
                _LOG.error('Game rule audit completion failed: kind=%s', kind)

    async def factions_unlocked(self, players, status):
        """Collector calls with its lock held and a newly successful player sample."""
        runtime = self.runtime
        config = self.ready('factions')
        clock = monotonic()
        if self._generation != (config or {}).get('generation'):
            self._next_factions = 0.
        elif config and clock < self._next_factions:
            return
        self._next_factions = clock + 5.
        settings = FactionSettings.model_validate_json(config['settings']) if config else FactionSettings()
        counts, plan, state = faction_plan(players, settings)
        self.last_factions = {'counts': counts, 'state': state if config else 'disabled',
                              'observedAt': datetime.now(UTC).isoformat()}
        self.observed_revision = runtime.target_revision
        active_ids = {p.get('steamId') for p in players if p.get('steamId')}
        if self._generation != (config or {}).get('generation'):
            self._violation = None
            self._joined = {}
            self._generation = (config or {}).get('generation')
        self._joined = {sid: at for sid, at in self._joined.items() if sid in active_ids}
        for sid in sorted(active_ids):
            self._joined.setdefault(sid, clock)
        # Observation gaps reset the continuous-violation grace period.
        if clock - self._last_sample > max(5., runtime.cadence[0]) * 2 + 1:
            self._violation = None
        self._last_sample = clock
        if config is None or plan is None:
            self._violation = None
            return
        names = {faction_name(row.get('name')): row.get('name') for row in status.get('factionScores') or []}
        if set(names) != set(FACTIONS):
            self.last_factions['state'] = 'unknown_faction'
            self._violation = None
            return
        # A random tie-break for the destination must not reset a source's grace period.
        if self._violation is None or self._violation[0] != plan[0]:
            self._violation, self._since = plan, clock
        if clock - self._since < settings.stableSeconds:
            self.last_factions['state'] = 'waiting_stability'
            return
        source, destination = plan
        candidates = [p for p in players if p.get('steamId') and faction_name(p.get('faction')) == source]
        if not candidates:
            self.last_factions['state'] = 'no_actionable_player'
            return
        # Prefer most recently observed arrivals; existing ties use SteamID deterministically.
        player = max(candidates, key=lambda p: (self._joined[p['steamId']], p['steamId']))
        try:
            await self.action('factions', config, player['steamId'], {'from': source, 'to': destination, 'counts': counts, 'reason': state},
                settings.cooldownSeconds, WriteName.CHANGE_FACTION, names[destination])
        except PanelError as exc:
            self.last_factions['state'] = exc.code
        self._violation = None

    async def items_unlocked(self, evidence):
        """Only the just-ingested, de-duplicated evidence can trigger a kill."""
        config = self.ready('items')
        if config is None or not evidence:
            return
        settings = ItemSettings.model_validate_json(config['settings'])
        runtime = self.runtime
        clock = datetime.now(UTC)
        origin = runtime.target.origin
        with self.store.db._connect() as db:
            current = db.execute('SELECT id,map,last_seen FROM observed_matches WHERE origin=? AND ended_seen IS NULL ORDER BY first_seen DESC LIMIT 1', (origin,)).fetchone()
        if not current or not 0 <= (clock - datetime.fromisoformat(current['last_seen'])).total_seconds() <= 15:
            return
        selected = {item.itemId: item for item in settings.items}
        aliases = {cause.casefold(): item.itemId for item in settings.items for cause in item.killCauses}
        matches = []
        for item in evidence:
            if item['localMatch'] != current['id'] or not item['steamId']:
                continue
            received = datetime.fromisoformat(item['receivedAt'])
            if received < datetime.fromisoformat(config['enabled_at']) or not 0 <= (clock - received).total_seconds() <= 10:
                continue
            if item['occurredAt']:
                happened = datetime.fromisoformat(item['occurredAt'])
                if happened < datetime.fromisoformat(config['enabled_at']) or not -3 <= (clock - happened).total_seconds() <= 10:
                    continue
            item_id = item['itemId'] if item['source'] == 'item_used' else aliases.get((item['cause'] or '').strip().casefold())
            if item_id in selected:
                matches.append((item, item_id))
        if not matches:
            return
        try:
            spec = write_route_for(WriteName.KILL)
            await runtime.capabilities.require_advertised(spec.method, spec.path)
            await runtime.capabilities.require_advertised('GET', '/v1/players')
            await runtime.capabilities.require_advertised('GET', '/v1/status')
            # Explicitly verify fresh map and online target; no stale cache may authorize punishment.
            from app.rcon.status import normalize_status
            status = normalize_status(await runtime.client.request(RouteName.STATUS))
            if not map_key(status.get('map')) or not map_key(current['map']) or map_key(status.get('map')) != map_key(current['map']):
                return
            roster = normalize_players(await runtime.client.request(RouteName.PLAYERS))['players']
        except PanelError:
            return
        online = {p['steamId'] for p in roster if p['steamId']}
        # Also cap work in one feed batch; do not turn an incoming batch into hundreds of writes.
        handled = set()
        for item, item_id in matches:
            sid = item['steamId']
            if sid not in online or sid in handled or len(handled) >= 10:
                continue
            # Freshness is checked again after network reads / earlier actions.
            if (datetime.now(UTC) - datetime.fromisoformat(item['receivedAt'])).total_seconds() > 10:
                continue
            handled.add(sid)
            details = {'itemId': item_id, 'cause': item['cause'], 'source': item['source'], 'evidence': item['fingerprint']}
            try:
                await self.action('items', config, sid, details, settings.cooldownSeconds, WriteName.KILL)
            except PanelError:
                return
