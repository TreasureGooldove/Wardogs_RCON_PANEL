"""Reference-console reads and explicit owner-only management actions."""

from datetime import UTC, datetime
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.api.auth import require_admin
from app.errors import PanelError
from app.rcon.actions import ActionService, MapSelection, valid_lighting, valid_steam_id
from app.rcon.config_doc import banned_ids_from_text, read_config
from app.rcon.reference_reads import ReferenceReadService, valid_map_id
from app.rcon.routes import RouteName, WriteName, route_for, write_route_for
from app.storage.db import AdminAccount


router = APIRouter(prefix="/api/server", tags=["actions"])
_LOG = logging.getLogger(__name__)


class RevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targetRevision: str = Field(min_length=1, max_length=128)


class PlayerBody(RevisionBody):
    steamId: str


class MessageBody(PlayerBody):
    message: str = Field(min_length=1, max_length=200)


class FactionBody(PlayerBody):
    faction: str = Field(min_length=1, max_length=64)
    respawn: StrictBool = False


class BroadcastBody(RevisionBody):
    message: str = Field(min_length=1, max_length=200)


class MapBody(RevisionBody):
    map: str = Field(min_length=1, max_length=96)
    experiences: list[str] = Field(default_factory=list, max_length=16)
    lighting: str | None = None
    zoneAlternator: str | None = None


class LightingBody(RevisionBody):
    lighting: str = Field(min_length=1, max_length=128)


class WarningBody(PlayerBody):
    reason: str = Field(min_length=1, max_length=193)


def _warning_reason(value: str) -> str:
    reason = value.strip()
    if not reason or len(reason) > 193 or any(ord(char) < 32 or ord(char) == 127 for char in reason):
        raise PanelError("invalid_moderation_reason")
    return reason


def _audit(
    request: Request,
    admin: AdminAccount,
    action: WriteName | str,
    outcome: str,
    steam_id: str,
    revision: str,
    origin: str,
    reason: str = "",
) -> bool:
    action_name = action.value if isinstance(action, WriteName) else action
    try:
        request.app.state.database.append_moderation_audit(
            admin.id, action_name, steam_id, outcome, reason,
            request.state.request_id, revision, origin,
        )
    except Exception:
        # The command may already have reached the game; never trigger a retry.
        _LOG.error("Action audit append failed: action=%s outcome=%s", action_name, outcome)
        return False
    return True


async def _read(
    request: Request,
    route: RouteName,
    *,
    map_id: str | None = None,
    audit_limit: int | None = None,
) -> dict[str, Any]:
    runtime = request.app.state.rcon_runtime
    if map_id is not None:
        valid_map_id(map_id)
    async with runtime.lock:
        spec = route_for(route)
        await runtime.capabilities.require_advertised(spec.method, spec.path)
        data = await ReferenceReadService(runtime.client).fetch(
            route, map_id=map_id, audit_limit=audit_limit,
        )
        data["targetRevision"] = runtime.target_revision
        return data


