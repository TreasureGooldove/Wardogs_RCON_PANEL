import asyncio
import hashlib
import io
import json
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import PanelSettings
from app.errors import PanelError
from app.main import create_app
from app.release_sources import ReleaseChecker, SOURCES, trusted_url
from app.upgrade import atomic_json, enqueue, unpack, update_state


def package(extra=None, version='9.0.0'):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, value in {'backend/pyproject.toml': f'[project]\nversion="{version}"\n',
                            'backend/app/main.py': '# test', 'frontend/dist/index.html': 'test',
                            'deploy/panel.env': 'must not extract', **(extra or {})}.items():
            archive.writestr(name, value)
    return stream.getvalue()


def test_empty_release_falls_back_and_auto_chooses_newest():
    async def scenario():
        def handler(request):
            url = str(request.url)
            assert 'authorization' not in request.headers
            if url == SOURCES['gitee']['manifest']:
                return httpx.Response(200, json={'version': 'v9.0.0', 'sha256': 'a' * 64,
                    'url': 'https://gitee.com/gooldove/Wardogs_RCON_PANEL/raw/updates/packages/Wardogs_RCON_PANEL-v9.0.0.zip'})
            if url == SOURCES['github']['api']:
                return httpx.Response(200, json={'tag_name': 'v8.0.0', 'assets': []})
            return httpx.Response(404)
        checker = ReleaseChecker(httpx.MockTransport(handler))
        try:
            result = await checker.check()
            assert result['source'] == 'gitee'
            assert result['fallback'] and result['installable'] and result['updateAvailable']
            assert (await checker.check(refresh=True))['cached']
        finally:
            await checker.close()
    asyncio.run(scenario())


def test_untrusted_manifest_cannot_install_and_download_redirect_is_rejected():
    async def scenario():
        def handler(request):
            if str(request.url) == SOURCES['gitee']['manifest']:
                return httpx.Response(200, json={'version': 'v9.0.0', 'sha256': 'a'*64, 'url': 'https://evil.invalid/a.zip'})
            if 'package.zip' in str(request.url):
                return httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})
            return httpx.Response(404)
        checker = ReleaseChecker(httpx.MockTransport(handler))
        try:
            assert not (await checker.check(source='gitee'))['installable']
            with pytest.raises(ValueError, match='untrusted_download'):
                await checker.fetch('https://gitee.com/gooldove/Wardogs_RCON_PANEL/package.zip', 100, source='gitee')
        finally:
            await checker.close()
    asyncio.run(scenario())
    assert not trusted_url('https://gitee.com/other/repo/a.zip', 'gitee')
    assert trusted_url('https://foruda.gitee.com/attachments/package.zip?signature=example', 'gitee', redirect=True)
    assert trusted_url('https://raw.giteeusercontent.com/gooldove/Wardogs_RCON_PANEL/raw/updates/latest.json?signature=example', 'gitee', redirect=True)
    assert not trusted_url('https://foruda.gitee.com.evil.invalid/a.zip', 'gitee', redirect=True)


def test_archive_limits_scope_and_version(tmp_path):
    target = tmp_path / 'safe'
    unpack(package(), target, 'v9.0.0')
    assert (target / 'backend/app/main.py').is_file()
    assert not (target / 'deploy').exists()
    with pytest.raises(ValueError, match='unsafe_package'):
        unpack(package({'backend/app/../../escape': 'bad'}), tmp_path / 'bad', 'v9.0.0')
    with pytest.raises(ValueError, match='package_version_mismatch'):
        unpack(package(), tmp_path / 'wrong', 'v8.0.0')
    assert not (tmp_path / 'wrong').exists()


def test_exclusive_queue_and_bad_heartbeat(tmp_path):
    settings = PanelSettings(db_path=tmp_path / 'panel.sqlite3', public_origin='https://panel.example.invalid', session_secure=True)
    atomic_json(tmp_path / 'updates/agent.json', {'at': 'invalid'})
    assert not update_state(settings)['agentAvailable']
    with pytest.raises(PanelError) as exc:
        enqueue(settings, 'auto', 'v9.0.0')
    assert exc.value.code == 'updater_unavailable'
    atomic_json(tmp_path / 'updates/agent.json', {'at': time.time()})
    assert enqueue(settings, 'gitee', 'v9.0.0')['state'] == 'queued'
    assert update_state(settings)['job']['state'] == 'queued'
    with pytest.raises(PanelError) as exc:
        enqueue(settings, 'github', 'v9.0.0')
    assert exc.value.code == 'update_busy'


def test_install_requires_owner_password_and_origin(tmp_path):
    origin = 'https://panel.example.invalid'
    app = create_app(PanelSettings(db_path=tmp_path / 'panel.sqlite3', public_origin=origin, session_secure=True))
    app.state.auth_service.create_admin('owner', 'test-owner-password')
    app.state.auth_service.create_subuser('viewer', 'test-viewer-password')
    class Checker:
        async def check(self, **kwargs):
            return {'updateAvailable': True, 'installable': True, 'latestVersion': 'v9.0.0'}
        async def close(self):
            pass
    asyncio.run(app.state.release_checker.close())
    app.state.release_checker = Checker()
    headers = {'Origin': origin}
    with TestClient(app, base_url=origin) as client:
        assert client.post('/api/auth/login', json={'username': 'viewer', 'password': 'test-viewer-password'}, headers=headers).status_code == 200
        assert client.get('/api/panel/updates/state').status_code == 403
        assert client.post('/api/panel/updates/install', json={'password': 'test-viewer-password'}, headers=headers).status_code == 403
        client.post('/api/auth/logout', headers=headers)
        client.post('/api/auth/login', json={'username': 'owner', 'password': 'test-owner-password'}, headers=headers)
        assert client.put('/api/panel/updates/policy', json={'password': 'wrong-password'}, headers=headers).status_code == 401
        assert client.put('/api/panel/updates/policy', json={'password': 'test-owner-password'}, headers={'Origin': 'https://evil.invalid'}).status_code == 401
        assert client.post('/api/panel/updates/install', json={'password': 'test-owner-password'}, headers=headers).status_code == 503
        atomic_json(tmp_path / 'updates/agent.json', {'at': time.time()})
        result = client.put('/api/panel/updates/policy', json={'password': 'test-owner-password', 'autoInstall': True, 'source': 'gitee'}, headers=headers)
        assert result.status_code == 200
        assert 'password' not in (tmp_path / 'updates/policy.json').read_text()
        assert client.post('/api/panel/updates/install', json={'password': 'test-owner-password'}, headers=headers).status_code == 200
        assert client.post('/api/panel/updates/install', json={'password': 'test-owner-password'}, headers=headers).status_code == 409
