import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from tests.test_upgrade import package


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(flock=lambda *args: None, LOCK_EX=1, LOCK_NB=2))
    spec = importlib.util.spec_from_file_location('host_updater', Path(__file__).parents[2] / 'deploy/update-agent.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, 'chown', lambda *args: None, raising=False)
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0, raising=False)
    return module


def test_host_independently_verifies_checksum_and_rejects_unsafe_zip(agent, monkeypatch, tmp_path):
    data = package()
    manifest = {'version': 'v9.0.0', 'url': 'https://gitee.com/gooldove/Wardogs_RCON_PANEL/raw/updates/package.zip',
                'sha256': hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(agent, 'fetch', lambda url, *args: json.dumps(manifest).encode() if url.endswith('latest.json') else data)
    agent.download('gitee', 'v9.0.0', tmp_path / 'good')
    assert (tmp_path / 'good/backend/app/main.py').exists()
    manifest['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='checksum_mismatch'):
        agent.download('gitee', 'v9.0.0', tmp_path / 'bad')
    data = package({'backend/app/../escape': 'unsafe'})
    manifest['sha256'] = hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError, match='unsafe_package'):
        agent.download('gitee', 'v9.0.0', tmp_path / 'unsafe')


def test_health_failure_restores_database_and_old_image(agent, monkeypatch, tmp_path):
    (tmp_path / 'deploy').mkdir()
    (tmp_path / 'deploy/compose.yaml').write_text('test')
    (tmp_path / 'data/updates').mkdir(parents=True)
    database = tmp_path / 'data/panel.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE value(data TEXT)')
        db.execute("INSERT INTO value VALUES('original')")
    (tmp_path / 'data/updates/request.json').write_text(json.dumps({'id': 'a'*32, 'source': 'gitee', 'version': 'v9.0.0'}))
    def stage(source, version, target):
        from app.upgrade import unpack
        unpack(package(), target, version)
    monkeypatch.setattr(agent, 'download', stage)
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        if args[:2] == ['docker', 'inspect']:
            return 'sha256:' + 'b' * 64
        if args[:2] == ['docker', 'exec']:
            return '0.3.0'
        if args[:2] == ['docker', 'compose'] and len([x for x in calls if x[:2] == ['docker', 'compose']]) == 1:
            with sqlite3.connect(database) as db:
                db.execute("UPDATE value SET data='new migration'")
        return ''
    monkeypatch.setattr(agent, 'command', command)
    monkeypatch.setattr(agent, 'healthy', lambda version: version == '0.3.0')
    # Windows reports st_uid=0 for this root-owned test staging directory.
    agent.run(tmp_path)
    assert json.loads((tmp_path / 'data/updates/status.json').read_text())['state'] == 'rolled_back'
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT data FROM value').fetchone()[0] == 'original'
    assert ['docker', 'tag', 'sha256:' + 'b'*64, 'wardogs-rcon-panel:local'] in calls