async def _write(
    request: Request,
    admin: AdminAccount,
    action: WriteName,
    target_revision: str,
    *,
    steam_id: str | None = None,
    message: str | None = None,
    faction: str | None = None,
    selection: MapSelection | None = None,
    respawn: bool = False,
    lighting: str | None = None,
    audit_action: str | None = None,
    audit_reason: str = "",
    verify_online: bool = False,
) -> dict[str, Any]:
    auth = request.app.state.auth_service
    auth.check_origin(request)
    if steam_id is not None:
        valid_steam_id(steam_id)
    if verify_online and steam_id is None:
        raise PanelError("invalid_moderation_target")
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        # Recheck after any queued settings or account change before a command.
        auth.require_permission(request, audit_action or action.value)
        if target_revision != runtime.target_revision:
            raise PanelError("stale_server_target")
        target = runtime.client.target
        if target is None:
            raise PanelError("rcon_unconfigured")
        revision = runtime.target_revision
        origin = target.origin
        spec = write_route_for(action)
        try:
            if action is WriteName.SET_LIGHTING:
                lighting = valid_lighting(lighting)
            if action is WriteName.CHANGE_FACTION:
                status = await runtime.read_service.status()
                scores = status.get("factionScores")
                names = {
                    row.get("name") for row in scores if isinstance(row, dict)
                } if isinstance(scores, list) else set()
                if status.get("stale") or faction not in names:
                    raise PanelError("invalid_selection")
            await runtime.capabilities.require_advertised(spec.method, spec.path)
            if verify_online:
                runtime.invalidate_players_unlocked()
                players = await runtime.read_service.players()
                if players.get("stale") or not any(
                    player.get("steamId") == steam_id
                    for player in players.get("players", []) if isinstance(player, dict)
                ):
                    raise PanelError("player_not_online")
            if action is WriteName.SET_LIGHTING:
                catalog = await runtime.read_service.catalog("lightings")
                options = catalog.get("items")
                identifiers = {
                    row.get("id") for row in options if isinstance(row, dict)
                } if isinstance(options, list) else set()
                if catalog.get("stale") or lighting not in identifiers:
                    raise PanelError("invalid_selection")
            await ActionService(runtime.client).send(
                action, steam_id=steam_id, message=message,
                faction=faction, selection=selection, lighting=lighting,
            )
        except PanelError as exc:
            _audit(request, admin, audit_action or action,
                   "uncertain" if exc.code == "action_uncertain" else "rejected",
                   steam_id or "", revision, origin, audit_reason)
            raise
        recorded = _audit(request, admin, audit_action or action, "accepted",
                          steam_id or "", revision, origin, audit_reason)
        if action in {WriteName.UNBAN, WriteName.KILL, WriteName.CHANGE_FACTION,
                      WriteName.CHANGE_MAP, WriteName.END_MATCH, WriteName.RESTART_MATCH,
                      WriteName.SET_LIGHTING}:
            if action is not WriteName.SET_LIGHTING:
                runtime.invalidate_players_unlocked()
            runtime.read_service.invalidate(RouteName.STATUS)
        if action is WriteName.CHANGE_FACTION:
            if not respawn:
                return {"ok": True, "respawned": None}
            kill_spec = write_route_for(WriteName.KILL)
            try:
                await runtime.capabilities.require_advertised(kill_spec.method, kill_spec.path)
                await ActionService(runtime.client).send(WriteName.KILL, steam_id=steam_id)
            except PanelError as exc:
                _audit(request, admin, WriteName.KILL,
                       "uncertain" if exc.code == "action_uncertain" else "rejected",
                       steam_id or "", revision, origin)
                if exc.code == "action_uncertain":
                    return {
                        "ok": True,
                        "respawned": None,
                        "respawnUncertain": True,
                        "respawnError": exc.code,
                    }
                return {"ok": True, "respawned": False, "respawnError": exc.code}
            _audit(request, admin, WriteName.KILL, "accepted", steam_id or "", revision, origin)
            return {"ok": True, "respawned": True}
        return {"ok": True, "recorded": recorded} if audit_action else {"ok": True}


@router.get("/bans")
async def get_bans(request: Request, _admin: AdminAccount = Depends(require_admin)) -> dict[str, Any]:
    result = await _read(request, RouteName.BANS)
    runtime = request.app.state.rcon_runtime
    try:
        async with runtime.lock:
            config_spec = route_for(RouteName.CONFIG)
            await runtime.capabilities.require_advertised(config_spec.method, config_spec.path)
            document = await read_config(runtime.client)
            configured = banned_ids_from_text(document["text"])
    except PanelError:
        configured = []
    known = {item["steamId"] for item in result["bans"]}
    result["bans"].extend(
        {"steamId": steam_id, "bannedAtUtc": None, "bannedBy": None,
         "reason": "服务器配置预设封禁", "source": "config"}
        for steam_id in configured if steam_id not in known
    )
    return result


