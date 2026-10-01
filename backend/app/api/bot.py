"""Isolated bot credentials, personal projections and durable ban receipts."""
from datetime import UTC, datetime
from hashlib import sha256
import json
import secrets
from time import monotonic
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.errors import PanelError
from app.rcon.config_doc import read_config, read_reserved, reserved_ids_from_text
from app.rcon.routes import RouteName, WriteName, route_for
from app.updates import APP_VERSION

router = APIRouter(prefix="/api/bot", tags=["QQ Bot"])
bearer = HTTPBearer(auto_error=False, scheme_name="BotToken")
SteamId = Annotated[str, Path(pattern=r"^[0-9]{17}$")]


class RecordedStats(BaseModel):
    kills: int | None
    deaths: int | None
    cash: int | None
    peakCash: int | None
    matches: int | None
    wins: int | None
    playtimeSeconds: int | None
    battleLevel: int | None


class StatsResponse(BaseModel):
    steamId: str
    name: str | None
    online: bool | None
    scope: Literal["recorded_lifetime"]
    source: Literal["panel_observations"]
    officialCareer: bool
    updatedAt: str | None
    onlineObservedAt: str
    stats: RecordedStats
    currentMatch: dict[str, int | None] | None


class RankingResponse(BaseModel):
    steamId: str
    period: Literal["all"]
    rank: int | None
    totalPlayers: int
    battleLevel: int | None
    rankBy: Literal["kills"]
    kills: int | None
    ties: Literal["dense_rank"]
    includesOffline: bool
    scope: Literal["recorded_lifetime"]
    updatedAt: str | None


class ReservedResponse(BaseModel):
    steamId: str
    active: bool
    configured: bool | None
    pendingRestart: bool | None
    expiresAt: str | None
    reason: str | None
    metadataStatus: str | None
    updatedAt: str
    configuredSource: str | None
    configurationVerified: bool


class BanReceipt(BaseModel):
    requestId: str
    steamId: str
    outcome: Literal["accepted", "rejected", "uncertain"]
    code: str
    replayed: bool
    updatedAt: str | None = None


def now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class BotStore:
    def __init__(self, database):
        self.db = database

    def initialize(self):
        with self.db._connect() as db:
            # Dedicated audit does not impersonate a panel administrator.
            db.execute("""CREATE TABLE IF NOT EXISTS bot_ban_requests (
                request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                payload TEXT NOT NULL, target_origin TEXT NOT NULL,
                result TEXT, created_at TEXT NOT NULL, completed_at TEXT)""")

    def claim(self, payload, origin):
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        fingerprint = sha256(encoded.encode()).hexdigest()
        with self.db._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM bot_ban_requests WHERE request_id=?", (payload["requestId"],)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise PanelError("bot_request_conflict")
                result = json.loads(row["result"]) if row["result"] else {
                    "outcome": "uncertain", "code": "operation_in_progress_or_interrupted",
                    "requestId": payload["requestId"], "steamId": payload["steamId"],
                }
                return {**result, "replayed": True}
            db.execute("INSERT INTO bot_ban_requests VALUES(?,?,?,?,NULL,?,NULL)",
                       (payload["requestId"], fingerprint, encoded, origin, now()))
        return None

    def finish(self, request_id, result):
        with self.db._connect() as db:
            db.execute("UPDATE bot_ban_requests SET result=?,completed_at=? WHERE request_id=?",
                       (json.dumps(result), now(), request_id))


async def identity(request: Request, credential: HTTPAuthorizationCredentials | None = Security(bearer)):
    settings = request.app.state.settings
    supplied = sha256((credential.credentials if credential else "").encode()).digest()
    role = None
    for name, token in (("read", settings.bot_read_token), ("management", settings.bot_admin_token)):
        if token and credential and secrets.compare_digest(supplied, sha256(token.encode()).digest()):
            role = name
    if role is None:
        raise PanelError("not_authenticated")
    # Fixed-size role buckets: caller-controlled values cannot grow the limiter.
    limits = request.app.state.bot_limits
    key = (role, "ban" if request.method == "POST" else "read")
    start, count = limits.get(key, (monotonic(), 0))
    if monotonic() - start >= 60:
        start, count = monotonic(), 0
    if count >= (10 if key[1] == "ban" else 120):
        raise PanelError("rate_limited")
    limits[key] = start, count + 1
    return role


async def management(role: str = Depends(identity)):
    if role != "management":
        raise PanelError("permission_denied")


def origin(runtime):
    if runtime.target is None:
        raise PanelError("rcon_unconfigured")
    return runtime.target.origin


