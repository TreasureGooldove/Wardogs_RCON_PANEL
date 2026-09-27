"""Hide credential assignments in config drafts and restore unchanged slots safely.

The browser receives a revision-bound marker, never the original value. A
validate/apply operation fetches the current document and can restore a marker
only at the same section, assignment key/operator, and occurrence. Moving or
deleting a hidden assignment fails closed instead of silently clearing it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Any

from app.errors import PanelError

from .config_doc import valid_document_text, valid_revision


_SECTION = re.compile(r"^[ \t]*\[([^\]\r\n]+)\][ \t]*$")
_ASSIGNMENT = re.compile(
    r"^(?P<leading>[ \t\ufeff]*)(?P<operator>[!+.\-]?)(?P<key>[^=\s]+)"
    r"(?P<separator>[ \t]*=[ \t]*)(?P<value>.*)$"
)
_EMBEDDED_ASSIGNMENT = re.compile(r"([A-Za-z0-9_.\-]+)[ \t]*=")
_MARKER = re.compile(r"__WD_REDACTED_[A-Za-z0-9._:-]+__")
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9_./+:-]{1,128}$")
_SECRET_WORDS = (
    "password", "passwd", "passphrase", "secret", "token", "bearer",
    "credential", "apikey", "accesskey", "privatekey", "authkey",
)


def _secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return normalized == "key" or any(word in normalized for word in _SECRET_WORDS)


def _sensitive_comment(body: str) -> bool:
    stripped = body.lstrip(" \t\ufeff")
    if not stripped.startswith((";", "#")):
        return False
    normalized = re.sub(r"[^a-z0-9]", "", stripped.casefold())
    return any(word in normalized for word in _SECRET_WORDS) or "__WD_REDACTED_" in body


def _body_and_ending(line: str) -> tuple[str, str]:
    body = line.rstrip("\r\n")
    return body, line[len(body):]


@dataclass(frozen=True)
class _Line:
    body: str
    ending: str
    identity: tuple[str, str, str, int] | None
    head: str | None
    value: str | None
    comment: bool = False


def _scan(text: str) -> list[_Line]:
    section = ""
    occurrences: dict[tuple[str, str, str], int] = defaultdict(int)
    comment_counts: dict[str, int] = defaultdict(int)
    lines: list[_Line] = []
    for raw_line in text.splitlines(keepends=True):
        body, ending = _body_and_ending(raw_line)
        section_match = _SECTION.fullmatch(body.lstrip("\ufeff"))
        if section_match:
            section = section_match.group(1).strip().casefold()
            lines.append(_Line(body, ending, None, None, None))
            continue
        assignment = _ASSIGNMENT.fullmatch(body)
        if assignment and not body.lstrip(" \t\ufeff").startswith((";", "#")):
            operator = assignment.group("operator")
            key = assignment.group("key").casefold()
            base = (section, operator, key)
            index = occurrences[base]
            occurrences[base] += 1
            value = assignment.group("value")
            nested = any(
                _secret_key(match.group(1))
                for match in _EMBEDDED_ASSIGNMENT.finditer(value)
            )
            if _secret_key(key) or nested or _MARKER.fullmatch(value):
                head = body[:assignment.start("value")]
                lines.append(_Line(body, ending, (*base, index), head, value))
                continue
        if _sensitive_comment(body):
            index = comment_counts[section]
            comment_counts[section] += 1
            lines.append(_Line(body, ending, (section, "#", "comment", index), None, None, True))
            continue
        lines.append(_Line(body, ending, None, None, None))
    return lines


def _marker(revision: str) -> str:
    return f"__WD_REDACTED_{valid_revision(revision)}__"


def mask_config(text: str, revision: str) -> str:
    """Produce a safe editable draft without any known secret assignment values."""
    valid_document_text(text)
    marker = _marker(revision)
    output: list[str] = []
    for line in _scan(text):
        if line.identity is None:
            output.append(line.body + line.ending)
        elif line.comment:
            prefix = line.body[:len(line.body) - len(line.body.lstrip(" \t\ufeff"))]
            comment_mark = line.body.lstrip(" \t\ufeff")[0]
            output.append(f"{prefix}{comment_mark} {marker}{line.ending}")
        else:
            assert line.head is not None
            output.append(
                f"{line.head}{marker}{line.ending}"
                if line.value and line.value.strip().strip('"') else line.body + line.ending
            )
    masked = "".join(output)
    valid_document_text(masked)
    return masked


def restore_config(submitted: str, current: str, revision: str) -> str:
    """Restore markers from current text only at their original field identity."""
    valid_document_text(submitted)
    valid_document_text(current)
    marker = _marker(revision)
    original = {line.identity: line for line in _scan(current) if line.identity is not None}
    seen: set[tuple[str, str, str, int]] = set()
    output: list[str] = []
    for line in _scan(submitted):
        identity = line.identity
        source = original.get(identity) if identity is not None else None
        if source is not None:
            seen.add(identity)
        if source is not None and source.comment:
            comment_value = line.body.lstrip(" \t\ufeff")[1:].strip() if line.comment else ""
            if comment_value == marker:
                output.append(source.body + line.ending)
                continue
        elif source is not None and not source.comment and line.value == marker:
            assert line.head is not None and source.value is not None
            output.append(line.head + source.value + line.ending)
            continue
        if _MARKER.search(line.body):
            raise PanelError("invalid_config")
        output.append(line.body + line.ending)
    if original.keys() - seen:
        raise PanelError("invalid_config")
    restored = "".join(output)
    valid_document_text(restored)
    return restored


def public_config_document(document: dict[str, Any]) -> dict[str, Any]:
    """Do not proxy arbitrary upstream sections or warning strings to a browser."""
    return {
        "revision": document["revision"],
        "writable": document["writable"],
        "text": mask_config(document["text"], document["revision"]),
        "sections": [],
        "warnings": (["服务器返回配置警告，详细内容已隐藏"] if document["warnings"] else []),
    }


def _label(value: Any) -> str | None:
    return value if isinstance(value, str) and _SAFE_LABEL.fullmatch(value) else None


def _visible_labels(text: str) -> tuple[set[str], set[str]]:
    sections = {""}
    keys: set[str] = set()
    for line in text.splitlines():
        section = _SECTION.fullmatch(line.lstrip("\ufeff"))
        if section is not None:
            sections.add(section.group(1).strip().casefold())
            continue
        assignment = _ASSIGNMENT.fullmatch(line)
        if assignment and not line.lstrip(" \t\ufeff").startswith((";", "#")):
            keys.add(assignment.group("key").casefold())
    return sections, keys


def public_config_result(result: dict[str, Any], visible_text: str) -> dict[str, Any]:
    """Keep validation metadata, never pass upstream freeform text to debug UI."""
    projected: dict[str, Any] = {"ok": result["ok"]}
    visible_sections, visible_keys = _visible_labels(visible_text)
    revision = result.get("revision")
    if _label(revision) is not None:
        projected["revision"] = revision
    errors = result.get("errors")
    if isinstance(errors, list):
        public_errors = []
        for item in errors[:100]:
            if isinstance(item, dict):
                section = _label(item.get("section"))
                key = _label(item.get("key"))
                public_errors.append({
                    "section": section if section and section.casefold() in visible_sections else None,
                    "key": key if key and key.casefold() in visible_keys else None,
                    "code": "validation_error",
                    "message": "配置项验证失败；详细内容已隐藏",
                })
            else:
                public_errors.append({"message": "配置项验证失败；详细内容已隐藏"})
        projected["errors"] = public_errors
    else:
        projected["errors"] = []
    projected["warnings"] = ["服务器返回配置警告，详细内容已隐藏"] if result.get("warnings") else []
    projected["changed"] = []
    projected["outcomes"] = []
    return projected
