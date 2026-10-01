"""Local-only safety and behavior checks. Never use a live RCON transport."""
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import PanelSettings, RconTarget
from app.errors import PanelError
from app.main import create_app
from app.history.community import CommunityStore
from app.steam.risk import SteamRiskService

SID='76561190000000001'; OTHER='76561190000000002'
ORIGIN='https://fake-game.example.test'; PUBLIC='https://fake-panel.example.test'
FEED='fictional-feed-'+('f'*40)

@pytest.fixture
def panel(tmp_path):
    app=create_app(PanelSettings(tmp_path/'panel.sqlite3',PUBLIC,True,
        rcon_target=RconTarget(ORIGIN,'fictional-rcon'),feed_token=FEED,feed_origin=ORIGIN))
    auth=app.state.auth_service
    owner=auth.create_admin('owner','Fictional-pass-123!')
    auth.create_subuser('readonly','Fictional-pass-123!')
    auth.create_subuser('messenger','Fictional-pass-123!',permissions=['message','rules'])
    app.state.rcon_runtime.capabilities.require_advertised=AsyncMock()
    app.state.rcon_runtime.client.request=AsyncMock(return_value={'players':[{'steamId':SID,'name':'Fake Player'}]})
    return app

def client(app,username='owner'):
    token=app.state.auth_service.login(username,'Fictional-pass-123!',username)[1]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=PUBLIC,
        headers={'Origin':PUBLIC},cookies={'panel_session':token})

def event(index=1,**changes):
    return dict(type='killed',eventId=str(index),matchId='fake-round',mapName='MapA',eventTime=index*12,
        killerSteamId=SID,killerName='Fictional',victimSteamId=OTHER,victimName='Other',
        distance=1234,contextTags=['Combat.Headshot'],**changes)

def sample(app,at,players):
    return app.state.history_store.record(ORIGIN,{'map':'MapA'},{'players':players},at)

@pytest.mark.asyncio
async def test_dossier_sessions_gaps_permissions_revision_and_isolation(panel):
    now=datetime.now(UTC)-timedelta(minutes=1)
    p={'steamId':SID,'name':'First','kills':2}
    sample(panel,now,[p]);sample(panel,now+timedelta(seconds=5),[{**p,'name':'Second'}])
    sample(panel,now+timedelta(seconds=10),[])
    sample(panel,now+timedelta(seconds=60),[p])
    async with client(panel) as c:
        data=(await c.get(f'/api/community/players/{SID}/dossier')).json()
        assert data['observedPlaytimeSeconds']==5 and len(data['sessions'])==2
        assert data['sessions'][0]['join_observed']==0
        assert {a['name'] for a in data['aliases']}=={'First','Second'}
        payload={'note':'Private note','watched':True,'targetRevision':data['targetRevision']}
        assert (await c.put(f'/api/community/players/{SID}/annotation',json={**payload,'targetRevision':'stale'})).status_code==409
        assert (await c.put(f'/api/community/players/{SID}/annotation',json=payload)).status_code==200
        audit=(await c.get('/api/community/audit')).json()['items']
        assert 'Private note' not in str(audit) and 'note_sha256' in str(audit)
        assert (await c.put(f'/api/community/players/{SID}/annotation',json=payload,headers={'Origin':'https://wrong.example.test'})).status_code==401
    async with client(panel,'readonly') as c:
        assert (await c.put(f'/api/community/players/{SID}/annotation',json=payload)).status_code==403
        assert (await c.get('/api/community/audit')).status_code==403
    assert panel.state.community_store.dossier('https://other.example.test',SID)['aliases']==[]
    assert panel.state.community_store.dossier('https://other.example.test',SID)['observedPlaytimeSeconds'] is None

