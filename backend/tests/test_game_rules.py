"""Offline rule execution and safety boundaries; all RCON actions are mocked."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.config import PanelSettings, RconTarget
from app.errors import PanelError
from app.game_rules.engine import faction_plan
from app.game_rules.models import FactionSettings, ItemSettings, ItemUsedEvent
from app.game_rules.store import GameRulesStore
from app.history.collector import HistoryCollector
from app.main import create_app
from app.rcon.actions import ActionService
from app.rcon.routes import RouteName, WriteName

ORIGIN = 'https://fictional-game.example.test'
PUBLIC = 'https://fictional-panel.example.test'
SID = '76561190000000001'
PASSWORD = 'Fictional-password-123!'
STATUS = {'map': 'TestMap', 'experiences': [], 'playerCount': 5,
          'factionScores': [{'name': name, 'score': 0} for name in ('Lonestar', 'Valkyra', 'Manticore')]}


def roster(counts=(3, 1, 1)):
    rows = []
    for team, count in zip(('BLU', 'RED', 'GRN'), counts):
        for _ in range(count):
            rows.append({'steamId': f'765611900000000{len(rows)+1:02d}', 'name': 'Example', 'faction': team,
                         'kills': 0, 'deaths': 0, 'cash': 100, 'pingMs': 50})
    return rows


@pytest.fixture
def panel(tmp_path, monkeypatch):
    app = create_app(PanelSettings(tmp_path/'panel.sqlite3', PUBLIC, True, history_enabled=True,
        rcon_target=RconTarget(ORIGIN, 'fictional-secret'), feed_token='fictional-feed-token-that-is-long-enough', feed_origin=ORIGIN))
    app.state.owner = app.state.auth_service.create_admin('owner', PASSWORD)
    runtime = app.state.rcon_runtime
    runtime.capabilities.require_write = AsyncMock()
    runtime.capabilities.require_advertised = AsyncMock()
    runtime.capabilities.require = AsyncMock()
    async def read(route):
        if route is RouteName.STATUS: return STATUS
        if route is RouteName.PLAYERS: return {'players': roster()}
        raise PanelError('route_unsupported')
    runtime.client.request = AsyncMock(side_effect=read)
    app.state.send = AsyncMock()
    monkeypatch.setattr(ActionService, 'send', app.state.send)
    app.state.history_store.record(ORIGIN, STATUS, {'players': roster()})
    return app


def enable(panel, kind, **values):
    model = FactionSettings if kind == 'factions' else ItemSettings
    settings = model(enabled=True, **values)
    panel.state.game_rules_store.save(ORIGIN, kind, panel.state.rcon_runtime.target_revision,
                                     panel.state.owner.id, settings.model_dump())
    return settings


def kill_event(event_id='example-kill', cause='M4', **values):
    return {'type': 'killed', 'eventId': event_id, 'matchId': 'game-round-1', 'mapName': 'TestMap',
            'eventTime': 20, 'killerSteamId': SID, 'victimSteamId': '76561190000000002', 'cause': cause, **values}


async def push(panel, events):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=panel), base_url=PUBLIC) as c:
        return await c.post('/api/ingest/events', headers={'Authorization': 'Bearer fictional-feed-token-that-is-long-enough'},
                            json={'serverId': 'game-instance-1', 'events': events})


def test_faction_capacity_difference_and_no_destination():
    counts, plan, reason = faction_plan(roster(), FactionSettings(maxDifference=1))
    assert counts == {'Lonestar': 3, 'Valkyra': 1, 'Manticore': 1}
    assert plan[0] == 'Lonestar' and plan[1] in ('Valkyra', 'Manticore')
    assert reason == 'unbalanced'
    _, plan, _ = faction_plan(roster(), FactionSettings(balanceEnabled=False, limits={'Lonestar': 2}))
    assert plan[0] == 'Lonestar'
    _, plan, reason = faction_plan(roster(), FactionSettings(limits={'Lonestar': 2, 'Valkyra': 1, 'Manticore': 1}))
    assert plan is None and reason == 'no_destination'
    _, plan, reason = faction_plan(roster(), FactionSettings(minimumPlayers=20))
    assert plan is None and reason == 'below_minimum'
    unknown = roster() + [{'faction': None, 'steamId': None}]
    assert faction_plan(unknown, FactionSettings())[1:] == (None, 'unknown_faction')


@pytest.mark.asyncio
async def test_collector_passes_shared_fresh_samples_without_extra_polling(panel, monkeypatch):
    clock = [100.]
    monkeypatch.setattr('app.history.collector.monotonic', lambda: clock[0])
    runtime = panel.state.rcon_runtime
    runtime.watch_until = 1000.
    rules = AsyncMock()
    # Reset is synchronous; evaluation is asynchronous.
    rules.reset = lambda: None
    collector = HistoryCollector(runtime, panel.state.history_store, game_rules=rules)
    await collector.sample(scheduled=True)
    rules.factions_unlocked.assert_awaited_once_with(collector.players['players'], collector.status)
    assert collector.status['map'] == 'TestMap' and collector.players['players'] == roster()
    reads = runtime.client.request.await_count
    clock[0] = 100.5
    assert not await collector.sample(scheduled=True)
    assert runtime.client.request.await_count == reads
    clock[0] = 101.
    await collector.sample(scheduled=True)
    assert rules.factions_unlocked.await_count == 2
    assert runtime.client.request.await_count == reads + 1
    # A successful roster cannot authorize a transfer using stale status.
    collector.status_due = 10000.
    clock[0] = 120.
    await collector.sample(scheduled=True)
    assert rules.factions_unlocked.await_count == 2


@pytest.mark.asyncio
async def test_faction_single_transfer_cooldown_and_permission(panel):
    enable(panel, 'factions', maxDifference=1, stableSeconds=0)
    engine = panel.state.game_rules_engine
    await engine.factions_unlocked(roster(), STATUS)
    assert panel.state.send.await_count == 1
    assert panel.state.send.await_args.args == (WriteName.CHANGE_FACTION,)
    assert panel.state.send.await_args.kwargs['faction'] in ('Valkyra', 'Manticore')
    await engine.factions_unlocked(roster(), STATUS)
    assert panel.state.send.await_count == 1
    enable(panel, 'factions', maxDifference=1, stableSeconds=0)
    panel.state.database.get_admin_by_id = lambda _: None
    await engine.factions_unlocked(roster(), STATUS)
    assert panel.state.send.await_count == 1


@pytest.mark.asyncio
async def test_faction_stability_and_disabled_defaults(panel):
    engine = panel.state.game_rules_engine
    await engine.factions_unlocked(roster(), STATUS)
    panel.state.send.assert_not_called()
    enable(panel, 'factions', maxDifference=1, stableSeconds=10)
    await engine.factions_unlocked(roster(), STATUS)
    assert engine.last_factions['state'] == 'waiting_stability'
    panel.state.send.assert_not_called()
    # An interruption must reset the continuous grace period.
    engine._last_sample -= 100
    engine._since -= 100
    await engine.factions_unlocked(roster(), STATUS)
    panel.state.send.assert_not_called()


@pytest.mark.asyncio
async def test_exact_weapon_match_duplicate_and_cooldown(panel):
    enable(panel, 'items', items=[{'itemId': 'm4', 'killCauses': ['M4']}])
    assert (await push(panel, [kill_event(cause='M4A1')])).status_code == 200
    panel.state.send.assert_not_called()
    assert (await push(panel, [kill_event('exact', cause=' m4 ')])).status_code == 200
    assert panel.state.send.await_count == 1
    assert panel.state.send.await_args.args == (WriteName.KILL,)
    assert panel.state.send.await_args.kwargs == {'steam_id': SID, 'faction': None}
    again = await push(panel, [kill_event('exact')])
    assert again.json()['duplicates'] == 1
    await push(panel, [kill_event('another')])
    assert panel.state.send.await_count == 1


@pytest.mark.asyncio
async def test_no_retroactive_offline_old_round_or_suicide_kill(panel):
    await push(panel, [kill_event('before-enable')])
    enable(panel, 'items', items=[{'itemId': 'm4', 'killCauses': ['M4']}])
    await push(panel, [kill_event('before-enable')])
    await push(panel, [kill_event('self', victimSteamId=SID)])
    await push(panel, [kill_event('suicide', contextTags=['Death.Suicide'])])
    await push(panel, [kill_event('old-map', mapName='OldMap')])
    panel.state.send.assert_not_called()
    runtime = panel.state.rcon_runtime
    runtime.client.request = AsyncMock(side_effect=lambda route: STATUS if route is RouteName.STATUS else {'players': []})
    await push(panel, [kill_event('offline')])
    panel.state.send.assert_not_called()


@pytest.mark.asyncio
async def test_uncertain_result_pauses_all_item_actions_and_survives_restart(panel):
    enable(panel, 'items', items=[{'itemId': 'm4', 'killCauses': ['M4']}])
    panel.state.send.side_effect = PanelError('action_uncertain')
    await push(panel, [kill_event('timeout')])
    await push(panel, [kill_event('after-timeout')])
    assert panel.state.send.await_count == 1
    view = panel.state.game_rules_store.view(ORIGIN, 'items', panel.state.rcon_runtime.target_revision)
    assert view['blocked'] and view['receipts'][0]['outcome'] == 'uncertain'
    recovered = GameRulesStore(panel.state.database)
    assert recovered.view(ORIGIN, 'items', panel.state.rcon_runtime.target_revision)['blocked']


@pytest.mark.asyncio
async def test_interrupted_claim_is_not_retried(panel):
    enable(panel, 'items', items=[{'itemId': 'm4'}])
    store = panel.state.game_rules_store
    config = store.config(ORIGIN, 'items')
    assert store.claim(ORIGIN, 'items', config, SID, {'itemId': 'm4'}, 30, per_player=True)
    recovered = GameRulesStore(panel.state.database)
    result = recovered.view(ORIGIN, 'items', panel.state.rcon_runtime.target_revision)
    assert result['blocked'] and result['receipts'][0]['error'] == 'operation_interrupted'


@pytest.mark.asyncio
async def test_item_used_requires_valid_fresh_timestamp_and_catalog(panel):
    equipment = next(i for i in __import__('app.game_rules.models', fromlist=['CATALOG']).CATALOG['items'] if i['kind'] == 'equipment')
    enable(panel, 'items', items=[{'itemId': equipment['id']}])
    event = {'type': 'itemUsed', 'eventId': 'use-item-1', 'matchId': 'game-round-1', 'mapName': 'TestMap',
             'steamId': SID, 'itemId': equipment['id'], 'itemKind': 'equipment', 'occurredAt': datetime.now(UTC).isoformat()}
    old = {**event, 'eventId': 'old-item', 'occurredAt': (datetime.now(UTC)-timedelta(minutes=2)).isoformat()}
    assert (await push(panel, [old])).json()['itemUsesAccepted'] == 1
    panel.state.send.assert_not_called()
    assert (await push(panel, [{**event, 'itemId': 'unknown'}])).status_code == 400
    assert (await push(panel, [{**event, 'occurredAt': '2026-01-01T00:00:00'}])).status_code == 400
    assert (await push(panel, [{**event, 'occurredAt': datetime.now(UTC).timestamp()}])).status_code == 400
    result = await push(panel, [event])
    assert result.json()['itemUsesAccepted'] == 1
    assert panel.state.send.await_count == 1
    repeated = await push(panel, [event])
    assert repeated.json()['itemUsesDuplicates'] == 1
    assert panel.state.send.await_count == 1


@pytest.mark.asyncio
async def test_missing_live_map_and_stale_wall_time_do_not_authorize_kill(panel):
    enable(panel, 'items', items=[{'itemId': 'm4', 'killCauses': ['M4']}])
    await push(panel, [kill_event('stale-wall-time', occurredAt=(datetime.now(UTC)-timedelta(minutes=2)).isoformat())])
    panel.state.send.assert_not_called()
    assert (await push(panel, [kill_event('numeric-time', occurredAt=datetime.now(UTC).timestamp())])).status_code == 400
    runtime = panel.state.rcon_runtime
    runtime.client.request = AsyncMock(side_effect=lambda route: {**STATUS, 'map': ''} if route is RouteName.STATUS else {'players': roster()})
    await push(panel, [kill_event('missing-live-map')])
    panel.state.send.assert_not_called()


@pytest.mark.asyncio
async def test_rule_api_owner_password_origin_revision_and_read_only(panel):
    token = panel.state.auth_service.login('owner', PASSWORD, 'test')[1]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=panel), base_url=PUBLIC, headers={'Origin': PUBLIC},
                                cookies={'panel_session': token}) as c:
        initial = (await c.get('/api/game-rules')).json()
        assert not initial['factions']['settings']['enabled'] and not initial['items']['settings']['enabled']
        assert initial['equipmentInventoryAvailable'] is False
        body = {**initial['factions']['settings'], 'enabled': True, 'targetRevision': initial['targetRevision']}
        assert (await c.put('/api/game-rules/factions', json=body)).status_code == 400
        body.update(password=PASSWORD, acknowledgeRisk=True)
        assert (await c.put('/api/game-rules/factions', json=body)).status_code == 200
        assert (await c.put('/api/game-rules/factions', json={**body, 'targetRevision': 'stale'})).status_code == 409
        assert (await c.put('/api/game-rules/factions', json=body, headers={'Origin': 'https://other.example.test'})).status_code == 401
    panel.state.auth_service.create_subuser('viewer', PASSWORD)
    viewer_token = panel.state.auth_service.login('viewer', PASSWORD, 'viewer')[1]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=panel), base_url=PUBLIC, headers={'Origin': PUBLIC},
                                cookies={'panel_session': viewer_token}) as c:
        assert (await c.get('/api/game-rules')).status_code == 200
        assert (await c.put('/api/game-rules/factions', json=body)).status_code == 403
    panel.state.send.assert_not_called()


@pytest.mark.asyncio
async def test_server_switch_and_unsupported_capability_do_not_act(panel):
    enable(panel, 'items', items=[{'itemId': 'm4', 'killCauses': ['M4']}])
    panel.state.rcon_runtime.capabilities.require_write.side_effect = PanelError('action_unsupported')
    await push(panel, [kill_event('unsupported')])
    panel.state.send.assert_not_called()
    panel.state.rcon_runtime.capabilities.require_write.side_effect = None
    panel.state.rcon_runtime.target_revision = 'different-server-revision'
    await push(panel, [kill_event('stale-config')])
    panel.state.send.assert_not_called()


def test_invalid_rules_do_not_silently_coerce_or_guess():
    for values in ({'limits': {'Lonestar': True}}, {'maxDifference': 0}, {'cooldownSeconds': 1}):
        with pytest.raises(ValidationError): FactionSettings(**values)
    with pytest.raises(ValidationError): ItemSettings(enabled=True)
    with pytest.raises(ValidationError): ItemSettings(items=[{'itemId': 'm4', 'killCauses': ['M4']}, {'itemId': 'sks', 'killCauses': ['m4']}])
    with pytest.raises(ValidationError): ItemUsedEvent(type='itemUsed', eventId='test', matchId='round', mapName='map', steamId=SID,
        itemId='m4', itemKind='equipment', occurredAt=datetime.now(UTC))
