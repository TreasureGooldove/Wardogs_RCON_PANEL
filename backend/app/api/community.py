"""Player tooling: bounded reads, permissioned notes, durable batches and previews."""
import asyncio
from bisect import bisect_left, bisect_right
import csv
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import io
import json
import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, SecretStr, field_validator

from app.api.auth import require_admin
from app.api.rules import RulesConfigBody
from app.errors import PanelError
from app.rcon.actions import ActionService, valid_steam_id
from app.rcon.players import normalize_players
from app.rcon.routes import RouteName, WriteName, route_for, write_route_for
from app.rules.engine import render_parts, validate_template

router = APIRouter(prefix='/api/community', dependencies=[Depends(require_admin)])


class TargetBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    targetRevision: str = Field(min_length=1, max_length=128)


class AnnotationBody(TargetBody):
    note: str = Field(max_length=2000)
    watched: StrictBool


class AwardTitles(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kills: str = Field(min_length=1,max_length=24)
    cash_gain: str = Field(min_length=1,max_length=24)
    deaths: str = Field(min_length=1,max_length=24)

    @field_validator('kills','cash_gain','deaths')
    @classmethod
    def clean_title(cls,value):
        if not value.strip() or any(ord(c)<32 or 127<=ord(c)<=159 or c in '\u2028\u2029' for c in value):
            raise ValueError('Title must be printable and single line')
        return value.strip()


class AwardsBody(TargetBody):
    enabled: StrictBool
    password: SecretStr|None=None
    titles: AwardTitles|None=None


@router.get('/observation')
async def observation(request: Request,response: Response):
    request.app.state.rcon_runtime.touch_interest()
    origin,_=await _target(request,response)
    collector=request.app.state.history_collector
    players,status=collector.cadence()
    return {'tier':collector.tier,'playersSeconds':players,'statusSeconds':status,
            'lastError':collector.last_error,'policy':'warcon_defaults'}


@router.get('/awards')
async def awards(request: Request,response: Response):
    origin,revision=await _target(request,response)
    return {**request.app.state.awards_store.view(origin),'targetRevision':revision}


@router.put('/awards')
async def save_awards(payload: AwardsBody,request: Request):
    runtime=request.app.state.rcon_runtime
    async with runtime.lock:
        origin,actor=_check(request,payload.targetRevision,'broadcast')
        if payload.enabled:
            if not request.app.state.settings.history_enabled or payload.password is None:
                raise PanelError('invalid_selection')
            request.app.state.auth_service.verify_current_password(request,payload.password.get_secret_value())
            spec=write_route_for(WriteName.BROADCAST)
            await runtime.capabilities.require_advertised(spec.method,spec.path)
        request.app.state.awards_store.save(origin,payload.enabled,actor.id,payload.titles.model_dump() if payload.titles else None)
        request.app.state.database.append_moderation_audit(actor.id,'roundAwardsSettings','', 'accepted',
            json.dumps({'enabled':payload.enabled,'titles':payload.titles.model_dump() if payload.titles else None},ensure_ascii=False),request.state.request_id,runtime.target_revision,origin)
        return {**request.app.state.awards_store.view(origin),'targetRevision':runtime.target_revision}


class BatchBody(TargetBody):
    requestId: str = Field(min_length=1, max_length=64)
    steamIds: list[str] = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=200)


class AlertBody(TargetBody):
    windowMinutes: StrictInt = Field(ge=1, le=1440)
    minimumKills: StrictInt = Field(ge=5, le=1000)
    killsPerMinute: float = Field(ge=1, le=100, allow_inf_nan=False)
    headshotPercent: float = Field(ge=50, le=100, allow_inf_nan=False)


async def _target(request, response=None):
    if response is not None:
        response.headers['Cache-Control']='no-store'
    runtime=request.app.state.rcon_runtime
    async with runtime.lock:
        if runtime.target is None:
            raise PanelError('rcon_unconfigured')
        return runtime.target.origin, runtime.target_revision


def _check(request, revision, permission):
    request.app.state.auth_service.check_origin(request)
    actor=request.app.state.auth_service.require_permission(request, permission)
    runtime=request.app.state.rcon_runtime
    if runtime.target is None or runtime.target_revision != revision:
        raise PanelError('stale_server_target')
    return runtime.target.origin, actor


