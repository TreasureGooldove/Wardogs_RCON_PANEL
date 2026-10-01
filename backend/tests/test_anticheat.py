"""Offline anti-cheat verification; no real player moderation."""
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.anticheat import AntiEngine
from app.api.feed import KillEvent
from app.config import PanelSettings, RconTarget
from app.errors import PanelError
from app.history.anticheat import AntiSettings, AntiStore
from app.main import create_app

ORIGIN = 'https://fictional-game.example.test'
PUBLIC = 'https://fictional-panel.example.test'
SID = '76561190000000001'


@pytest.fixture
def app(tmp_path):
    app = create_app(PanelSettings(tmp_path/'test.sqlite3', PUBLIC, True,
        rcon_target=RconTarget(ORIGIN, 'fictional-secret')))
    actor = app.state.auth_service.create_admin('owner', 'Fictional-password-123!')
    app.state.owner = actor
    app.state.rcon_runtime.capabilities.require_write = AsyncMock()
    app.state.rcon_runtime.capabilities.require_advertised = AsyncMock()
    app.state.rcon_runtime.client.request = AsyncMock(return_value={'players': [{'steamId': SID, 'name': 'Fictional'}]})
    app.state.rcon_runtime.client.moderate = AsyncMock()
    return app


def enable(app, action='alert', **rules):
    settings = AntiSettings(enabled=True, action=action, **rules).model_dump()
    app.state.anticheat_store.save(ORIGIN, app.state.rcon_runtime.target_revision, app.state.owner.id, settings)
    return settings


def events(app, *, position=True, distance=1000, head=True, repeated=False, prefix=''):
    batch = []
    for i in range(5):
        batch.append(KillEvent.model_validate({
            'type': 'killed', 'eventId': prefix+str(i), 'matchId': 'round', 'eventTime': 100+i,
            'killerSteamId': SID, 'victimSteamId': f'76561190000000{2 if repeated else i+2:03d}',
            'distance': distance, 'contextTags': ['Combat.Headshot'] if head else None,
            'victimPositionMeters': {'x': float(i), 'y': 0.0, 'z': 0.0} if position else None,
        }).model_dump())
    app.state.kill_store.ingest(ORIGIN, 'instance', batch)


@pytest.mark.asyncio
async def test_defaults_and_unknown_coordinates_do_not_punish(app):
    assert not app.state.anticheat_store.view(ORIGIN, app.state.rcon_runtime.target_revision)['active']
    events(app)
    await app.state.anticheat_engine.evaluate()
    app.state.rcon_runtime.client.moderate.assert_not_called()
    enable(app, 'ban', cluster={'enabled': True})
    events(app, position=False, prefix='new')
    await app.state.anticheat_engine.evaluate()
    app.state.rcon_runtime.client.moderate.assert_not_called()


@pytest.mark.asyncio
async def test_two_distances_alerts_persist_and_deduplicate(app):
    enable(app, shot={'enabled': True}, cluster={'enabled': True})
    events(app)
    await app.state.anticheat_engine.evaluate()
    await app.state.anticheat_engine.evaluate()
    view = app.state.anticheat_store.view(ORIGIN, app.state.rcon_runtime.target_revision)
    assert {f['rule'] for f in view['items']} == {'shot', 'cluster'}
    assert view['coordinateSamples'] == 5
    app.state.rcon_runtime.client.moderate.assert_not_called()


@pytest.mark.asyncio
async def test_punishment_is_once_even_after_unknown_result(app):
    enable(app, 'kick', shot={'enabled': True}, cluster={'enabled': True})
    events(app)
    app.state.rcon_runtime.client.moderate.side_effect = PanelError('action_uncertain')
    await app.state.anticheat_engine.evaluate()
    await app.state.anticheat_engine.evaluate()
    app.state.rcon_runtime.client.moderate.assert_awaited_once()
    view = app.state.anticheat_store.view(ORIGIN, app.state.rcon_runtime.target_revision)
    assert view['receipts'][0]['outcome'] == 'uncertain'


@pytest.mark.asyncio
async def test_distance_repeated_victims_and_missing_tags_fail_closed(app):
    enable(app, 'ban', shot={'enabled': True}, headshot={'enabled': True, 'players': 5})
    events(app, distance=100000, head=False, repeated=True)
    await app.state.anticheat_engine.evaluate()
    app.state.rcon_runtime.client.moderate.assert_not_called()


