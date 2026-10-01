"""Lossless reserved-slot edits and bounded whole-document RCON calls.

The server's config PUT replaces every section. Reserved-slot changes therefore
start from a fresh GET and surgically replace only DefaultReservedPlayerIds lines.
Unknown keys, comments and ordering are left byte-for-byte unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.errors import PanelError

from .client import RconClient
from .routes import RouteName, WriteName, write_route_for


RESERVED_SECTION = "/Script/WDGame.WDGameSession"
RESERVED_KEY = "DefaultReservedPlayerIds"
BANNED_KEY = "DefaultBannedPlayerIds"
_STEAM_ID = re.compile(r"^[1-9][0-9]{16}$")
_CONFIG_ID = re.compile(r"^[0-9]{17}$")
_REVISION = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SECTION = re.compile(r"^\s*\[([^\]\r\n]+)\]\s*$")
_ASSIGNMENT = re.compile(r"^\s*([!+.\-]?)([^=\s]+)\s*=(.*)$")
_MAX_DOCUMENT_BYTES = 1_048_576


def valid_steam_id(value: str) -> str:
    if not isinstance(value, str) or _STEAM_ID.fullmatch(value) is None:
        raise PanelError("invalid_moderation_target")
    return value


def valid_revision(value: str) -> str:
    if not isinstance(value, str) or _REVISION.fullmatch(value) is None:
        raise PanelError("invalid_config")
    return value


def valid_document_text(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or len(value.encode("utf-8")) > _MAX_DOCUMENT_BYTES
    ):
        raise PanelError("invalid_config")
    return value


def _body(line: str) -> str:
    return line.rstrip("\r\n")


def _section_name(line: str) -> str | None:
    match = _SECTION.fullmatch(_body(line).lstrip("\ufeff"))
    return match.group(1).strip().casefold() if match else None


def _reserved_assignment(line: str) -> tuple[str, str] | None:
    stripped = _body(line).strip()
    if not stripped or stripped.startswith((";", "#")):
        return None
    match = _ASSIGNMENT.fullmatch(_body(line))
    if match and match.group(2).casefold() == RESERVED_KEY.casefold():
        return match.group(1), match.group(3).strip()
    return None


def _unquote_id(value: str) -> str:
    if not isinstance(value,str):
        raise PanelError("invalid_config")
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if _CONFIG_ID.fullmatch(value) is None:
        raise PanelError("invalid_config")
    return value


def reserved_ids_from_text(text: str) -> list[str]:
    """Evaluate Unreal .ini array operators in the target section only."""
    valid_document_text(text)
    in_section = False
    ids: list[str] = []
    for line in text.splitlines(keepends=True):
        section = _section_name(line)
        if section is not None:
            in_section = section == RESERVED_SECTION.casefold()
            continue
        if not in_section:
            continue
        entry = _reserved_assignment(line)
        if entry is None:
            continue
        operation, raw = entry
        if operation == "!":
            ids = []
            continue
        steam_id = _unquote_id(raw)
        if operation == "-":
            ids = [existing for existing in ids if existing != steam_id]
        elif operation == "":
            ids = [steam_id]
        elif operation == "+":
            if steam_id not in ids:
                ids.append(steam_id)
        else:  # '.' appends in the server's current config format.
            ids.append(steam_id)
    return list(dict.fromkeys(ids))


def banned_ids_from_text(text: str) -> list[str]:
    """Read +DefaultBannedPlayerIds entries without changing the document."""
    valid_document_text(text)
    in_section = False
    ids: list[str] = []
    for line in text.splitlines(keepends=True):
        section = _section_name(line)
        if section is not None:
            in_section = section == RESERVED_SECTION.casefold()
            continue
        if not in_section:
            continue
        match = _ASSIGNMENT.fullmatch(_body(line))
        if match is None or match.group(2).casefold() != BANNED_KEY.casefold():
            continue
        operation, raw = match.group(1), match.group(3).strip()
        if operation == "!":
            ids = []
            continue
        steam_id = _unquote_id(raw)
        if operation == "-":
            ids = [item for item in ids if item != steam_id]
        elif operation == "":
            ids = [steam_id]
        elif operation in {"+", "."} and steam_id not in ids:
            ids.append(steam_id)
    return ids


def replace_reserved_ids(text: str, ids: list[str]) -> str:
    """Replace only the target array, preserving all unrelated config text."""
    valid_document_text(text)
    if not isinstance(ids, list) or len(ids) > 10_000:
        raise PanelError("invalid_config")
    normalized = [_unquote_id(value) for value in ids]
    if len(set(normalized)) != len(normalized):
        raise PanelError("invalid_config")

    newline = "\r\n" if "\r\n" in text else ("\r" if "\r" in text and "\n" not in text else "\n")
    kept: list[str] = []
    in_section = False
    insertion: int | None = None
    for line in text.splitlines(keepends=True):
        section = _section_name(line)
        if section is not None:
            in_section = section == RESERVED_SECTION.casefold()
            kept.append(line)
            if in_section and insertion is None:
                insertion = len(kept)
            continue
        if in_section and _reserved_assignment(line) is not None:
            continue
        kept.append(line)

    canonical = [f"!{RESERVED_KEY}=ClearArray{newline}"]
    canonical.extend(f".{RESERVED_KEY}={steam_id}{newline}" for steam_id in normalized)
    if insertion is None:
        if kept and not kept[-1].endswith(("\r", "\n")):
            kept[-1] += newline
        if kept and _body(kept[-1]).strip():
            kept.append(newline)
        kept.append(f"[{RESERVED_SECTION}]{newline}")
        kept.extend(canonical)
    else:
        if insertion and not kept[insertion - 1].endswith(("\r", "\n")):
            kept[insertion - 1] += newline
        kept[insertion:insertion] = canonical
    result = "".join(kept)
    valid_document_text(result)
    return result


def _config_response(raw: dict[str, Any] | list[Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise PanelError("invalid_upstream")
    revision, writable, text = raw.get("revision"), raw.get("writable"), raw.get("text")
    sections, warnings = raw.get("sections", []), raw.get("warnings", [])
    if (
        not isinstance(revision, str)
        or not isinstance(writable, bool)
        or not isinstance(text, str)
        or not isinstance(sections, list)
        or not isinstance(warnings, list)
        or any(not isinstance(item, str) for item in warnings)
    ):
        raise PanelError("invalid_upstream")
    try:
        valid_revision(revision)
        valid_document_text(text)
    except PanelError as exc:
        raise PanelError("invalid_upstream") from exc
    return {
        "revision": revision,
        "writable": writable,
        "text": text,
        "sections": sections,
        "warnings": warnings,
    }


async def read_config(client: RconClient) -> dict[str, Any]:
    headers: dict[str, str] = {}
    raw = await client.request(RouteName.CONFIG, response_headers=headers)
    # Warcon also reads the same JSON document and falls back to its ETag.
    # Keep the returned text intact; a missing writable flag must not enable writes.
    if isinstance(raw, dict) and not raw.get("revision"):
        etag = headers.get("etag", "").strip()
        if etag.startswith("W/"):
            etag = etag[2:].strip()
        if len(etag) >= 2 and etag.startswith('"') and etag.endswith('"'):
            etag = etag[1:-1]
        raw = {**raw, "revision": etag}
    return _config_response(raw)


async def config_consistency(client: RconClient, document: dict[str, Any]) -> dict[str, Any]:
    """Cross-check the config ban array against the independent live ban route.

    Never synthesize missing lines: a mismatched or unverifiable document is
    diagnostic-only and cannot safely be used for a whole-document PUT.
    """
    configured = set(banned_ids_from_text(document["text"]))
    result = {"ok": False, "reason": "verification_unavailable",
              "configuredBannedCount": len(configured - {"00000000000000000"}),
              "liveBannedCount": None}
    try:
        raw = await client.request(RouteName.BANS)
        if not isinstance(raw, dict) or not isinstance(raw.get("bans"), list):
            return result
        live: set[str] = set()
        for item in raw["bans"]:
            if not isinstance(item, dict):
                return result
            steam_id = item.get("steamId")
            if not isinstance(steam_id, str) or _CONFIG_ID.fullmatch(steam_id) is None:
                return result
            live.add(steam_id)
        result.update(ok=configured == live,
                      reason=None if configured == live else "ban_list_mismatch",
                      liveBannedCount=len(live - {"00000000000000000"}))
    except PanelError:
        pass
    return result


async def require_consistent_config(client: RconClient, document: dict[str, Any]) -> None:
    if not (await config_consistency(client, document))["ok"]:
        raise PanelError("config_interface_inconsistent")


async def read_reserved(client: RconClient) -> list[str]:
    raw = await client.request(RouteName.RESERVED_SLOTS)
    if not isinstance(raw, dict) or not isinstance(raw.get("reservedSlots"), list):
        raise PanelError("invalid_upstream")
    ids = raw["reservedSlots"]
    try:
        normalized = [_unquote_id(value) for value in ids]
    except PanelError as exc:
        raise PanelError("invalid_upstream") from exc
    # The live Unreal array can include duplicate entries. Treat reserved players
    # as a set like Warcon's observer, retaining the server's first-seen order.
    return list(dict.fromkeys(normalized))


async def send_config(
    client: RconClient,
    text: str,
    *,
    apply: bool,
    revision: str | None = None,
    full_apply: bool = False,
) -> dict[str, Any]:
    """Submit one text/plain request. Never retry an uncertain PUT."""
    text = valid_document_text(text)
    if apply:
        revision = valid_revision(revision)
        current = await read_config(client)
        if current["revision"] != revision:
            raise PanelError("config_conflict")
        # Use the config endpoint's revision independently of runtime lists.
        spec = write_route_for(WriteName.CONFIG_APPLY)
    else:
        spec = write_route_for(WriteName.CONFIG_VALIDATE)
    http_client = client._get_client()
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if revision is not None and apply:
        headers["If-Match"] = f'"{revision}"'
    path = spec.path + ("?fullApply=true" if apply and full_apply else "")
    try:
        async with client._semaphore:
            async with http_client.stream(
                spec.method, path, content=text.encode("utf-8"), headers=headers
            ) as response:
                if response.status_code in (401, 403):
                    raise PanelError("rcon_auth_failed")
                if response.status_code == 404:
                    raise PanelError("action_unsupported")
                if response.status_code == 429:
                    raise PanelError("rcon_rate_limited")
                if response.status_code == 412:
                    raise PanelError("config_conflict")
                if response.status_code == 422:
                    raise PanelError("invalid_config")
                if response.status_code >= 500:
                    raise PanelError("action_uncertain" if apply else "rcon_unavailable")
                if not 200 <= response.status_code < 300:
                    raise PanelError("action_rejected")
                assert client.target is not None
                limit = client.target.max_response_bytes
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65_536):
                    if len(body) + len(chunk) > limit:
                        raise PanelError("action_uncertain" if apply else "invalid_upstream")
                    body.extend(chunk)
    except (httpx.TimeoutException, httpx.TransportError, httpx.DecodingError) as exc:
        raise PanelError("action_uncertain" if apply else "rcon_unavailable") from exc
    try:
        result = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise PanelError("action_uncertain" if apply else "invalid_upstream") from exc
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise PanelError("action_uncertain" if apply else "invalid_upstream")
    if apply and result["ok"] is not True:
        raise PanelError("action_rejected")
    return result