@router.get("/capabilities")
async def capabilities(request: Request, role: str = Depends(identity)):
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        server = origin(runtime)
        ban = False
        try:
            await runtime.capabilities.require_write(WriteName.BAN)
            ban = True
        except PanelError as exc:
            if exc.code not in {"write_disabled", "action_unsupported", "route_unsupported"}:
                raise
        return {"apiVersion": "1", "panelVersion": APP_VERSION, "build": "bot-api-1",
                "serverId": sha256(server.encode()).hexdigest()[:24],
                "targetRevision": runtime.target_revision, "permissions": ["personal:read"] + (["players:read", "bans:write"] if role == "management" else []),
                "features": {"stats": True, "ranking": True, "reservedSlot": True,
                             "ban": ban and role == "management", "banOnlineOnly": True},
                "rankingPeriods": ["all"], "statsSource": "panel_observations",
                "officialCareer": False, "updatedAt": now()}


@router.get("/players/{steamId}/stats", dependencies=[Depends(identity)], response_model=StatsResponse,
            description="本服累计观测数据与当前比赛分开返回；缺失字段为 null，非官方生涯。")
async def stats(request: Request, steamId: SteamId):
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        server = origin(runtime)
        snapshot = await runtime.read_service.players()
        player = next((p for p in snapshot["players"] if p["steamId"] == steamId), None)
        with request.app.state.database._connect() as db:
            total = db.execute("SELECT * FROM player_totals WHERE origin=? AND steam_id=?", (server, steamId)).fetchone()
            observed = db.execute("""SELECT COUNT(*) AS matches, MAX(p.last_seen) AS updated
                FROM observed_players p JOIN observed_matches m ON m.id=p.match_id
                WHERE m.origin=? AND p.steam_id=?""", (server, steamId)).fetchone()
            recent = db.execute("""SELECT p.name FROM observed_players p JOIN observed_matches m ON m.id=p.match_id
                WHERE m.origin=? AND p.steam_id=? ORDER BY p.last_seen DESC LIMIT 1""", (server, steamId)).fetchone()
        return {"steamId": steamId, "name": player["name"] if player else recent["name"] if recent else None,
                "online": None if snapshot["stale"] else player is not None,
                "scope": "recorded_lifetime", "source": "panel_observations", "officialCareer": False,
                "updatedAt": observed["updated"], "onlineObservedAt": snapshot["observedAt"],
                "stats": {"kills": total["total_kills"] if total else None,
                          "deaths": total["total_deaths"] if total else None,
                          "cash": total["latest_cash"] if total else None,
                          "peakCash": total["peak_cash"] if total else None,
                          "matches": observed["matches"] if recent else None,
                          "wins": None, "playtimeSeconds": None, "battleLevel": None},
                "currentMatch": {k: player[k] for k in ("kills", "deaths", "cash", "pingMs")} if player else None}


@router.get("/players/{steamId}/ranking", dependencies=[Depends(identity)], response_model=RankingResponse,
            description="all 周期，按累计观测击杀密集排名；包含离线玩家，排除缺失击杀者。")
async def ranking(request: Request, steamId: SteamId, period: Literal["all"] = "all"):
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        server = origin(runtime)
        with request.app.state.database._connect() as db:
            row = db.execute("""SELECT * FROM (SELECT steam_id,total_kills,
                DENSE_RANK() OVER(ORDER BY total_kills DESC) AS rank,
                COUNT(*) OVER() AS total FROM player_totals WHERE origin=? AND total_kills IS NOT NULL)
                WHERE steam_id=?""", (server, steamId)).fetchone()
            count = db.execute("SELECT COUNT(*) FROM player_totals WHERE origin=? AND total_kills IS NOT NULL", (server,)).fetchone()[0]
            updated = db.execute("SELECT MAX(last_seen) FROM observed_matches WHERE origin=?", (server,)).fetchone()[0]
        return {"steamId": steamId, "period": period, "rank": row["rank"] if row else None,
                "totalPlayers": count, "battleLevel": None, "rankBy": "kills",
                "kills": row["total_kills"] if row else None, "ties": "dense_rank",
                "includesOffline": True, "scope": "recorded_lifetime", "updatedAt": updated}


@router.get("/players/{steamId}/reserved-slot", dependencies=[Depends(identity)], response_model=ReservedResponse,
            description="仅返回该玩家。active 为运行列表；configured 为官方配置报告，不能保证磁盘一致。")