@pytest.mark.asyncio
async def test_revision_and_offline_player_fail_closed(app):
    enable(app, 'ban', shot={'enabled': True})
    events(app)
    app.state.rcon_runtime.client.request.return_value = {'players': []}
    await app.state.anticheat_engine.evaluate()
    app.state.rcon_runtime.client.moderate.assert_not_called()
    app.state.rcon_runtime.client.request.return_value = {'players': [{'steamId': SID, 'name': 'Fictional'}]}
    app.state.rcon_runtime.target_revision = 'changed'
    await app.state.anticheat_engine.evaluate()
    app.state.rcon_runtime.client.moderate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,distance', [('burst', 1000), ('headshot', 1000), ('longshot', 30000)])
async def test_additional_rules_and_distance_unit_boundary(app, kind, distance):
    enable(app, **{kind: {'enabled': True, 'players': 5}})
    events(app, distance=distance)
    await app.state.anticheat_engine.evaluate()
    view = app.state.anticheat_store.view(ORIGIN, app.state.rcon_runtime.target_revision)
    assert [f['rule'] for f in view['items']] == [kind]
    app.state.rcon_runtime.client.moderate.assert_not_called()


@pytest.mark.asyncio
async def test_cluster_spread_and_missing_headshot_tags(app):
    enable(app, 'ban', cluster={'enabled': True, 'distanceMeters': 1},
           headshot={'enabled': True, 'players': 5, 'headshots': 0})
    events(app, head=False)
    await app.state.anticheat_engine.evaluate()
    assert app.state.anticheat_store.view(ORIGIN, app.state.rcon_runtime.target_revision)['items'] == []
    app.state.rcon_runtime.client.moderate.assert_not_called()


@pytest.mark.asyncio
async def test_old_events_and_owner_revocation_do_not_punish(app):
    enable(app, 'ban', shot={'enabled': True})
    events(app)
    with app.state.database._connect() as db:
        db.execute('UPDATE kill_events SET received_at=?', ((datetime.now(UTC)-timedelta(seconds=320)).isoformat(),))
    await app.state.anticheat_engine.evaluate()
    assert app.state.anticheat_store.view(ORIGIN, app.state.rcon_runtime.target_revision)['items'] == []
    events(app, prefix='fresh')
    app.state.database.get_admin_by_id = lambda actor: None
    await app.state.anticheat_engine.evaluate()
    app.state.rcon_runtime.client.moderate.assert_not_called()


@pytest.mark.asyncio
async def test_restart_does_not_repeat_an_inflight_punishment(app):
    enable(app, 'kick', shot={'enabled': True})
    events(app)
    store = app.state.anticheat_store
    key = store.claim(ORIGIN, store.detect(ORIGIN, store.config(ORIGIN))[0], 'kick')
    assert key
    recovered = AntiStore(app.state.database)
    await AntiEngine(app.state.rcon_runtime, recovered).evaluate()
    assert recovered.view(ORIGIN, app.state.rcon_runtime.target_revision)['receipts'][0]['outcome'] == 'uncertain'
    app.state.rcon_runtime.client.moderate.assert_not_called()


@pytest.mark.asyncio
async def test_api_requires_owner_password_risk_and_origin(app):
    auth = app.state.auth_service
    token = auth.login('owner', 'Fictional-password-123!', 'test')[1]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC,
        headers={'Origin': PUBLIC}, cookies={'panel_session': token}) as c:
        current = (await c.get('/api/anticheat')).json()
        body = {**current['settings'], 'targetRevision': current['targetRevision'], 'enabled': True}
        assert (await c.put('/api/anticheat/settings', json=body)).status_code == 400
        body.update(acknowledgeRisk=True, password='Fictional-password-123!')
        assert (await c.put('/api/anticheat/settings', json=body)).status_code == 200
        body['targetRevision'] = 'stale'
        assert (await c.put('/api/anticheat/settings', json=body)).status_code == 409


@pytest.mark.parametrize('position', [{'x': True, 'y': 0, 'z': 0}, {'x': float('nan'), 'y': 0, 'z': 0}])
def test_invalid_coordinates_are_rejected(position):
    with pytest.raises(ValueError):
        KillEvent.model_validate({'type': 'killed', 'eventId': 'a', 'matchId': 'b', 'victimPositionMeters': position})
