"""Unprivileged update queue and release staging. No Docker socket in panel."""
import argparse
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import time
import tomllib
from uuid import uuid4
import zipfile

from app.config import load_settings
from app.release_sources import MAX_PACKAGE, ReleaseChecker, version_tuple
from app.errors import PanelError


def directory(settings):
    return settings.db_path.parent / "updates"


def read_json(path, default=None):
    try:
        if path.is_symlink() or path.stat().st_size > 32768:
            return default
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def atomic_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def update_state(settings):
    root = directory(settings)
    heartbeat = read_json(root / "agent.json", {})
    stamp = heartbeat.get("at", 0) if isinstance(heartbeat, dict) else 0
    policy = read_json(root / "policy.json", {})
    job = read_json(root / "status.json", {})
    pending = read_json(root / "request.json")
    return {"agentAvailable": isinstance(stamp, (int, float)) and 0 <= time.time() - stamp < 180,
            "policy": {"autoInstall": False, "source": "auto", **(policy if isinstance(policy, dict) else {})},
            "job": {**pending, "state": "queued"} if isinstance(pending, dict) else job if isinstance(job, dict) and job else {"state": "idle"}}


def enqueue(settings, source, version):
    root = directory(settings)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = update_state(settings)
    if not state["agentAvailable"]:
        raise PanelError("updater_unavailable")
    if state["job"].get("state") in {"queued", "downloading", "building", "installing"}:
        raise PanelError("update_busy")
    request = {"id": uuid4().hex, "source": source, "version": version}
    # Exclusive creation prevents two simultaneous authenticated requests.
    try:
        with (root / "request.json").open("x", encoding="utf-8") as handle:
            json.dump(request, handle)
    except FileExistsError as exc:
        raise PanelError("update_busy") from exc
    return {**request, "state": "queued"}


def unpack(data, target, version):
    """Reject traversal, symlinks, duplicate names, bombs and wrong versions."""
    if target.exists():
        raise ValueError("stage_exists")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set()
        size = 0
        selected = []
        for item in archive.infolist():
            path = PurePosixPath(item.filename)
            if (item.filename in names or path.is_absolute() or ".." in path.parts or "\\" in item.filename
                    or "\x00" in item.filename or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("unsafe_package")
            names.add(item.filename)
            size += item.file_size
            if size > 200 * 1024 * 1024 or len(names) > 10000:
                raise ValueError("package_too_large")
            if (item.filename.startswith(("backend/app/", "frontend/dist/"))
                    or item.filename == "backend/pyproject.toml"):
                if not item.is_dir():
                    selected.append(item)
        project = tomllib.loads(archive.read("backend/pyproject.toml").decode("utf-8-sig"))
        if version_tuple(project["project"]["version"]) != version_tuple(version):
            raise ValueError("package_version_mismatch")
        if "frontend/dist/index.html" not in names or "backend/app/main.py" not in names:
            raise ValueError("incomplete_package")
        target.mkdir(parents=True, mode=0o700)
        try:
            for item in selected:
                path = target / item.filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(item))
        except Exception:
            shutil.rmtree(target)
            raise


async def stage(source, version, job_id):
    if not re.fullmatch(r"[0-9a-f]{32}", job_id) or not version_tuple(version):
        raise ValueError("invalid_job")
    checker = ReleaseChecker()
    try:
        release = await checker.check(source=source)
        if not release["installable"] or release["latestVersion"] != version or not release["updateAvailable"]:
            raise ValueError("release_changed_or_not_newer")
        data = await checker.fetch(release["packageUrl"], MAX_PACKAGE, source=release["source"])
        if hashlib.sha256(data).hexdigest() != release["sha256"]:
            raise ValueError("checksum_mismatch")
        target = directory(load_settings()) / job_id / "stage"
        unpack(data, target, version)
        print(json.dumps({"staged": True, "version": version, "source": release["source"]}))
    finally:
        await checker.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["auto", "github", "gitee"], default="auto")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--version")
    parser.add_argument("--job-id")
    args = parser.parse_args()
    async def run():
        if args.check:
            checker = ReleaseChecker()
            try:
                print(json.dumps(await checker.check(source=args.source)))
            finally:
                await checker.close()
        else:
            await stage(args.source, args.version, args.job_id)
    asyncio.run(run())