@pytest.mark.asyncio
async def test_batch_timeout_idempotency_and_readonly(panel,monkeypatch):
    send=AsyncMock(side_effect=PanelError('action_uncertain'))
    monkeypatch.setattr('app.api.community.ActionService.send',send)
    body={'requestId':str(uuid4()),'steamIds':[SID],'message':'Local test only',
          'targetRevision':panel.state.rcon_runtime.target_revision}
    async with client(panel,'readonly') as c:
        assert (await c.post('/api/community/messages',json=body)).status_code==403
    async with client(panel,'messenger') as c:
        response=await c.post('/api/community/messages',json=body)
        assert response.status_code==202,response.text
        await asyncio.gather(*list(panel.state.community_tasks))
        result=(await c.get('/api/community/messages/'+body['requestId'])).json()
        assert result['complete'] and result['items'][0]['outcome']=='uncertain'
        assert (await c.post('/api/community/messages',json=body)).status_code==202
        assert (await c.post('/api/community/messages',json={**body,'message':'changed'})).status_code==409
        assert (await c.post('/api/community/messages',json={**body,'requestId':str(uuid4()),'steamIds':[OTHER]})).status_code==409
    send.assert_awaited_once()
    restarted=CommunityStore(panel.state.database);restarted.initialize()
    assert restarted.batch_status(body['requestId'],ORIGIN)['items'][0]['outcome']=='uncertain'

@pytest.mark.asyncio
async def test_batch_revoked_permission_and_restart_no_replay(panel,monkeypatch):
    send=AsyncMock();monkeypatch.setattr('app.api.community.ActionService.send',send)
    actor=panel.state.database.get_admin_by_username('messenger')
    bid=str(uuid4());store=panel.state.community_store
    recipients=[{'steamId':SID,'name':'fake'}]
    store.batch(bid,actor.id,ORIGIN,panel.state.rcon_runtime.target_revision,recipients,'Test')
    panel.state.auth_service.update_subuser(actor.id,permissions=[])
    from app.api.community import deliver_batch
    await deliver_batch(panel,bid,ORIGIN,panel.state.rcon_runtime.target_revision,actor.id,recipients,'Test')
    assert store.batch_status(bid,ORIGIN)['items'][0]['error']=='permission_denied';send.assert_not_called()
    bid=str(uuid4());store.batch(bid,actor.id,ORIGIN,'rev',recipients,'Test');store.outcome(bid,SID,'processing')
    store.initialize();assert store.batch_status(bid,ORIGIN)['items'][0]['outcome']=='uncertain'

@pytest.mark.asyncio
async def test_feed_auth_dedupe_atomic_validation_and_match_link(panel):
    at=datetime.now(UTC);mid=sample(panel,at,[{'steamId':SID,'name':'Fictional'}])
    body={'serverId':'fake-process','events':[event()]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=panel),base_url=PUBLIC) as c:
        assert (await c.post('/api/ingest/events',json=body)).status_code==401
        headers={'Authorization':'Bearer '+FEED}
        assert (await c.post('/api/ingest/events',headers=headers,json=body)).json()['accepted']==1
        assert (await c.post('/api/ingest/events',headers=headers,json=body)).json()['duplicates']==1
        bad=event(2);bad['distance']=True
        assert (await c.post('/api/ingest/events',headers=headers,json={**body,'events':[event(3),bad]})).status_code==400
        assert (await c.post('/api/ingest/events',headers=headers,content=b'x'*65537)).status_code==413
        second=event(4);second['matchId']='another-real-round'
        assert (await c.post('/api/ingest/events',headers=headers,json={**body,'events':[second]})).json()['accepted']==1
    data=panel.state.kill_store.query(ORIGIN,mid)
    assert data['total']==1 and data['items'][0]['distance_meters']==12.34
    assert data['unlinkedRounds']==1
    assert panel.state.kill_store.query('https://other.example.test','all')['total']==0
    panel.state.kill_store.initialize()
    assert panel.state.kill_store.query(ORIGIN,'all')['total']==2


def test_feed_before_snapshot_links_later_but_competing_rounds_do_not(panel):
    at=datetime.now(UTC)
    store=panel.state.kill_store
    store.ingest(ORIGIN,'early-process',[event()],at=at)
    assert store.query(ORIGIN,'all')['unlinkedRounds']==1
    mid=sample(panel,at+timedelta(seconds=2),[{'steamId':SID,'name':'Fictional'}])
    data=store.query(ORIGIN,mid)
    assert data['total']==1 and data['items'][0]['local_match_id']==mid
    second=event(2);second['matchId']='another-round'
    store.ingest(ORIGIN,'early-process',[second],at=at+timedelta(seconds=3))
    assert store.query(ORIGIN,mid)['total']==1
    assert store.query(ORIGIN,'all')['unlinkedRounds']==1

