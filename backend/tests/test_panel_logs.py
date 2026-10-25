import asyncio,json,logging
from datetime import UTC,datetime
import httpx
from fastapi.testclient import TestClient
from app.config import PanelSettings,RconTarget
from app.main import create_app
from app.panel_logs import PanelLogs

def test_logs_redact_credentials_and_keep_operational_metadata(tmp_path):
    service=PanelLogs(tmp_path,secrets=lambda:['Fictional-private-value-123'])
    service.start()
    try:
        service.emit('http',route='/api/server/config/rotation',password='never-store-me',
            message='Bearer fictional-bearer https://example.invalid/path?key=hidden Fictional-private-value-123')
        logging.getLogger('wardogs.test').warning('token=another-private-token')
        service.queue.join()
        rows=service.read()
        text=json.dumps(rows)
        assert 'never-store-me' not in text and 'another-private-token' not in text and 'fictional-bearer' not in text
        assert 'Fictional-private-value-123' not in text and '?key=' not in text
        assert '/api/server/config/rotation' in text and '[REDACTED]' in text
    finally:service.close()

def test_private_log_view_requires_owner_password_origin_and_does_not_log_the_password(tmp_path):
    origin='https://panel.example.invalid'
    app=create_app(PanelSettings(db_path=tmp_path/'panel.sqlite3',public_origin=origin,session_secure=True,
        rcon_target=RconTarget('https://fictional-game.invalid','Fictional-rcon-bearer-to-mask')))
    password='Fictional-owner-password-123'
    app.state.auth_service.create_admin('owner',password)
    app.state.auth_service.create_subuser('viewer','Fictional-viewer-password-123')
    with TestClient(app,base_url=origin) as client:
        assert client.post('/api/panel/logs/query',json={'password':password},headers={'Origin':origin}).status_code==401
        client.post('/api/auth/login',json={'username':'viewer','password':'Fictional-viewer-password-123'},headers={'Origin':origin})
        assert client.post('/api/panel/logs/query',json={'password':password},headers={'Origin':origin}).status_code==403
        client.post('/api/auth/logout',headers={'Origin':origin})
        client.post('/api/auth/login',json={'username':'owner','password':password},headers={'Origin':origin})
        wrong=client.post('/api/panel/logs/query',json={'password':'Fictional-wrong-confirmation'},headers={'Origin':origin})
        assert wrong.status_code==401 and client.get('/api/auth/me').status_code==200
        assert client.post('/api/panel/logs/query',json={'password':password},headers={'Origin':'https://other.invalid'}).json()['code']=='origin_mismatch'
        app.state.panel_logs.queue.join()
        result=client.post('/api/panel/logs/query',json={'password':password},headers={'Origin':origin})
        assert result.status_code==200 and result.headers['cache-control']=='no-store'
        assert result.json()['available'] and any(row.get('status')==401 for row in result.json()['items'])
        assert password not in result.text and 'Fictional-wrong-confirmation' not in result.text
        assert 'Fictional-rcon-bearer-to-mask' not in result.text
        assert client.post('/api/panel/logs/client',json={'source':'vue','errorType':'TypeError','version':'0.3.14'},headers={'Origin':origin}).status_code==204
        assert client.post('/api/panel/logs/client',json={'source':'vue','errorType':password,'version':'0.3.14'},headers={'Origin':origin}).status_code==400

def test_rotation_wrong_confirmation_and_origin_preserve_backend_session(tmp_path):
    async def run():
        origin='https://panel.example.invalid'
        app=create_app(PanelSettings(db_path=tmp_path/'panel.sqlite3',public_origin=origin,session_secure=True,
            rcon_target=RconTarget('https://fictional-game.invalid','Fictional-rcon-secret')),
            rcon_transport=httpx.MockTransport(lambda request:(_ for _ in ()).throw(AssertionError('no game writes'))))
        app.state.auth_service.create_admin('owner','Fictional-owner-password-123')
        token=app.state.auth_service.login('owner','Fictional-owner-password-123','test')[1]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=origin,cookies={'panel_session':token}) as client:
            body={'text':'[Rotation]\nx=y','revision':'fixture-revision','targetRevision':app.state.rcon_runtime.target_revision,'password':'Fictional-wrong-password'}
            response=await client.put('/api/server/config/rotation',json=body,headers={'Origin':origin})
            assert response.status_code==401 and response.json()['code']=='invalid_credentials'
            assert (await client.get('/api/auth/me')).status_code==200
            response=await client.put('/api/server/config/rotation',json=body,headers={'Origin':'https://other.invalid'})
            assert response.json()['code']=='origin_mismatch' and (await client.get('/api/auth/me')).status_code==200
        await app.state.rcon_runtime.close();await app.state.item_catalog.close();await app.state.release_checker.close();await app.state.steam_service.close()
    asyncio.run(run())
