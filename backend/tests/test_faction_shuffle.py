"""All transfers here use an in-memory roster and mocked RCON writes."""
import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.test_game_rules import panel, enable, roster as sample_roster, PUBLIC, PASSWORD, ORIGIN, STATUS
from app.errors import PanelError
from app.game_rules.models import FactionSettings
from app.game_rules.shuffle import ShuffleStore, shuffle_plan, deliver_shuffle, context_for
from app.rcon.routes import RouteName

LIMITS = {'Lonestar': 0, 'Valkyra': 50, 'Manticore': 50}


def roster(counts=(3, 1, 1)):
    players = sample_roster(counts)
    for index, player in enumerate(players):
        player['steamId'] = str(76561190000000000 + index + 1)
    return players


@pytest.mark.parametrize('counts', [(0,50,50), (0,49,50), (3,1,1), (0,2,0)])
def test_random_plan_is_balanced_unique_and_respects_closed_team(counts):
    players = roster(counts)
    before = [dict(p) for p in players]
    for _ in range(10):
        plan = shuffle_plan(players, FactionSettings(limits=LIMITS))
        assert len({p['steamId'] for p in plan}) == len(players)
        assert all(p['to'] != 'Lonestar' for p in plan)
        assert abs(sum(p['to']=='Valkyra' for p in plan)-sum(p['to']=='Manticore' for p in plan)) <= 1
        assert any(p['outcome']=='queued' for p in plan)
    assert players == before


def test_capacity_unlimited_and_invalid_roster():
    plan = shuffle_plan(roster((0, 5, 5)), FactionSettings(limits={**LIMITS, 'Valkyra': 2, 'Manticore': -1}))
    assert sum(p['to']=='Valkyra' for p in plan)==2
    for players, settings, code in [
        (roster((0,51,50)), FactionSettings(limits=LIMITS), 'shuffle_capacity'),
        (roster(), FactionSettings(), 'shuffle_two_factions'),
        (roster((0,0,1)), FactionSettings(limits=LIMITS), 'shuffle_two_factions'),
        (roster()+[roster()[0]], FactionSettings(limits=LIMITS), 'invalid_upstream'),
        ([{'steamId':'76561190000000001','faction':None}], FactionSettings(limits=LIMITS), 'invalid_upstream')]:
        with pytest.raises(PanelError, match=code): shuffle_plan(players, settings)


def setup_job(panel, monkeypatch):
    enable(panel, 'factions', limits=LIMITS)
    monkeypatch.setattr('app.game_rules.shuffle.PAUSE_SECONDS', 0)
    players = roster((0, 3, 3))
    runtime = panel.state.rcon_runtime
    async def read(route):
        if route is RouteName.PLAYERS: return {'players':players}
        if route is RouteName.STATUS: return {**STATUS, 'playerCount':len(players)}
        raise AssertionError(route)
    runtime.client.request = AsyncMock(side_effect=read)
    async def send(action, *, steam_id, faction):
        next(p for p in players if p['steamId']==steam_id)['faction']=faction
    panel.state.send.side_effect = send
    return players


async def login(panel):
    token = panel.state.auth_service.login('owner', PASSWORD, 'test')[1]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=panel), base_url=PUBLIC,
                             headers={'Origin':PUBLIC}, cookies={'panel_session':token})


async def drain(panel):
    tasks=list(panel.state.community_tasks)
    if tasks: await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_preview_confirm_idempotence_live_verification_and_audit(panel, monkeypatch):
    setup_job(panel,monkeypatch)
    async with await login(panel) as client:
        revision=panel.state.rcon_runtime.target_revision
        response=await client.post('/api/game-rules/factions/shuffle/preview',json={'targetRevision':revision})
        assert response.status_code==200
        job=response.json()
        assert job['phase']=='preview' and response.headers['cache-control']=='no-store'
        assert panel.state.send.await_count==0
        url='/api/game-rules/factions/shuffle/'+job['id']+'/execute'
        body={'targetRevision':revision,'acknowledgeRisk':True,'password':PASSWORD}
        assert (await client.post(url,json={**body,'password':'wrong'})).status_code==401
        assert (await client.post(url,json={**body,'acknowledgeRisk':False})).status_code==400
        assert panel.state.send.await_count==0
        assert (await client.post(url,json=body)).status_code==202
        await drain(panel)
        result=(await client.get('/api/game-rules/factions/shuffle/'+job['id'])).json()
        assert result['phase']=='completed'
        sent=panel.state.send.await_count
        assert sent==sum(p['outcome']=='accepted' for p in result['plan']) and sent>0
        assert (await client.post(url,json=body)).status_code==202
        await drain(panel)
        assert panel.state.send.await_count==sent
        assert (await client.get('/api/game-rules')).json()['latestShuffle']['id']==job['id']
        with panel.state.database._connect() as db:
            assert db.execute("SELECT count(*) FROM moderation_audit WHERE action='shuffleFaction'").fetchone()[0]==sent


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['roster','rules','expired','revision','origin'])
async def test_stale_preview_and_origin_rejected_before_writes(panel, monkeypatch, change):
    players=setup_job(panel,monkeypatch)
    async with await login(panel) as client:
        revision=panel.state.rcon_runtime.target_revision
        job=(await client.post('/api/game-rules/factions/shuffle/preview',json={'targetRevision':revision})).json()
        if change=='roster': players.pop()
        if change=='rules': enable(panel,'factions',limits=LIMITS)
        if change=='expired':
            with panel.state.database._connect() as db:
                db.execute('UPDATE faction_shuffles SET expires=?',((datetime.now(UTC)-timedelta(seconds=1)).isoformat(),))
        if change=='revision': panel.state.rcon_runtime.target_revision='changed'
        response=await client.post('/api/game-rules/factions/shuffle/'+job['id']+'/execute',
            json={'targetRevision':revision,'acknowledgeRisk':True,'password':PASSWORD},
            headers={'Origin':'https://other.example.test'} if change=='origin' else {})
        assert response.status_code in (401,403,409)
        assert panel.state.send.await_count==0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['timeout','departed','stop','rule_change'])