@router.get('/players/{sid}/dossier')
async def dossier(sid: str, request: Request, response: Response):
    valid_steam_id(sid)
    origin,revision=await _target(request,response)
    result=request.app.state.community_store.dossier(origin,sid)
    return {**result,'targetRevision':revision}


@router.put('/players/{sid}/annotation')
async def annotation(sid: str,payload: AnnotationBody,request: Request):
    valid_steam_id(sid)
    if any(ord(c)<32 and c not in '\r\n\t' for c in payload.note):
        raise PanelError('invalid_selection')
    runtime=request.app.state.rcon_runtime
    async with runtime.lock:
        origin,actor=_check(request,payload.targetRevision,'notes')
        request.app.state.community_store.annotate(origin,sid,payload.note,payload.watched,actor.username)
        request.app.state.database.append_moderation_audit(actor.id,'annotation',sid,'accepted',
            'watched='+str(payload.watched)+'; note_sha256='+sha256(payload.note.encode()).hexdigest(),
            request.state.request_id,runtime.target_revision,origin)
    return {'ok':True}


async def deliver_batch(app,batch_id,origin,revision,actor_id,recipients,message):
    store=app.state.community_store
    runtime=app.state.rcon_runtime
    try:
        for recipient in recipients:
            sid=recipient['steamId']
            outcome,error='skipped',None
            async with runtime.lock:
                try:
                    actor=app.state.database.get_admin_by_id(actor_id)
                    if actor is None or actor.disabled or (actor.role != 'owner' and 'message' not in actor.permissions):
                        raise PanelError('permission_denied')
                    if runtime.target_revision!=revision or runtime.target is None or runtime.target.origin!=origin:
                        raise PanelError('stale_server_target')
                    spec=write_route_for(WriteName.MESSAGE)
                    await runtime.capabilities.require_advertised(spec.method,spec.path)
                    player_route=route_for(RouteName.PLAYERS)
                    await runtime.capabilities.require_advertised(player_route.method,player_route.path)
                    # A fresh roster per recipient avoids contacting a player who has left.
                    roster=normalize_players(await runtime.client.request(RouteName.PLAYERS))['players']
                    if not any(p.get('steamId')==sid for p in roster):
                        raise PanelError('player_not_online')
                    store.outcome(batch_id,sid,'processing')
                    try:
                        await ActionService(runtime.client).send(WriteName.MESSAGE,steam_id=sid,message=message)
                        outcome='accepted'
                    except PanelError as exc:
                        outcome='uncertain' if exc.code=='action_uncertain' else 'rejected'
                        error=exc.code
                    except Exception:
                        outcome,error='uncertain','unexpected'
                    app.state.database.append_moderation_audit(actor_id,'batchMessage',sid,outcome,'',batch_id,revision,origin)
                except PanelError as exc:
                    error=exc.code
                except Exception:
                    outcome,error='uncertain','unexpected'
                store.outcome(batch_id,sid,outcome,error)
            await asyncio.sleep(0.35)
    finally:
        with store.db._connect() as db:
            db.execute("UPDATE private_deliveries SET outcome='uncertain',error='interrupted' WHERE batch_id=? AND outcome='processing'",(batch_id,))
            db.execute("UPDATE private_deliveries SET outcome='skipped',error='interrupted' WHERE batch_id=? AND outcome='queued'",(batch_id,))


