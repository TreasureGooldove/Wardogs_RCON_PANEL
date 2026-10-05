"""No game traffic: exercise the stop latch with real clients on MockTransport."""
import asyncio
from cryptography.fernet import Fernet
import httpx
import pytest

from app.config import PanelSettings, RconTarget
from app.errors import PanelError
from app.main import create_app
from app.rcon.actions import ActionService
from app.rcon.routes import RouteName, WriteName
from app.rcon.runtime import RconRuntime

PUBLIC = 'https://panel.example.test'
GAME = 'https://game.example.test'
PASSWORD = 'Example-owner-password!'
SID = '76561190000000001'


@pytest.fixture
def panel(tmp_path):
    sent = []
    def handler(request):
        sent.append((request.method, request.url.path))
        return httpx.Response(200, json={})
    transport = httpx.MockTransport(handler)
    settings = PanelSettings(tmp_path/'panel.sqlite3', PUBLIC, True,
        config_key=Fernet.generate_key().decode(), rcon_target=RconTarget(GAME, 'example-rcon-secret'))
    app = create_app(settings, rcon_transport=transport)
    app.state.auth_service.create_admin('owner', PASSWORD)
    app.state.sent = sent
    app.state.test_transport = transport
    return app


def session(panel, username='owner'):
    return panel.state.auth_service.login(username, PASSWORD, username)[1]


def browser(panel, username='owner', origin=PUBLIC):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=panel), base_url=PUBLIC,
        headers={'Origin': origin}, cookies={'panel_session': session(panel, username)})


@pytest.mark.asyncio
async def test_stop_requires_owner_password_and_origin(panel):
    runtime = panel.state.rcon_runtime
    async with browser(panel) as client:
        assert (await client.post('/api/server/connection/stop', json={'password':'wrong'})).status_code == 401
        assert not runtime.paused
    async with browser(panel, origin='https://other.example.test') as client:
        assert (await client.post('/api/server/connection/stop', json={'password':PASSWORD})).status_code == 401
        assert not runtime.paused
    panel.state.auth_service.create_subuser('viewer', PASSWORD)
    async with browser(panel, 'viewer') as client:
        assert (await client.get('/api/server/connection')).status_code == 200
        assert (await client.post('/api/server/connection/stop', json={'password':PASSWORD})).status_code == 403
    assert not runtime.paused and not panel.state.sent


@pytest.mark.asyncio
async def test_stop_does_not_wait_for_rcon_lock_and_blocks_all_new_writes(panel):
    runtime = panel.state.rcon_runtime
    old_revision = runtime.target_revision
    async with runtime.lock, browser(panel) as client:
        response = await asyncio.wait_for(client.post('/api/server/connection/stop', json={'password':PASSWORD}), 2.)
        assert response.status_code == 200 and response.json()['paused'] is True
        assert runtime.target_revision != old_revision
        state = await client.get('/api/server/connection')
        assert state.headers['cache-control'] == 'no-store'
    operations = [runtime.client.request(RouteName.STATUS),
        runtime.client.moderate(WriteName.BAN, SID, 'fictional reason'),
        ActionService(runtime.client).send(WriteName.KILL, steam_id=SID),
        ActionService(runtime.client).send(WriteName.CHANGE_FACTION, steam_id=SID, faction='BLU')]
    for operation in operations:
        with pytest.raises(PanelError, match='rcon_stopped'):
            await operation
    assert panel.state.sent == []
    assert panel.state.database.connection_control()['paused']
    recovered = RconRuntime(panel.state.settings, panel.state.database, transport=panel.state.test_transport)
    assert recovered.paused and recovered.target is None and recovered.client.target is not None
    with pytest.raises(PanelError, match='rcon_stopped'):
        await recovered.client.request(RouteName.STATUS)
    await recovered.close()


@pytest.mark.asyncio
async def test_waiting_connection_slot_cannot_send_after_stop(panel):
    runtime = panel.state.rcon_runtime
    semaphore = runtime.client._semaphore
    for _ in range(4):
        await semaphore.acquire()
    pending = asyncio.create_task(runtime.client.request(RouteName.STATUS))
    await asyncio.sleep(0)
    async with browser(panel) as client:
        assert (await client.post('/api/server/connection/stop', json={'password':PASSWORD})).status_code == 200
    for _ in range(4):
        semaphore.release()
    with pytest.raises(PanelError, match='rcon_stopped'):
        await pending
    assert not panel.state.sent


@pytest.mark.asyncio
async def test_settings_save_cannot_bypass_persistent_stop_and_resume_requires_password(panel):
    runtime = panel.state.rcon_runtime
    async with browser(panel) as client:
        assert (await client.post('/api/server/connection/stop', json={'password':PASSWORD})).status_code == 200
        saved = await client.put('/api/server/settings', json={'name':'Example', 'origin':GAME, 'allowPublicHttp':False})
        assert saved.status_code == 200 and runtime.paused
        stopped_revision = runtime.target_revision
        assert (await client.post('/api/server/connection/resume', json={'password':'wrong'})).status_code == 401
        assert runtime.paused
        assert (await client.post('/api/server/connection/resume', json={'password':PASSWORD})).status_code == 200
        assert not runtime.paused and runtime.target_revision != stopped_revision
        assert not panel.state.database.connection_control()['paused']
        assert panel.state.sent == []  # Resume itself does not call the game.
        assert (await client.get('/api/status')).status_code != 401
    await runtime.client.request(RouteName.STATUS)
    assert panel.state.sent
    await runtime.close()