async def test_partial_failure_halts_remaining_and_never_replays(panel, monkeypatch, failure):
    players=setup_job(panel,monkeypatch)
    async with await login(panel) as client:
        revision=panel.state.rcon_runtime.target_revision
        job=(await client.post('/api/game-rules/factions/shuffle/preview',json={'targetRevision':revision})).json()
        calls=[]
        async def send(action, *, steam_id, faction):
            calls.append(steam_id)
            if failure=='timeout': raise PanelError('action_uncertain')
            next(p for p in players if p['steamId']==steam_id)['faction']=faction
            if failure=='departed': players.pop()
            if failure=='stop': await panel.state.rcon_runtime.stop_connection()
            if failure=='rule_change': enable(panel,'factions',limits=LIMITS)
        panel.state.send.side_effect=send
        url='/api/game-rules/factions/shuffle/'+job['id']+'/execute'
        body={'targetRevision':revision,'acknowledgeRisk':True,'password':PASSWORD}
        assert (await client.post(url,json=body)).status_code==202
        await drain(panel)
        result=panel.state.shuffle_store.get(job['id'],ORIGIN)
        assert result['phase']=='uncertain' and len(calls)==1
        assert any(p['outcome']=='skipped' for p in result['plan'])
        recovered=ShuffleStore(panel.state.database)
        assert recovered.get(job['id'],ORIGIN)['phase']=='uncertain'
        assert len(calls)==1


def test_restart_disarms_running_job_without_retry(panel):
    enable(panel,'factions',limits=LIMITS)
    store=panel.state.shuffle_store
    rules=panel.state.game_rules_store.config(ORIGIN,'factions')
    plan=shuffle_plan(roster((0,3,3)),FactionSettings(limits=LIMITS))
    job=store.preview(ORIGIN,panel.state.owner.id,panel.state.rcon_runtime.target_revision,rules['generation'],plan,context_for(STATUS))
    assert store.start(job)
    job=store.get(job['id'],ORIGIN)
    next(p for p in job['plan'] if p['outcome']=='queued')['outcome']='processing'
    store.update(job)
    recovered=ShuffleStore(panel.state.database).get(job['id'],ORIGIN)
    assert recovered['phase']=='uncertain'
    assert all(p['outcome'] not in ('queued','processing') for p in recovered['plan'])
    assert panel.state.send.await_count==0


@pytest.mark.asyncio
async def test_subuser_cannot_preview_execute_or_read_shuffle(panel, monkeypatch):
    setup_job(panel,monkeypatch)
    panel.state.auth_service.create_subuser('viewer',PASSWORD,permissions=['changeFaction'])
    token=panel.state.auth_service.login('viewer',PASSWORD,'viewer')[1]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=panel),base_url=PUBLIC,
        headers={'Origin':PUBLIC},cookies={'panel_session':token}) as client:
        base='/api/game-rules/factions/shuffle'
        body={'targetRevision':panel.state.rcon_runtime.target_revision}
        assert (await client.post(base+'/preview',json=body)).status_code==403
        assert (await client.post(base+'/example/execute',json={**body,'acknowledgeRisk':True,'password':PASSWORD})).status_code==403
        assert (await client.get(base+'/example')).status_code==403
        assert (await client.get('/api/game-rules')).json()['latestShuffle'] is None
    panel.state.send.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_job_rejected_and_auto_balance_paused(panel):
    enable(panel,'factions',limits=LIMITS)
    store=panel.state.shuffle_store
    config=panel.state.game_rules_store.config(ORIGIN,'factions')
    def preview():
        return store.preview(ORIGIN,panel.state.owner.id,panel.state.rcon_runtime.target_revision,
            config['generation'],shuffle_plan(roster(),FactionSettings(limits=LIMITS)),context_for(STATUS))
    first,second=preview(),preview()
    assert store.start(first)
    with pytest.raises(PanelError,match='shuffle_busy'): store.start(second)
    await panel.state.game_rules_engine.factions_unlocked(roster(),STATUS)
    assert panel.state.game_rules_engine.last_factions['state']=='shuffle_running'
    panel.state.send.assert_not_called()
