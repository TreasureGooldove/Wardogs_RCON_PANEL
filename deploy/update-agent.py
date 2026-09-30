#!/usr/bin/env python3
"""Root host helper: fixed Docker deployment only; never exposed as HTTP."""
import argparse
import fcntl
import json
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import time
from uuid import uuid4
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import zipfile

REPOS = {'github': 'TreasureGooldove/Wardogs_RCON_PANEL', 'gitee': 'gooldove/Wardogs_RCON_PANEL'}
MANIFESTS = {'github': 'https://raw.githubusercontent.com/TreasureGooldove/Wardogs_RCON_PANEL/updates/latest.json',
             'gitee': 'https://gitee.com/gooldove/Wardogs_RCON_PANEL/raw/updates/latest.json'}


def release_manifest(source, version):
    if source == 'github':
        try:
            release = json.loads(fetch('https://api.github.com/repos/' + REPOS[source] + '/releases/latest', 1024 * 1024))
            if release.get('tag_name') == version and not release.get('draft') and not release.get('prerelease'):
                assets = release.get('assets', [])
                name = 'Wardogs_RCON_PANEL-' + version + '.zip'
                package = next((x for x in assets if x.get('name') == name), {})
                digest = package.get('digest', '')
                if re.fullmatch('sha256:[0-9a-f]{64}', digest) and trusted(package.get('browser_download_url', ''), source):
                    return {'version': version, 'url': package['browser_download_url'], 'sha256': digest[7:]}
        except Exception:
            pass
    return json.loads(fetch(MANIFESTS[source], 32768))


