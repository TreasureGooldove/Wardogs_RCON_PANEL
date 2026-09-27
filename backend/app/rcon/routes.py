"""Fixed upstream request allowlists for read and moderation operations.

Never construct an upstream path from an HTTP request or a user supplied value.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class RouteName(StrEnum):
    CAPABILITIES = "capabilities"
    STATUS = "status"
    PLAYERS = "players"
    ROTATION = "rotation"
    MAPS = "maps"
    EXPERIENCES = "experiences"
    LIGHTINGS = "lightings"
    CONFIG = "config"
    RESERVED_SLOTS = "reservedSlots"
    BANS = "bans"
    AUDIT = "audit"
    SPONSOR = "sponsor"
    SERVER_ID = "serverId"
    HEALTH = "health"
    MAP_EXPERIENCES = "mapExperiences"
    MAP_ALTERNATORS = "mapAlternators"


class WriteName(StrEnum):
    KICK = "kick"
    BAN = "ban"
    UNBAN = "unban"
    KILL = "kill"
    MESSAGE = "message"
    CHANGE_FACTION = "changeFaction"
    BROADCAST = "broadcast"
    CHANGE_MAP = "changeMap"
    END_MATCH = "endMatch"
    RESTART_MATCH = "restartMatch"
    SET_LIGHTING = "setLighting"
    CONFIG_VALIDATE = "configValidate"
    CONFIG_APPLY = "configApply"


@dataclass(frozen=True, slots=True)
class RouteSpec:
    name: RouteName
    path: str
    method: str = "GET"

    @property
    def capability_keys(self) -> tuple[str, ...]:
        return (self.path, f"GET {self.path}", f"get {self.path}")


@dataclass(frozen=True, slots=True)
class WriteSpec:
    name: WriteName
    path: str
    method: str = "POST"

    @property
    def capability_key(self) -> str:
        return f"{self.method} {self.path}"


ROUTES: Mapping[RouteName, RouteSpec] = MappingProxyType(
    {
        RouteName.CAPABILITIES: RouteSpec(RouteName.CAPABILITIES, "/v1/capabilities"),
        RouteName.STATUS: RouteSpec(RouteName.STATUS, "/v1/status"),
        RouteName.PLAYERS: RouteSpec(RouteName.PLAYERS, "/v1/players"),
        RouteName.ROTATION: RouteSpec(RouteName.ROTATION, "/v1/rotation"),
        RouteName.MAPS: RouteSpec(RouteName.MAPS, "/v1/catalog/maps"),
        RouteName.EXPERIENCES: RouteSpec(
            RouteName.EXPERIENCES, "/v1/catalog/experiences"
        ),
        RouteName.LIGHTINGS: RouteSpec(RouteName.LIGHTINGS, "/v1/catalog/lightings"),
        RouteName.CONFIG: RouteSpec(RouteName.CONFIG, "/v1/config"),
        RouteName.RESERVED_SLOTS: RouteSpec(RouteName.RESERVED_SLOTS, "/v1/reserved-slots"),
        RouteName.BANS: RouteSpec(RouteName.BANS, "/v1/bans"),
        RouteName.AUDIT: RouteSpec(RouteName.AUDIT, "/v1/audit"),
        RouteName.SPONSOR: RouteSpec(RouteName.SPONSOR, "/v1/sponsor"),
        RouteName.SERVER_ID: RouteSpec(RouteName.SERVER_ID, "/v1/server-id"),
        RouteName.HEALTH: RouteSpec(RouteName.HEALTH, "/v1/health"),
        RouteName.MAP_EXPERIENCES: RouteSpec(
            RouteName.MAP_EXPERIENCES, "/v1/catalog/maps/{id}/experiences"
        ),
        RouteName.MAP_ALTERNATORS: RouteSpec(
            RouteName.MAP_ALTERNATORS, "/v1/catalog/maps/{id}/alternators"
        ),
    }
)

WRITE_ROUTES: Mapping[WriteName, WriteSpec] = MappingProxyType(
    {
        WriteName.KICK: WriteSpec(WriteName.KICK, "/v1/players/{steamId}/kick"),
        WriteName.BAN: WriteSpec(WriteName.BAN, "/v1/bans"),
        WriteName.UNBAN: WriteSpec(WriteName.UNBAN, "/v1/bans/{steamId}", "DELETE"),
        WriteName.KILL: WriteSpec(WriteName.KILL, "/v1/players/{steamId}/kill"),
        WriteName.MESSAGE: WriteSpec(WriteName.MESSAGE, "/v1/players/{steamId}/message"),
        WriteName.CHANGE_FACTION: WriteSpec(
            WriteName.CHANGE_FACTION, "/v1/players/{steamId}", "PATCH"
        ),
        WriteName.BROADCAST: WriteSpec(WriteName.BROADCAST, "/v1/broadcast"),
        WriteName.CHANGE_MAP: WriteSpec(WriteName.CHANGE_MAP, "/v1/match/map"),
        WriteName.END_MATCH: WriteSpec(WriteName.END_MATCH, "/v1/match/end"),
        WriteName.RESTART_MATCH: WriteSpec(WriteName.RESTART_MATCH, "/v1/match/restart"),
        WriteName.SET_LIGHTING: WriteSpec(
            WriteName.SET_LIGHTING, "/v1/world/lighting", "PUT"
        ),
        WriteName.CONFIG_VALIDATE: WriteSpec(
            WriteName.CONFIG_VALIDATE, "/v1/config/validate"
        ),
        WriteName.CONFIG_APPLY: WriteSpec(WriteName.CONFIG_APPLY, "/v1/config", "PUT"),
    }
)


def route_for(name: RouteName) -> RouteSpec:
    from app.errors import PanelError

    if not isinstance(name, RouteName):
        raise PanelError("route_unsupported")
    return ROUTES[name]


def write_route_for(name: WriteName) -> WriteSpec:
    from app.errors import PanelError

    if not isinstance(name, WriteName):
        raise PanelError("action_unsupported")
    return WRITE_ROUTES[name]
