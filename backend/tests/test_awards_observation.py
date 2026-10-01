"""Awards and warcon cadence tests use only in-memory/local fake transports."""
from datetime import UTC,datetime,timedelta
from unittest.mock import AsyncMock
import httpx
import pytest
from app.config import PanelSettings,RconTarget
from app.main import create_app
from app.rcon.routes import RouteName
from app.errors import PanelError
from app.history.collector import HistoryCollector

ORIGIN='https://fake-game.example.test';PUBLIC='https://fake-panel.example.test'
A='76561190000000001';B='76561190000000002'

@pytest.fixture
def panel(tmp_path):
    app=create_app(PanelSettings(tmp_path/'db.sqlite3',PUBLIC,True,rcon_target=RconTarget(ORIGIN,'fake-only'),history_enabled=True))
    owner=app.state.auth_service.create_admin('owner','Fictional-only-123!')
    app.state.rcon_runtime.capabilities.require_advertised=AsyncMock()
    app.state.owner_id=owner.id
    return app

def record(app,at,map='A',players=None):
    return app.state.history_store.record(ORIGIN,{'map':map},{'players':players or []},at)

def player(sid=A,name='Fictional',kills=1,deaths=1,cash=1000):
    return dict(steamId=sid,name=name,kills=kills,deaths=deaths,cash=cash)

@pytest.mark.asyncio
async def test_cash_positive_changes_ties_leavers_and_one_delivery(panel,monkeypatch):
    store=panel.state.awards_store;engine=panel.state.awards_engine;runtime=panel.state.rcon_runtime
    store.save(ORIGIN,True,panel.state.owner_id)
    now=datetime.now(UTC)-timedelta(seconds=15)
    first=record(panel,now,players=[player(),player(B,'Other',cash=10000)])
    engine.observe(ORIGIN,runtime.target_revision)
    record(panel,now+timedelta(seconds=5),players=[player(kills=3,cash=900),player(B,'Other',kills=3,deaths=5,cash=10000)])
    record(panel,now+timedelta(seconds=10),players=[player(kills=3,cash=1100)])
    second=record(panel,datetime.now(UTC),map='B',players=[player(kills=0,deaths=0,cash=50)])
    data=store.view(ORIGIN)
    assert data['board'][0]['tiedPlayers']==2
    assert data['board'][1]['value']==200 # Not starting wallet, spending or net balance.
    assert data['board'][2]['winners'][0]['steamId']==B # Leavers remain eligible.
    assert '估算' in data['previewMessages'][1]
    assert len(data['jobs'])==1 and data['jobs'][0]['old_match']==first
    send=AsyncMock();monkeypatch.setattr('app.history.awards.ActionService.send',send)
    await engine.tick();await engine.tick()
    assert send.await_count==3 and store.view(ORIGIN)['jobs'][0]['outcome']=='accepted'
    assert all(len(call.kwargs['message'])<=200 for call in send.await_args_list)

@pytest.mark.asyncio
async def test_gaps_unknown_cash_startup_and_timeout_never_replay(panel,monkeypatch):
    store=panel.state.awards_store;engine=panel.state.awards_engine;runtime=panel.state.rcon_runtime
    store.save(ORIGIN,True,panel.state.owner_id)
    now=datetime.now(UTC)-timedelta(seconds=15)
    record(panel,now,players=[player(cash=None)])
    record(panel,now+timedelta(seconds=5),players=[player(cash=1000)])
    mid=record(panel,datetime.now(UTC),map='B',players=[player()])
    assert store.view(ORIGIN)['board'][1]['value'] is None
    engine.observe(ORIGIN,runtime.target_revision)
    assert store.view(ORIGIN)['jobs'][0]['outcome']=='skipped'
    record(panel,datetime.now(UTC)+timedelta(seconds=1),map='B',players=[player(kills=2)])
    record(panel,datetime.now(UTC)+timedelta(seconds=2),map='C',players=[player()])
    send=AsyncMock(side_effect=PanelError('action_uncertain'));monkeypatch.setattr('app.history.awards.ActionService.send',send)
    await engine.tick();await engine.tick();store.initialize();await engine.tick()
    send.assert_awaited_once()
    assert any(j['outcome']=='uncertain' for j in store.view(ORIGIN)['jobs'])
    count=len(store.view(ORIGIN)['jobs'])
    record(panel,datetime.now(UTC)+timedelta(minutes=4),map='D',players=[player()])
    assert len(store.view(ORIGIN)['jobs'])==count

