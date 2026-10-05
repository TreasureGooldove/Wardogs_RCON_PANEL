"""Offline publisher checks: retry reads, never blindly retry uncertain Git writes."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest


def publisher():
    path = Path(__file__).resolve().parents[2] / 'deploy/publish-gitee.py'
    spec = importlib.util.spec_from_file_location('release_publisher_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assets(tmp_path):
    package = tmp_path / 'Wardogs_RCON_PANEL-v9.9.0.zip'
    package.write_bytes(b'fictional-package')
    sums = tmp_path / 'SHA256SUMS.txt'
    sums.write_text(hashlib.sha256(package.read_bytes()).hexdigest() + '  ' + package.name)
    notes = tmp_path / 'notes.md'
    notes.write_text('Fictional release')
    return package, sums, notes


@pytest.mark.parametrize('fail_push', [False, True])
def test_read_retry_and_uncertain_write_boundary(tmp_path, monkeypatch, fail_push):
    module = publisher()
    package, sums, notes = assets(tmp_path)
    calls, waits, api_calls = [], [], []
    monkeypatch.setenv('GIT_ASKPASS', 'fictional-askpass')
    monkeypatch.setattr(module.time, 'sleep', waits.append)

    def run(command, **options):
        operation = next(name for name in ('ls-remote', 'clone', 'checkout', 'add', 'diff', 'commit', 'push') if name in command)
        calls.append(operation)
        assert options['stdin'] == subprocess.DEVNULL and options['timeout'] == 120
        if operation == 'ls-remote' and calls.count(operation) == 1:
            raise subprocess.TimeoutExpired(command, 120)
        if operation == 'clone':
            Path(command[-1]).mkdir()
        if operation == 'push' and fail_push:
            raise subprocess.TimeoutExpired(command, 120)
        return SimpleNamespace(returncode=0, stdout='changed' if operation == 'diff' and fail_push else '')

    def api(path, token, *args, **kwargs):
        api_calls.append(path)
        assert token == 'fictional-token'
        return [{'id': 1, 'tag_name': 'v9.9.0',
                 'assets': [{'name': package.name}, {'name': sums.name}]}]

    monkeypatch.setattr(module.subprocess, 'run', run)
    monkeypatch.setattr(module, 'api', api)
    if fail_push:
        with pytest.raises(RuntimeError, match='inspect remote state'):
            module.publish('fictional-token', 'v9.9.0', package, sums, notes)
        assert calls.count('push') == 1
        assert api_calls == ['/releases?per_page=100']  # Release discovery occurs before a fallback push.
    else:
        module.publish('fictional-token', 'v9.9.0', package, sums, notes)
        assert api_calls == ['/releases?per_page=100']  # Existing attachments are not uploaded again.
    assert calls.count('ls-remote') == 2 and waits == [2]
