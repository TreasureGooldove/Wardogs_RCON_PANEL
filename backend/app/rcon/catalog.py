"""Project map, experience and lighting catalogs to safe display rows."""

from typing import Any

from app.errors import PanelError


_KINDS = frozenset({"maps", "experiences", "lightings"})


def _valid_text(value: Any, limit: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and bool(value.strip())
        and all(ord(char) >= 32 for char in value)
    )


def normalize_catalog(raw: dict[str, Any] | list[Any], kind: str) -> dict[str, Any]:
    if kind not in _KINDS:
        raise PanelError("route_unsupported")
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = None
        for key in (kind, "items", "values", "data"):
            if key in raw:
                items = raw[key]
                break
    else:
        raise PanelError("invalid_upstream")
    if not isinstance(items, list):
        raise PanelError("invalid_upstream")

    projected: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, str):
            identifier = label = item
        elif isinstance(item, dict):
            identifier = next(
                (item[key] for key in ("id", "value", "name") if key in item),
                None,
            )
            label = next(
                (item[key] for key in ("displayName", "label", "name", "id") if key in item),
                identifier,
            )
        else:
            raise PanelError("invalid_upstream")
        if not _valid_text(identifier, 128) or not _valid_text(label, 160):
            raise PanelError("invalid_upstream")
        projected.append({"id": identifier, "label": label})
    return {"kind": kind, "items": projected}