@router.get("/audit")
async def get_audit(
    request: Request,
    limit: int = Query(default=50),
    _admin: AdminAccount = Depends(require_admin),
) -> dict[str, Any]:
    return await _read(request, RouteName.AUDIT, audit_limit=limit)


@router.get("/catalog/maps/experiences")
async def get_map_experiences(
    request: Request,
    map: str = Query(min_length=1, max_length=96),
    _admin: AdminAccount = Depends(require_admin),
) -> dict[str, Any]:
    return await _read(request, RouteName.MAP_EXPERIENCES, map_id=map)


@router.get("/catalog/maps/alternators")
async def get_map_alternators(
    request: Request,
    map: str = Query(min_length=1, max_length=96),
    _admin: AdminAccount = Depends(require_admin),
) -> dict[str, Any]:
    return await _read(request, RouteName.MAP_ALTERNATORS, map_id=map)


@router.post("/unbans")
async def unban(
    payload: PlayerBody, request: Request, owner: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    return await _write(request, owner, WriteName.UNBAN, payload.targetRevision, steam_id=payload.steamId)


@router.post("/kills")
async def kill_player(
    payload: PlayerBody, request: Request, owner: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    return await _write(request, owner, WriteName.KILL, payload.targetRevision, steam_id=payload.steamId)


@router.post("/messages")
async def message_player(
    payload: MessageBody, request: Request, owner: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    return await _write(request, owner, WriteName.MESSAGE, payload.targetRevision,
                        steam_id=payload.steamId, message=payload.message)


@router.post("/factions")
async def change_faction(
    payload: FactionBody, request: Request, owner: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    return await _write(request, owner, WriteName.CHANGE_FACTION, payload.targetRevision,
                        steam_id=payload.steamId, faction=payload.faction, respawn=payload.respawn)


@router.post("/broadcast")
async def broadcast(
    payload: BroadcastBody, request: Request, owner: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    return await _write(request, owner, WriteName.BROADCAST, payload.targetRevision,
                        message=payload.message)


@router.post("/match/end")
async def end_match(
    payload: RevisionBody, request: Request, owner: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    return await _write(request, owner, WriteName.END_MATCH, payload.targetRevision)


@router.post("/match/restart")
async def restart_match(
    payload: RevisionBody, request: Request, owner: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    return await _write(request, owner, WriteName.RESTART_MATCH, payload.targetRevision)


@router.post("/match/map")
async def change_map(
    payload: MapBody, request: Request, owner: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    selection = MapSelection(
        map=payload.map,
        experiences=tuple(payload.experiences),
        lighting=payload.lighting,
        zone_alternator=payload.zoneAlternator,
    )
    return await _write(request, owner, WriteName.CHANGE_MAP, payload.targetRevision,
                        selection=selection)


@router.put("/world/lighting")
async def set_lighting(
    payload: LightingBody, request: Request, owner: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    return await _write(request, owner, WriteName.SET_LIGHTING, payload.targetRevision,
                        lighting=payload.lighting)


@router.post("/warnings")
async def warn_player(
    payload: WarningBody, request: Request, owner: AdminAccount = Depends(require_admin)
) -> dict[str, Any]:
    request.app.state.auth_service.check_origin(request)
    reason = _warning_reason(payload.reason)
    return await _write(
        request, owner, WriteName.MESSAGE, payload.targetRevision,
        steam_id=payload.steamId, message=f"【管理员警告】{reason}",
        audit_action="warning", audit_reason=reason, verify_online=True,
    )


@router.get("/warnings")
async def get_warnings(
    request: Request, response: Response, steamId: str,
    _admin: AdminAccount = Depends(require_admin),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    steam_id = valid_steam_id(steamId)
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        target = runtime.client.target
        if target is None:
            raise PanelError("rcon_unconfigured")
        count, entries = request.app.state.database.warning_history(steam_id, target.origin)
        return {
            "steamId": steam_id,
            "count": count,
            "entries": entries,
            "source": "panel_local",
            "targetRevision": runtime.target_revision,
            "observedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "stale": False,
        }
