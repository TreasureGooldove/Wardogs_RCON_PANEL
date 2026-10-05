"""Fixed, single-attempt Wardogs management commands beyond kick and ban."""

from dataclasses import dataclass
import logging
import re
from time import monotonic

import httpx

from app.errors import PanelError

from .client import RconClient
from .reference_reads import valid_map_id
from .routes import WriteName, write_route_for


_LOG = logging.getLogger(__name__)
_STEAM_ID = re.compile(r"^[1-9][0-9]{16}$")
_ACTIONS = frozenset({
    WriteName.UNBAN, WriteName.KILL, WriteName.MESSAGE,
    WriteName.CHANGE_FACTION, WriteName.BROADCAST,
    WriteName.CHANGE_MAP, WriteName.END_MATCH, WriteName.RESTART_MATCH,
    WriteName.SET_LIGHTING,
})
_PLAYER_ACTIONS = frozenset({
    WriteName.UNBAN, WriteName.KILL, WriteName.MESSAGE, WriteName.CHANGE_FACTION,
})


def valid_steam_id(value: str) -> str:
    if not isinstance(value, str) or _STEAM_ID.fullmatch(value) is None:
        raise PanelError("invalid_moderation_target")
    return value


def _short_text(value: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise PanelError("invalid_selection")
    value = value.strip()
    if not value or len(value) > max_length or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PanelError("invalid_selection")
    return value


def valid_lighting(value: str) -> str:
    return _short_text(value, max_length=128)


@dataclass(frozen=True)
class MapSelection:
    map: str
    experiences: tuple[str, ...] = ()
    lighting: str | None = None
    zone_alternator: str | None = None

    def body(self) -> dict[str, object]:
        result: dict[str, object] = {"map": valid_map_id(self.map)}
        if len(self.experiences) > 16:
            raise PanelError("invalid_selection")
        experiences = [_short_text(value, max_length=128) for value in self.experiences]
        if len(experiences) != len(set(experiences)):
            raise PanelError("invalid_selection")
        if experiences:
            result["experiences"] = experiences
        if self.lighting:
            result["lighting"] = _short_text(self.lighting, max_length=128)
        if self.zone_alternator and self.zone_alternator != "None":
            result["zoneAlternator"] = _short_text(self.zone_alternator, max_length=128)
        return result


class ActionService:
    def __init__(self, client: RconClient) -> None:
        self.client = client

    async def send(
        self,
        action: WriteName,
        *,
        steam_id: str | None = None,
        message: str | None = None,
        faction: str | None = None,
        selection: MapSelection | None = None,
        lighting: str | None = None,
    ) -> None:
        if action not in _ACTIONS:
            raise PanelError("action_unsupported")
        if action in _PLAYER_ACTIONS:
            if steam_id is None:
                raise PanelError("invalid_moderation_target")
            valid_steam_id(steam_id)
        elif steam_id is not None:
            raise PanelError("invalid_moderation_target")
        if action in {WriteName.MESSAGE, WriteName.BROADCAST}:
            if message is None:
                raise PanelError("invalid_selection")
            body: dict[str, object] | None = {"message": _short_text(message, max_length=200)}
        elif message is not None:
            raise PanelError("invalid_selection")
        else:
            body = None
        if action is WriteName.CHANGE_FACTION:
            if faction is None:
                raise PanelError("invalid_selection")
            body = {"faction": _short_text(faction, max_length=64)}
        elif faction is not None:
            raise PanelError("invalid_selection")
        if action is WriteName.CHANGE_MAP:
            if selection is None:
                raise PanelError("invalid_selection")
            body = selection.body()
        elif selection is not None:
            raise PanelError("invalid_selection")
        if action is WriteName.SET_LIGHTING:
            if lighting is None:
                raise PanelError("invalid_selection")
            body = {"lighting": valid_lighting(lighting)}
        elif lighting is not None:
            raise PanelError("invalid_selection")

        spec = write_route_for(action)
        path = spec.path.replace("{steamId}", steam_id or "")
        started = monotonic()
        try:
            async with self.client.exchange(spec.method, path, json=body) as response:
                if action is WriteName.UNBAN and response.status_code == 404:
                    raise PanelError("action_rejected")
                await self.client._decode_write(response)
        except (httpx.TimeoutException, httpx.TransportError, httpx.DecodingError) as exc:
            _LOG.warning("Wardogs RCON action uncertain: action=%s duration_ms=%d",
                         action.value, round((monotonic() - started) * 1000))
            raise PanelError("action_uncertain") from exc
        _LOG.info("Wardogs RCON action accepted: action=%s duration_ms=%d",
                  action.value, round((monotonic() - started) * 1000))