async def reserved(request: Request, steamId: SteamId):
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        server = origin(runtime)
        spec = route_for(RouteName.RESERVED_SLOTS)
        await runtime.capabilities.require_advertised(spec.method, spec.path)
        active = steamId in await read_reserved(runtime.client)
        configured = None
        try:
            spec = route_for(RouteName.CONFIG)
            await runtime.capabilities.require_advertised(spec.method, spec.path)
            configured = steamId in reserved_ids_from_text((await read_config(runtime.client))["text"])
        except PanelError as exc:
            if exc.code != "route_unsupported":
                raise
        metadata = request.app.state.database.reserved_metadata(server).get(steamId, {})
        return {"steamId": steamId, "active": active, "configured": configured,
                "pendingRestart": active != configured if configured is not None else None,
                "expiresAt": metadata.get("expiresAt"), "reason": metadata.get("reason"),
                "metadataStatus": metadata.get("status"), "updatedAt": now(),
                "configuredSource": "rcon_config" if configured is not None else None,
                "configurationVerified": False}


@router.get("/players", dependencies=[Depends(management)])
async def players(request: Request, search: Annotated[str, Query(max_length=64)] = "",
                  limit: Annotated[int, Query(ge=1, le=50)] = 20,
                  offset: Annotated[int, Query(ge=0, le=10000)] = 0):
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        origin(runtime)
        snapshot = await runtime.read_service.players()
        selected = [p for p in snapshot["players"] if p["steamId"] and
                    (search.casefold() in p["name"].casefold() or search in p["steamId"])]
        return {"items": [{"steamId": p["steamId"], "name": p["name"]} for p in selected[offset:offset+limit]],
                "total": len(selected), "limit": limit, "offset": offset,
                "targetRevision": runtime.target_revision, "stale": snapshot["stale"],
                "updatedAt": snapshot["observedAt"], "scope": "online"}


class Operator(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qqId: str = Field(pattern=r"^[0-9]{1,20}$")
    groupId: str = Field(pattern=r"^[0-9]{1,20}$")


class BanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steamId: str = Field(pattern=r"^[1-9][0-9]{16}$")
    reason: str = Field(min_length=1, max_length=200)
    targetRevision: str = Field(min_length=1, max_length=128)
    requestId: str = Field(pattern=r"^[A-Za-z0-9_.:-]{8,128}$")
    operator: Operator

    @field_validator("reason")
    @classmethod
    def safe_reason(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("reason must contain printable text")
        return value.strip()


@router.post("/bans", dependencies=[Depends(management)], response_model=BanReceipt,
             responses={409: {"model": BanReceipt, "description": "拒绝或未决重复请求；参数冲突返回通用错误"},
                        502: {"model": BanReceipt, "description": "不确定结果，不能自动重发"}},
             description="管理 Token 专用；仅在线目标，版本校验，持久幂等与 QQ/群审计。")
async def ban(request: Request, body: BanRequest):
    runtime = request.app.state.rcon_runtime
    store = request.app.state.bot_store
    payload = body.model_dump()
    # Claim outside the runtime lock so simultaneous retries get an uncertain receipt.
    receipt = store.claim(payload, origin(runtime))
    if receipt:
        return JSONResponse(receipt, status_code=200 if receipt["outcome"] == "accepted" else 409)
    dispatched = False
    result = {"requestId": body.requestId, "steamId": body.steamId, "replayed": False}
    try:
        async with runtime.lock:
            if body.targetRevision != runtime.target_revision:
                raise PanelError("stale_server_target")
            await runtime.capabilities.require_write(WriteName.BAN)
            runtime.invalidate_players_unlocked()
            snapshot = await runtime.read_service.players()
            if snapshot["stale"]:
                raise PanelError("rcon_unavailable")
            if not any(p["steamId"] == body.steamId for p in snapshot["players"]):
                raise PanelError("player_not_online")
            dispatched = True
            await runtime.client.moderate(WriteName.BAN, body.steamId, body.reason)
            runtime.invalidate_players_unlocked()
        result.update(outcome="accepted", code="accepted", updatedAt=now())
    except PanelError as exc:
        result.update(outcome="uncertain" if exc.code == "action_uncertain" else "rejected", code=exc.code, updatedAt=now())
    except Exception:
        result.update(outcome="uncertain" if dispatched else "rejected", code="internal_error", updatedAt=now())
    try:
        store.finish(body.requestId, result)
    except Exception:
        result.update(outcome="uncertain", code="audit_completion_failed")
    return JSONResponse(result, status_code=200 if result["outcome"] == "accepted" else 502 if result["outcome"] == "uncertain" else 409)


@router.get("/openapi.json", dependencies=[Depends(identity)], include_in_schema=False)
async def openapi(request: Request):
    return get_openapi(title="Wardogs QQ Bot API", version="1",
                       description="bot-api-1；累计观测数据，非官方生涯；详见 BOT_API.md。",
                       routes=router.routes)