def trusted(url, source, redirect=False):
    p = urlsplit(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or p.fragment:
        return False
    if source == 'github':
        return (p.hostname == 'github.com' and p.path.startswith('/' + REPOS[source] + '/releases/download/') and not p.query) or (redirect and p.hostname == 'release-assets.githubusercontent.com')
    return (p.hostname == 'gitee.com' and p.path.startswith('/' + REPOS[source] + '/') and not p.query) or (redirect and p.hostname in ('gitee.com', 'giteeusercontent.com', 'files.gitee.com'))


def fetch(url, limit, source=None):
    class Redirects(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not source or not trusted(newurl, source, True):
                raise ValueError('untrusted_redirect')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    with build_opener(Redirects()).open(Request(url, headers={'User-Agent': 'Wardogs-panel-updater'}), timeout=20) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError('download_too_large')
        return data


def download(source, version, stage):
    # Root fetches its own pinned manifest; it never trusts package paths, URLs,
    # digests or extracted code supplied by the unprivileged panel process.
    for name in ('gitee', 'github') if source == 'auto' else (source,):
        try:
            manifest = release_manifest(name, version)
            if manifest.get('version') != version or not trusted(manifest.get('url', ''), name):
                raise ValueError('release_changed')
            digest = manifest.get('sha256', '')
            if not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('missing_digest')
            data = fetch(manifest['url'], 40 * 1024 * 1024, name)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('checksum_mismatch')
            break
        except Exception:
            if source != 'auto' or name == 'github':
                raise
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names, size = set(), 0
        for item in archive.infolist():
            p = Path(item.filename)
            if item.filename in names or p.is_absolute() or '..' in p.parts or '\\' in item.filename or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('unsafe_package')
            names.add(item.filename)
            size += item.file_size
            if size > 200 * 1024 * 1024 or len(names) > 10000:
                raise ValueError('package_too_large')
        project = archive.read('backend/pyproject.toml').decode('utf-8-sig')
        if not re.search(r'(?m)^version\s*=\s*"' + re.escape(version.lstrip('v')) + r'"\s*$', project):
            raise ValueError('package_version_mismatch')
        if not {'frontend/dist/index.html', 'backend/app/main.py'} <= names:
            raise ValueError('incomplete_package')
        stage.mkdir(mode=0o700, parents=True)
        for item in archive.infolist():
            if not item.is_dir() and (item.filename.startswith(('backend/app/', 'frontend/dist/')) or item.filename == 'backend/pyproject.toml'):
                p = stage / item.filename
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(archive.read(item))


def command(args, *, cwd=None, timeout=900):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True,
                          text=True, timeout=timeout).stdout.strip()


def read(path, default=None):
    try:
        if path.is_symlink() or path.stat().st_size > 32768:
            return default
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write(path, value):
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    with temp.open('x') as handle:
        json.dump(value, handle)
    os.chown(temp, 10001, 10001)
    os.chmod(temp, 0o600)
    temp.replace(path)


def healthy(version):
    for _ in range(30):
        state = command(['docker', 'inspect', 'wardogs-rcon-panel', '--format', '{{.State.Health.Status}}'], timeout=15)
        if state == 'healthy':
            actual = command(['docker', 'exec', 'wardogs-rcon-panel', 'python', '-c',
                              'from app.updates import APP_VERSION; print(APP_VERSION)'], timeout=15)
            return actual == version.lstrip('v')
        time.sleep(2)
    return False


def run(root):
    if (os.geteuid() != 0 or not (root / 'deploy/compose.yaml').is_file()
            or (root / 'data').is_symlink() or (root / 'data/panel.sqlite3').is_symlink()
            or not (root / 'data/panel.sqlite3').is_file()):
        raise ValueError('invalid_deployment')
    updates = root / 'data/updates'
    if updates.is_symlink():
        raise ValueError('invalid_update_storage')
    updates.mkdir(mode=0o700, exist_ok=True)
    os.chown(updates, 10001, 10001)
    # Do not follow panel-controlled symlinks when opening a root-owned lock.
    with os.fdopen(os.open(updates / 'agent.lock', os.O_CREAT | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600), 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        write(updates / 'agent.json', {'at': time.time()})
        request_path = updates / 'request.json'
        policy = read(updates / 'policy.json', {})
        previous = read(updates / 'status.json', {})
        policy = policy if isinstance(policy, dict) else {}
        previous = previous if isinstance(previous, dict) else {}
        if not request_path.exists() and policy.get('autoInstall') is True:
            checked = read(updates / 'last-check.json', {})
            if time.time() - checked.get('at', 0) < 3600:
                return
            source = policy.get('source', 'auto')
            if source not in ('auto', 'github', 'gitee'):
                return
            write(updates / 'last-check.json', {'at': time.time()})
            release = json.loads(command(['docker', 'exec', 'wardogs-rcon-panel', 'python', '-m',
                'app.upgrade', '--check', '--source', source], timeout=120))
            if not release.get('updateAvailable') or not release.get('installable'):
                return
            write(request_path, {'id': uuid4().hex, 'source': source, 'version': release['latestVersion']})
        request = read(request_path)
        if request is None:
            return
        if (not isinstance(request, dict) or set(request) != {'id', 'source', 'version'}
                or not re.fullmatch(r'[0-9a-f]{32}', str(request.get('id', '')))
                or request.get('source') not in ('auto', 'github', 'gitee')
                or not re.fullmatch(r'v\d+\.\d+\.\d+', str(request.get('version', '')))):
            request_path.rename(updates / ('invalid-' + uuid4().hex + '.json'))
            return
        job_id, version, source = request['id'], request['version'], request['source']
        if previous.get('id') == job_id:
            # An interrupted installation is never replayed automatically.
            write(updates / 'status.json', {**request, 'state': 'failed', 'code': 'interrupted_requires_review'})
            request_path.unlink()
            return
        request_path.rename(updates / ('accepted-' + job_id + '.json'))
        old_image = None
        switched = False
        def state(value, code=None):
            write(updates / 'status.json', {**request, 'state': value, 'code': code, 'at': time.time()})
        state('downloading')
        try:
            private = root / '.updates-host'
            private.mkdir(mode=0o700, exist_ok=True)
            if private.is_symlink() or private.stat().st_uid != 0:
                raise ValueError('invalid_host_storage')
            job = private / job_id
            stage = job / 'stage'
            download(source, version, stage)
            old_image = command(['docker', 'inspect', 'wardogs-rcon-panel', '--format', '{{.Image}}'])
            old_version = command(['docker', 'exec', 'wardogs-rcon-panel', 'python', '-c',
                                  'from app.updates import APP_VERSION; print(APP_VERSION)'])
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', old_image):
                raise ValueError('invalid_base_image')
            backup = job / 'panel.sqlite3'
            with sqlite3.connect(root / 'data/panel.sqlite3') as db, sqlite3.connect(backup) as saved:
                db.backup(saved)
                if saved.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('backup_failed')
            os.chmod(backup, 0o600)
            write(job / 'rollback.json', {'image': old_image, 'version': old_version})
            state('building')
            # Fixed context paths; package deploy scripts/compose/env are not used.
            (stage / 'Dockerfile').write_text(f'''FROM {old_image}
USER root
RUN rm -r /srv/backend/app /srv/frontend/dist
COPY backend/app /srv/backend/app
COPY backend/pyproject.toml /srv/backend/pyproject.toml
RUN python -m pip install --no-cache-dir /srv/backend "uvicorn[standard]>=0.30,<1"
COPY frontend/dist /srv/frontend/dist
USER 10001:10001
''')
            image = 'wardogs-rcon-panel:update-' + job_id
            command(['docker', 'build', '-t', image, str(stage)])
            # Refresh the snapshot immediately before restart so a long build
            # does not discard panel activity if installation needs rollback.
            with sqlite3.connect(root / 'data/panel.sqlite3') as db, sqlite3.connect(backup) as saved:
                db.backup(saved)
                if saved.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('backup_failed')
            state('installing')
            command(['docker', 'tag', image, 'wardogs-rcon-panel:local'])
            switched = True
            compose = ['docker', 'compose', '-f', 'deploy/compose.yaml', 'up', '-d', '--no-build', '--force-recreate', 'panel']
            command(compose, cwd=root)
            if not healthy(version):
                raise ValueError('health_check_failed')
            shutil.copy2(root / 'backend/pyproject.toml', job / 'old-pyproject.toml')
            for rel in ('backend/app', 'frontend/dist'):
                current = root / rel
                if current.exists():
                    current.rename(job / ('old-' + rel.replace('/', '-')))
                shutil.copytree(stage / rel, current)
            shutil.copy2(stage / 'backend/pyproject.toml', root / 'backend/pyproject.toml')
            state('success')
        except Exception:
            if switched and old_image:
                try:
                    command(['docker', 'tag', old_image, 'wardogs-rcon-panel:local'])
                    command(['docker', 'compose', '-f', 'deploy/compose.yaml', 'up', '-d',
                             '--no-build', '--force-recreate', 'panel'], cwd=root)
                    # Restore the backup while the container is stopped, preserving
                    # the unsuccessful DB separately for investigation.
                    command(['docker', 'stop', 'wardogs-rcon-panel'])
                    shutil.copy2(root / 'data/panel.sqlite3', job / 'failed-panel.sqlite3')
                    with sqlite3.connect(backup) as saved, sqlite3.connect(root / 'data/panel.sqlite3') as db:
                        saved.backup(db)
                    for rel in ('backend/app', 'frontend/dist'):
                        old = job / ('old-' + rel.replace('/', '-'))
                        if old.exists():
                            current = root / rel
                            if current.exists():
                                shutil.rmtree(current)
                            old.rename(current)
                    if (job / 'old-pyproject.toml').exists():
                        shutil.copy2(job / 'old-pyproject.toml', root / 'backend/pyproject.toml')
                    command(['docker', 'start', 'wardogs-rcon-panel'])
                    if not healthy(old_version):
                        raise ValueError('rollback_unhealthy')
                    state('rolled_back', 'update_failed')
                except Exception:
                    state('failed', 'rollback_requires_review')
            else:
                state('failed', 'download_or_build_failed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    run(Path(args.root).resolve())
