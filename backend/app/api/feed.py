"""The game's separate Bearer-authenticated, bounded kill-event ingress."""
import asyncio
from datetime import UTC, datetime
from collections import deque
from hashlib import sha256
import json
import secrets
from time import monotonic

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.errors import PanelError
from app.game_rules.models import ItemUsedEvent

router=APIRouter()


class PositionMeters(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    x: float = Field(allow_inf_nan=False, ge=-1000000, le=1000000)
    y: float = Field(allow_inf_nan=False, ge=-1000000, le=1000000)
    z: float = Field(allow_inf_nan=False, ge=-1000000, le=1000000)


class KillEvent(BaseModel):
    model_config=ConfigDict(extra='ignore')
    type: str
    eventId: str=Field(min_length=1,max_length=128)
    matchId: str=Field(min_length=1,max_length=128)
    eventTime: float|None=Field(default=None,ge=0,allow_inf_nan=False)
    mapName: str=Field(default='',max_length=256)
    killerSteamId: str|None=Field(default=None,pattern=r'^[1-9][0-9]{16}$')
    killerName: str|None=Field(default=None,max_length=256)
    victimSteamId: str|None=Field(default=None,pattern=r'^[1-9][0-9]{16}$')
    victimName: str|None=Field(default=None,max_length=256)
    cause: str|None=Field(default=None,max_length=256)
    distance: float|None=Field(default=None,ge=0,le=100000000,allow_inf_nan=False)
    contextTags: list[str]|None=Field(default=None,max_length=64)
    victimPositionMeters: PositionMeters | None = None
    occurredAt: datetime | None = None

    @field_validator('occurredAt', mode='before')
    @classmethod
    def timestamp_text(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError('occurredAt must be a timestamp with an explicit time zone')
        return value

    @field_validator('occurredAt')
    @classmethod
    def time_zone(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('occurredAt requires an explicit time zone')
        return value

    @field_validator('eventTime','distance',mode='before')
    @classmethod
    def numeric(cls,value):
        if value is not None and (isinstance(value,bool) or not isinstance(value,(float,int))):
            raise ValueError('invalid number')
        return value

    @field_validator('eventId','matchId','mapName','killerName','victimName','cause')
    @classmethod
    def text(cls,value):
        if value is not None and any(ord(c)<32 or ord(c)==127 for c in value):
            raise ValueError('invalid text')
        return value

    @field_validator('contextTags')
    @classmethod
    def tags(cls,value):
        if value is not None and any(len(tag)>256 or any(ord(c)<32 or ord(c)==127 for c in tag) for tag in value):
            raise ValueError('invalid tags')
        return value


@router.post('/api/ingest/events')
async def ingest(request: Request):
    settings=request.app.state.settings
    supplied=request.headers.get('Authorization','')
    expected='Bearer '+(settings.feed_token or '')
    if not settings.feed_token or not secrets.compare_digest(sha256(supplied.encode()).digest(),sha256(expected.encode()).digest()):
        raise PanelError('not_authenticated')
    clock=monotonic()
    arrivals=request.app.state.feed_arrivals
    while arrivals and arrivals[0]<clock-60:
        arrivals.popleft()
    if len(arrivals)>=120:
        raise PanelError('rate_limited')
    arrivals.append(clock)
    raw=bytearray()
    async for part in request.stream():
        if len(raw)+len(part)>65536:
            raise PanelError('feed_body_too_large')
        raw.extend(part)
    try:
        body=json.loads(raw)
        if not isinstance(body,dict) or not isinstance(body.get('serverId'),str) or not 1<=len(body['serverId'])<=128 or any(ord(c)<32 for c in body['serverId']):
            raise ValueError('invalid instance')
        events=body.get('events')
        if not isinstance(events,list) or len(events)>200 or any(not isinstance(e,dict) for e in events):
            raise ValueError('invalid batch')
        kills=[KillEvent.model_validate(e).model_dump(mode='json') for e in events if e.get('type')=='killed']
        item_uses=[ItemUsedEvent.model_validate(e).model_dump(mode='json') for e in events if e.get('type')=='itemUsed']
    except (ValueError,TypeError,ValidationError,RecursionError) as exc:
        raise PanelError('feed_invalid_batch') from exc
    runtime=request.app.state.rcon_runtime
    async with runtime.lock:
        if runtime.target is None or runtime.target.origin!=settings.feed_origin:
            raise PanelError('stale_server_target')
        result=await asyncio.to_thread(request.app.state.kill_store.ingest,settings.feed_origin,body['serverId'],kills,include_inserted=True)
        fresh=result.pop('insertedEvents')
        store=request.app.state.game_rules_store
        evidence=[]
        for event in fresh:
            tags=[tag.split('.')[-1].lower() for tag in event.get('contextTags') or []]
            actionable=event.get('killerSteamId') if event.get('killerSteamId') != event.get('victimSteamId') and 'suicide' not in tags else None
            recorded=store.evidence(settings.feed_origin,'kill_cause',body['serverId'],event['eventId'],event['matchId'],
                event['localMatch'],actionable,cause=event.get('cause'),occurred_at=event.get('occurredAt'))
            if recorded:evidence.append(recorded)
        item_accepted=0
        for event in item_uses:
            age=(datetime.now(UTC)-datetime.fromisoformat(event['occurredAt'])).total_seconds()
            linked=request.app.state.kill_store.register_item_round(settings.feed_origin,body['serverId'],event) if -3<=age<=10 else None
            recorded=store.evidence(settings.feed_origin,'item_used',body['serverId'],event['eventId'],event['matchId'],
                linked,event['steamId'],item_id=event['itemId'],occurred_at=event['occurredAt'])
            if recorded:
                item_accepted+=1
                evidence.append(recorded)
        # Recording success is independent of an automation failure: producers may retry
        # batches, but consumed event IDs must never cause another game operation.
        try:
            await request.app.state.game_rules_engine.items_unlocked(evidence)
        except Exception:
            import logging
            logging.getLogger(__name__).error('Item rule evaluation failed; recorded events will not be replayed')
    await request.app.state.anticheat_engine.evaluate()
    return {'ok':True,**result,'itemUsesAccepted':item_accepted,'itemUsesDuplicates':len(item_uses)-item_accepted,
            'ignored':len(events)-len(kills)-len(item_uses)}