@pytest.mark.asyncio
async def test_enable_requires_permission_password_and_no_immediate_message(panel,monkeypatch):
    auth=panel.state.auth_service;auth.create_subuser('reader','Fictional-only-123!')
    token=auth.login('owner','Fictional-only-123!','local')[1]
    body={'enabled':True,'targetRevision':panel.state.rcon_runtime.target_revision}
    send=AsyncMock();monkeypatch.setattr('app.history.awards.ActionService.send',send)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=panel),base_url=PUBLIC,
        headers={'Origin':PUBLIC},cookies={'panel_session':token}) as c:
        assert (await c.put('/api/community/awards',json=body)).status_code==400
        assert (await c.put('/api/community/awards',json={**body,'password':'wrong'})).status_code==401
        assert (await c.put('/api/community/awards',json={**body,'password':'Fictional-only-123!'})).status_code==200
    token=auth.login('reader','Fictional-only-123!','reader')[1]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=panel),base_url=PUBLIC,
        headers={'Origin':PUBLIC},cookies={'panel_session':token}) as c:
        assert (await c.put('/api/community/awards',json={**body,'password':'Fictional-only-123!'})).status_code==403
    send.assert_not_called()


@pytest.mark.asyncio
async def test_custom_titles_validation_persistence_and_queued_messages(panel,monkeypatch):
    auth=panel.state.auth_service
    token=auth.login('owner','Fictional-only-123!','local')[1]
    labels={'kills':'测试战神','cash_gain':'测试富豪','deaths':'测试勇士'}
    body={'enabled':False,'targetRevision':panel.state.rcon_runtime.target_revision,'titles':labels}
    send=AsyncMock();monkeypatch.setattr('app.history.awards.ActionService.send',send)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=panel),base_url=PUBLIC,
        headers={'Origin':PUBLIC},cookies={'panel_session':token}) as c:
        saved=await c.put('/api/community/awards',json=body)
        assert saved.status_code==200 and saved.json()['titles']==labels
        for bad in (' ', 'bad\nmessage', 'x'*25):
            rejected=await c.put('/api/community/awards',json={**body,'titles':{**labels,'kills':bad}})
            assert rejected.status_code==400 and rejected.json()['code']=='invalid_selection'
        enabled=await c.put('/api/community/awards',json={**body,'enabled':True,'password':'Fictional-only-123!'})
        assert enabled.status_code==200
    panel.state.awards_store.initialize()
    assert panel.state.awards_store.view(ORIGIN)['titles']==labels
    now=datetime.now(UTC)-timedelta(seconds=10)
    record(panel,now,players=[player()])
    record(panel,now+timedelta(seconds=5),players=[player(cash=1100)])
    record(panel,datetime.now(UTC),map='B',players=[player()])
    data=panel.state.awards_store.view(ORIGIN)
    assert [row['title'] for row in data['board']]==list(labels.values())
    assert all(label in data['jobs'][0]['parts'][i]['message'] for i,label in enumerate(labels.values()))
    send.assert_not_called()

@pytest.mark.asyncio
async def test_warcon_default_tiers_separate_due_times_and_shared_reads(panel,monkeypatch):
    clock=[100.];monkeypatch.setattr('app.history.collector.monotonic',lambda:clock[0])
    runtime=panel.state.rcon_runtime;collector=HistoryCollector(runtime,panel.state.history_store)
    runtime.capabilities.require=AsyncMock()
    async def fake(route):
        if route is RouteName.STATUS:return {'map':'A','players':{'current':1,'max':100}}
        if route is RouteName.PLAYERS:return {'players':[player()]}
        return {}
    runtime.client.request=AsyncMock(side_effect=fake)
    assert collector.cadence()==(30.,30.)
    runtime.watch_until=115
    await collector.sample(scheduled=True)
    assert collector.cadence()==(1.,2.)
    initial=runtime.client.request.await_count;clock[0]=100.5
    assert not await collector.sample(scheduled=True) and runtime.client.request.await_count==initial
    clock[0]=101.;await collector.sample(scheduled=True)
    assert runtime.client.request.await_args.args==(RouteName.PLAYERS,)
    # API viewers consume the worker's cached snapshot, without another RCON call.
    runtime.collector_enabled=True
    await runtime.read_service.players();assert runtime.client.request.await_count==initial+1
    runtime.watch_until=0;assert collector.cadence()==(2.,5.)
    clock[0]=102.;await collector.sample(scheduled=True)
    queried=[call.args[0] for call in runtime.client.request.await_args_list[initial+1:]]
    assert RouteName.STATUS in queried and RouteName.PLAYERS in queried
    clock[0]=103.;before=runtime.client.request.await_count
    await collector.sample(scheduled=True)
    assert runtime.client.request.await_count==before
    collector.players={'players':[]};collector.status={'playerCount':0};assert collector.cadence()==(30.,30.)
    for failures,seconds in [(3,30.),(4,60.),(5,120.),(20,120.)]:
        collector.failures=failures;assert collector.cadence()==(seconds,seconds)
    assert HistoryCollector.next_due(100.,2.,107.)==108.