@router.post('/messages',status_code=202)
async def batch_messages(payload: BatchBody,request: Request):
    try:
        UUID(payload.requestId)
    except ValueError as exc:
        raise PanelError('invalid_selection') from exc
    for sid in payload.steamIds:
        valid_steam_id(sid)
    if len(set(payload.steamIds)) != len(payload.steamIds) or not payload.message.strip() or any(ord(c)<32 or ord(c)==127 for c in payload.message):
        raise PanelError('invalid_selection')
    runtime=request.app.state.rcon_runtime
    async with runtime.lock:
        origin,actor=_check(request,payload.targetRevision,'message')
        spec=write_route_for(WriteName.MESSAGE)
        await runtime.capabilities.require_advertised(spec.method,spec.path)
        player_route=route_for(RouteName.PLAYERS)
        await runtime.capabilities.require_advertised(player_route.method,player_route.path)
        roster=normalize_players(await runtime.client.request(RouteName.PLAYERS))['players']
        by_id={p['steamId']:p for p in roster if p.get('steamId')}
        # Repeated requests consult the ledger before requiring current online presence.
        old=request.app.state.community_store.batch_status(payload.requestId,origin)
        if old:
            recipients=[{'steamId':sid,'name':by_id.get(sid,{}).get('name','')} for sid in payload.steamIds]
        else:
            if any(sid not in by_id for sid in payload.steamIds):
                raise PanelError('player_not_online')
            recipients=[by_id[sid] for sid in payload.steamIds]
        created=request.app.state.community_store.batch(payload.requestId,actor.id,origin,payload.targetRevision,recipients,payload.message.strip())
        if created:
            task=asyncio.create_task(deliver_batch(request.app,payload.requestId,origin,runtime.target_revision,actor.id,recipients,payload.message.strip()))
            request.app.state.community_tasks.add(task)
            task.add_done_callback(request.app.state.community_tasks.discard)
        return request.app.state.community_store.batch_status(payload.requestId,origin)


@router.get('/messages/{batch_id}')
async def batch_status(batch_id: str,request: Request,response: Response):
    request.app.state.auth_service.require_permission(request,'message')
    origin,_=await _target(request,response)
    result=request.app.state.community_store.batch_status(batch_id,origin)
    if result is None:
        raise HTTPException(404)
    actor=request.app.state.auth_service.require_admin(request)
    if actor.role!='owner' and result['actor']!=actor.id:
        raise PanelError('permission_denied')
    return result