@pytest.mark.asyncio
async def test_alerts_require_real_samples_not_unknown_fields(panel):
    rows=[event(i) for i in range(1,6)]
    panel.state.kill_store.save_settings(ORIGIN,dict(windowMinutes=10,minimumKills=5,killsPerMinute=2,headshotPercent=90))
    panel.state.kill_store.ingest(ORIGIN,'first-process',rows)
    alerts=panel.state.kill_store.alerts(ORIGIN)
    assert alerts['items'][0]['reasons']==['headshot_share'] # Span <60s cannot support rate.
    for row in rows:row['eventTime']=None;row['contextTags']=None
    panel.state.kill_store.ingest(ORIGIN,'unknown-fields',rows)
    assert len(panel.state.kill_store.alerts(ORIGIN)['items'])==1
    self_kill=event(9);self_kill['victimSteamId']=SID
    panel.state.kill_store.ingest(ORIGIN,'first-process',[self_kill])
    assert panel.state.kill_store.alerts(ORIGIN)['items'][0]['kills']==5
    async with client(panel,'readonly') as c:
        assert (await c.put('/api/community/kill-alerts/settings',json={**alerts['settings'],'targetRevision':panel.state.rcon_runtime.target_revision})).status_code==403
        assert (await c.get('/api/community/feed-setup')).status_code==403

@pytest.mark.asyncio
async def test_preview_never_calls_rcon_and_honors_session_observations(panel):
    start=datetime.now(UTC)-timedelta(seconds=30)
    sample(panel,start,[])
    sample(panel,start+timedelta(seconds=5),[{'steamId':SID,'name':'Fictional'}])
    sample(panel,start+timedelta(seconds=10),[{'steamId':SID,'name':'Fictional'}])
    body=dict(targetRevision=panel.state.rcon_runtime.target_revision,enabled=False,
        firstText='Hello {name}, {count}',secondText='',delaySeconds=0,gapSeconds=5,cooldownMinutes=60,maxPerRound=1)
    async with client(panel,'messenger') as c:
        response=await c.post('/api/community/rules/preview',json=body)
        assert response.status_code==200,response.text
        data=response.json();assert data['commandsSent']==0 and data['simulation']
        assert data['items'][0]['result']=='would_send' and 'Fictional' in data['items'][0]['parts'][0]
        body['delaySeconds']=60
        assert (await c.post('/api/community/rules/preview',json=body)).json()['items'][0]['result']=='left_before_delivery'
    panel.state.rcon_runtime.client.request.assert_not_called()

@pytest.mark.asyncio
async def test_audit_filters_csv_formula_and_secret_redaction(panel):
    owner=panel.state.database.get_admin_by_username('owner')
    for origin in (ORIGIN,'https://other.example.test'):
        panel.state.database.append_moderation_audit(owner.id,'warning',SID,'accepted','=WEBSERVICE("fake") token=fake-secret',str(uuid4()),'rev',origin)
    async with client(panel) as c:
        response=await c.get('/api/community/audit?format=csv&action=warning')
        assert response.status_code==200 and response.text.startswith('\ufeff')
        assert "'=WEBSERVICE" in response.text and 'fake-secret' not in response.text
        assert response.headers['x-total-count']=='1'
        assert (await c.get('/api/community/audit?actor=absent')).json()['total']==0

@pytest.mark.asyncio
async def test_steam_private_age_unknown_and_malformed_cached():
    class Profiles:
        _client=None
        def _usable_key(self):return 'fictional-only-key'
    service=SteamRiskService(Profiles())
    service._fetch=AsyncMock(side_effect=[{'players':[{'SteamId':SID,'VACBanned':True,'NumberOfVACBans':1,'NumberOfGameBans':0,'DaysSinceLastBan':1000}]},{'response':{'players':[{'steamid':SID}]}}])
    result=(await service.lookup([SID]))[0]
    assert result['accountAgeDays'] is None and result['signals']==['vac_ban'] and result['advisoryOnly']
    assert (await service.lookup([SID]))[0]==result
    assert service._fetch.await_count==2
    service=SteamRiskService(Profiles());service._fetch=AsyncMock(side_effect=[{'players':[{'SteamId':[]}]},{'response':{'players':[]}}])
    with pytest.raises(PanelError,match='steam_bad_response'):await service.lookup([SID])
    with pytest.raises(PanelError,match='steam_bad_response'):await service.lookup([SID])
    assert service._fetch.await_count==2
