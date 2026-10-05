#!/usr/bin/env python3
"""Publish sanitized GitHub assets to Gitee Release and fallback branch."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from uuid import uuid4

API = 'https://gitee.com/api/v5/repos/gooldove/Wardogs_RCON_PANEL'
REPO_URL = 'https://gitee.com/gooldove/Wardogs_RCON_PANEL'


def api(path, token, payload=None, *, method=None, multipart=None):
    headers = {'User-Agent': 'Wardogs-release-publisher'}
    if multipart:
        filename, data = multipart
        boundary = uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="access_token"\r\n\r\n{token}\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
        body += data + f'\r\n--{boundary}--\r\n'.encode()
        headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
    else:
        values = {**(payload or {}), 'access_token': token}
        body = urlencode({k: str(v).lower() if isinstance(v, bool) else v for k, v in values.items()}).encode() if method or payload is not None else None
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        headers['Authorization'] = 'Bearer ' + token
    try:
        url = API + path
        if body is None:
            url += ('&' if '?' in url else '?') + urlencode({'access_token': token})
        with urlopen(Request(url, body, headers, method=method), timeout=180) as response:
            return json.load(response)
    except HTTPError as exc:
        # Never print request headers, token-bearing body or upstream free text.
        raise RuntimeError('Gitee HTTP ' + str(exc.code)) from None
    except (URLError, TimeoutError):
        raise RuntimeError('Gitee network request failed; inspect remote state before retrying') from None


def publish(token, version, package, checksums, notes):
    if not re.fullmatch(r'v\d+\.\d+\.\d+', version) or package.name != f'Wardogs_RCON_PANEL-{version}.zip':
        raise ValueError('invalid_asset_name')
    data = package.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if not re.search(r'(?m)^' + digest + r'\s+\*?' + re.escape(package.name) + r'\s*$', checksums.read_text()):
        raise ValueError('checksum_mismatch')
    # A partially completed publication is resumed using read-only discovery;
    # never blindly replay create/upload after an uncertain network error.
    releases = api('/releases?per_page=100', token)
    release = next((r for r in releases if r.get('tag_name') == version), None)
    if not release:
        release = api('/releases', token, {'tag_name': version, 'name': version,
                      'body': notes.read_text(encoding='utf-8-sig'), 'target_commitish': 'main', 'prerelease': False})
    assets = release.get('assets', [])
    if isinstance(assets, dict):
        assets = assets.get('links', [])
    existing_names = {x.get('name') for x in assets if isinstance(x, dict)}
    for asset in (package, checksums):
        if asset.name not in existing_names:
            api('/releases/' + str(release['id']) + '/attach_files', token, multipart=(asset.name, asset.read_bytes()))
    print(REPO_URL + '/releases/tag/' + version, flush=True)
    # Fallback branch is updated only by the publisher with prebuilt artifacts.
    manifest = {'version': version, 'url': REPO_URL + '/raw/updates/packages/' + package.name,
                'sha256': digest, 'publishedAt': datetime.now(timezone.utc).isoformat()}
    # Publish binary artifacts over Git, avoiding the contents API's body limits.
    # CI supplies GIT_ASKPASS; it reads only the protected environment secret.
    if not os.environ.get('GIT_ASKPASS'):
        raise RuntimeError('Configure GIT_ASKPASS for authenticated Git publication')
    with tempfile.TemporaryDirectory(prefix='wardogs-gitee-') as temporary:
        checkout = Path(temporary) / 'updates'
        def git(*args):
            # Retry only reads. A timed-out push must be checked remotely before resuming.
            read = args[0] in ('ls-remote', 'clone')
            for attempt in range(3 if read else 1):
                try:
                    result = subprocess.run(['git', '-c', 'credential.helper=',
                        '-c', 'http.version=HTTP/1.1', '-c', 'http.lowSpeedLimit=512',
                        '-c', 'http.lowSpeedTime=30', '-c', 'http.postBuffer=10485760', *args], cwd=checkout if checkout.exists() else None,
                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
                    if result.returncode == 0:
                        return result.stdout
                except subprocess.TimeoutExpired:
                    pass
                if not read or attempt == 2:
                    raise RuntimeError('Gitee Git request failed; inspect remote state before resuming')
                if args[0] == 'clone' and checkout.exists():
                    # This directory belongs only to this publisher's TemporaryDirectory.
                    assert checkout.resolve().parent == Path(temporary).resolve()
                    shutil.rmtree(checkout)
                time.sleep(2 * (attempt + 1))
        branches = git('ls-remote', '--heads', REPO_URL + '.git', 'updates')
        git('clone', '--depth', '1', '--single-branch', '--branch', 'updates' if branches.strip() else 'main', REPO_URL + '.git', str(checkout))
        if not branches.strip():
            git('checkout', '-b', 'updates')
        (checkout / 'packages').mkdir(exist_ok=True)
        (checkout / 'packages' / package.name).write_bytes(data)
        (checkout / 'latest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        git('add', 'packages/' + package.name, 'latest.json')
        if git('diff', '--cached', '--name-only').strip():
            git('-c', 'user.name=Wardogs Release', '-c', 'user.email=release@users.noreply.github.com', 'commit', '-m', '发布更新包 ' + version)
            git('push', 'origin', 'HEAD:updates')
    print('Gitee fallback manifest published', flush=True)



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('version')
    parser.add_argument('package', type=Path)
    parser.add_argument('checksums', type=Path)
    parser.add_argument('notes', type=Path)
    parser.add_argument('--token-stdin', action='store_true')
    args = parser.parse_args()
    token = sys.stdin.readline().strip() if args.token_stdin else os.environ.get('GITEE_TOKEN')
    if not token:
        raise SystemExit('Configure GITEE_TOKEN in CI secrets or use --token-stdin.')
    publish(token, args.version, args.package, args.checksums, args.notes)