@router.post('/rules/preview')
async def preview(payload: RulesConfigBody,request: Request):
    runtime=request.app.state.rcon_runtime
    async with runtime.lock:
        origin,_=_check(request,payload.targetRevision,'rules')
    try:
        first=validate_template(payload.firstText,required=True)
        second=validate_template(payload.secondText,required=False)
    except ValueError as exc:
        raise PanelError('invalid_selection') from exc
    end=datetime.now(UTC)
    start=end-timedelta(hours=24)
    rows=request.app.state.community_store.preview_sessions(origin,(start-timedelta(hours=24)).isoformat(),end.isoformat())
    cooldown={};slots={};items=[]
    starts=sorted(datetime.fromisoformat(s['started_at']).timestamp() for s in rows)
    ends=sorted(datetime.fromisoformat(s['ended_at'] or s['last_seen']).timestamp() for s in rows)
    for row in rows[:10000]:
        joined=datetime.fromisoformat(row['started_at'])
        due=joined+timedelta(seconds=payload.delaySeconds)
        why=None
        if not row['join_observed']:
            why='baseline'
        elif row['steam_id'] in cooldown and (due-cooldown[row['steam_id']]).total_seconds()<payload.cooldownMinutes*60:
            why='cooldown'
        bucket=int(due.timestamp()//5)
        if not why:
            while slots.get(bucket,0)>=payload.maxPerRound:
                bucket+=1
            due=max(due,datetime.fromtimestamp(bucket*5,UTC))
        left=datetime.fromisoformat(row['ended_at'] or row['last_seen'])
        parts=[]
        if not why and (due>left or due>end):
            why='left_before_delivery' if due>left else 'not_due'
        if not why:
            count=bisect_right(starts,due.timestamp())-bisect_left(ends,due.timestamp())
            try:
                parts=render_parts(first,row['name'],row['steam_id'],count)
                second_due=due+timedelta(seconds=payload.gapSeconds+max(0,len(parts)-1)*0.3)
                if second and second_due<=left and second_due<=end:
                    parts+=render_parts(second,row['name'],row['steam_id'],count)
            except ValueError as exc:
                raise PanelError('invalid_selection') from exc
            cooldown[row['steam_id']]=due;slots[bucket]=slots.get(bucket,0)+1
        if joined>=start:
            items.append({'steamId':row['steam_id'],'name':row['name'],'joinedAt':row['started_at'],
                          'wouldSendAt':due.isoformat(),'result':why or 'would_send','parts':parts})
    return {'items':items[:1000],'total':len(items),'truncated':len(rows)>10000 or len(items)>1000,
            'from':start.isoformat(),'to':end.isoformat(),'simulation':True,'commandsSent':0,
            'scope':'observed_sessions','approximate':True}


@router.get('/kills')
async def kills(request: Request,response: Response,matchId: str=Query('current',max_length=64),
    limit: int=Query(50,ge=1,le=200),offset: int=Query(0,ge=0,le=100000),
    gameMatchId: str|None=Query(None,max_length=128),instanceId: str|None=Query(None,max_length=128)):
    origin,_=await _target(request,response)
    result=request.app.state.kill_store.query(origin,matchId,limit,offset,gameMatchId,instanceId)
    return {**result,'configured':bool(request.app.state.settings.feed_token and request.app.state.settings.feed_origin==origin)}


@router.get('/kill-alerts')
async def kill_alerts(request: Request,response: Response):
    origin,revision=await _target(request,response)
    return {**request.app.state.kill_store.alerts(origin),'targetRevision':revision}


@router.put('/kill-alerts/settings')
async def alert_settings(payload: AlertBody,request: Request):
    runtime=request.app.state.rcon_runtime
    async with runtime.lock:
        origin,actor=_check(request,payload.targetRevision,'notes')
        request.app.state.kill_store.save_settings(origin,payload.model_dump(exclude={'targetRevision'}))
        request.app.state.database.append_moderation_audit(actor.id,'killAlertSettings','', 'accepted',
            json.dumps(payload.model_dump(exclude={'targetRevision'})),request.state.request_id,runtime.target_revision,origin)
    return {'ok':True}


@router.get('/feed-setup')
async def feed_setup(request: Request,response: Response):
    actor=request.app.state.auth_service.require_admin(request)
    if actor.role!='owner':
        raise PanelError('permission_denied')
    origin,_=await _target(request,response)
    settings=request.app.state.settings
    configured=bool(settings.feed_token and settings.feed_origin==origin)
    return {'configured':configured,'url':settings.public_origin,'token':settings.feed_token if configured else None,
            'section':'WDServerFeed','restartRequired':True}


@router.get('/audit')
async def audit(request: Request,response: Response,format: Literal['json','csv']='json',
    action: str=Query('',max_length=64),outcome: Literal['','accepted','rejected','uncertain']='',
    actor: str=Query('',max_length=64),steamId: str=Query('',max_length=17),
    since: datetime|None=None,until: datetime|None=None,limit: int=Query(200,ge=1,le=5000)):
    request.app.state.auth_service.require_permission(request,'audit')
    origin,_=await _target(request,response)
    where=['a.target_origin=?'];args=[origin]
    for column,value in [('a.action',action),('a.outcome',outcome),('u.username',actor),('a.steam_id',steamId)]:
        if value:
            where.append(column+'=?');args.append(value)
    for column,value in [('a.created_at>=',since),('a.created_at<=',until)]:
        if value:
            if value.tzinfo is None:
                raise PanelError('invalid_selection')
            where.append(column+'?');args.append(value.astimezone(UTC).isoformat())
    with request.app.state.database._connect() as db:
        base=' FROM moderation_audit a JOIN admins u ON u.id=a.admin_id WHERE '+' AND '.join(where)
        total=db.execute('SELECT COUNT(*)'+base,args).fetchone()[0]
        rows=db.execute('SELECT a.id,a.created_at,u.username actor,a.action,a.steam_id,a.outcome,a.reason,a.request_id'+base+' ORDER BY a.id DESC LIMIT ?',(*args,limit)).fetchall()
    items=[dict(r) for r in rows]
    # Audit text is supplied by humans; hide recognisable credential assignments.
    for row in items:
        row['reason']=re.sub(r'(?i)(password|token|bearer|apikey|secret)\s*[:=]\s*\S+',r'\1=[redacted]',row['reason'])
    if format=='json':
        return {'items':items,'total':total,'truncated':total>limit,'source':'panel_operations'}
    stream=io.StringIO(newline='')
    columns=['id','created_at','actor','action','steam_id','outcome','reason','request_id']
    writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
    for row in items:
        writer.writerow({k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')) else v) for k,v in row.items()})
    return Response('\ufeff'+stream.getvalue(),media_type='text/csv; charset=utf-8',headers={
        'Cache-Control':'no-store','Content-Disposition':'attachment; filename="panel-audit.csv"','X-Total-Count':str(total),
        'X-Export-Truncated':str(total>limit).lower()})
