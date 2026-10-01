"""Shared HTTP RCON observations, using Warcon's default tier cadences."""
from __future__ import annotations
import asyncio
import logging
from time import monotonic
from app.errors import PanelError
from app.rcon.players import normalize_players
from app.rcon.routes import RouteName
from app.rcon.runtime import RconRuntime
from app.rcon.status import normalize_status
from app.rules.engine import RulesEngine
from app.warmup.engine import WarmupEngine
from .store import HistoryStore
_LOG=logging.getLogger(__name__)

class HistoryCollector:
    def __init__(self,runtime: RconRuntime,store: HistoryStore,rules: RulesEngine|None=None,
                 warmup: WarmupEngine|None=None,interval: float=5.0,awards=None):
        self.runtime=runtime;self.store=store;self.rules=rules;self.warmup=warmup;self.awards=awards
        self.interval=interval;self.last_error=None
        self.revision='';self.status=None;self.players=None;self.failures=0
        self.players_due=0.;self.status_due=0.;self.lists_at=0.;self.identity_at=0.
        self.hold_until=0.;self.health_unserved=False;self.tier='idle'

    def cadence(self,now=None):
        now=monotonic() if now is None else now
        if self.failures>=3:
            self.tier='offline';delay=min(120.,30.*2**min(10,self.failures-3));return delay,delay
        if now<self.runtime.watch_until:self.tier='watched';return 1.,2.
        if (self.players and self.players['players']) or (self.status and (self.status.get('playerCount') or 0)>0):
            self.tier='hot';return 2.,5.
        self.tier='idle';return 30.,30.

    @staticmethod
    def next_due(previous,period,now):
        return previous+max(1,int((now-previous)//period)+1)*period if previous else now+period

    async def optional_read(self,route):
        try:
            await self.runtime.capabilities.require(route)
            return await self.runtime.client.request(route)
        except PanelError as exc:
            if exc.code=='rcon_rate_limited':raise
            if route is RouteName.HEALTH and exc.code=='route_unsupported':self.health_unserved=True
            return None

    async def sample(self,*,scheduled=False):
        async with self.runtime.lock:
            if self.runtime.target is None:return False
            now=monotonic()
            if self.revision!=self.runtime.target_revision:
                self.revision=self.runtime.target_revision;self.status=None;self.players=None
                self.players_due=self.status_due=self.lists_at=self.identity_at=self.hold_until=0.
                self.failures=0;self.health_unserved=False
            if now<self.hold_until:return False
            p_period,s_period=self.cadence(now);self.runtime.cadence=(p_period,s_period)
            p_due=not scheduled or now>=self.players_due
            s_due=not scheduled or now>=self.status_due or self.status is None
            if not p_due and not s_due:return False
            if p_due:self.players_due=self.next_due(self.players_due,p_period,now)
            if s_due:self.status_due=self.next_due(self.status_due,s_period,now)
            origin=self.runtime.target.origin;status=players=None
            if s_due:
                await self.runtime.capabilities.require(RouteName.STATUS)
                status=normalize_status(await self.runtime.client.request(RouteName.STATUS))
            if p_due:
                await self.runtime.capabilities.require(RouteName.PLAYERS)
                players=normalize_players(await self.runtime.client.request(RouteName.PLAYERS))
            if status:self.status=status;self.runtime.read_service.publish(RouteName.STATUS,status)
            if players is not None:self.players=players;self.runtime.read_service.publish(RouteName.PLAYERS,players)
            had_failed=self.failures>0;self.failures=0;self.last_error=None
            if players is not None and self.status is not None:
                await asyncio.to_thread(self.store.record,origin,self.status,players,trust_gap=2*p_period+1)
                if self.rules:self.rules.observe(origin,players['players'])
                if self.warmup:self.warmup.observe(origin,players['players'])
                if self.awards:self.awards.observe(origin,self.runtime.target_revision)
            if had_failed or now-self.identity_at>=3600 or not self.identity_at:
                self.identity_at=now;self.health_unserved=False
                if had_failed:self.runtime.capabilities.invalidate()
                await self.optional_read(RouteName.SERVER_ID)
            if status is not None and not self.health_unserved:
                await self.optional_read(RouteName.HEALTH)
            if now-self.lists_at>=300 or not self.lists_at:
                self.lists_at=now
                # Only read list snapshots. No automatic reconciliation writes.
                for route in (RouteName.BANS,RouteName.RESERVED_SLOTS):
                    raw=await self.optional_read(route)
                    if raw is not None:self.runtime.read_service.publish(route,{'raw':raw})
            new_p,new_s=self.cadence(monotonic());self.runtime.cadence=(new_p,new_s)
            self.players_due=min(self.players_due,now+new_p);self.status_due=min(self.status_due,now+new_s)
        return True

    async def run(self):
        self.runtime.collector_enabled=True
        try:
            while True:
                now=monotonic();p,s=self.cadence(now);self.runtime.cadence=(p,s)
                self.players_due=min(self.players_due,now+p);self.status_due=min(self.status_due,now+s)
                try:await self.sample(scheduled=True)
                except asyncio.CancelledError:raise
                except Exception as exc:
                    self.last_error=exc.code if isinstance(exc,PanelError) else type(exc).__name__
                    if isinstance(exc,PanelError) and exc.code=='rcon_rate_limited':
                        delay=max(1.,getattr(self.runtime.client,'retry_after',5.));self.hold_until=max(self.hold_until,monotonic()+delay)
                    else:
                        self.failures+=1;p,s=self.cadence();self.players_due=monotonic()+p;self.status_due=monotonic()+s
                        if self.failures==3:
                            async with self.runtime.lock:
                                if self.runtime.target and self.runtime.target_revision==self.revision:
                                    origin=self.runtime.target.origin
                                    with self.store.db._connect() as db:
                                        db.execute("UPDATE observed_matches SET ended_seen=last_seen,end_reason='observation_gap' WHERE origin=? AND ended_seen IS NULL",(origin,))
                                        db.execute("UPDATE player_sessions SET ended_at=last_seen,end_reason='observation_gap' WHERE origin=? AND ended_at IS NULL",(origin,))
                                        db.execute('DELETE FROM session_samples WHERE origin=?',(origin,))
                                    if self.rules:self.rules.reset()
                    _LOG.warning('History collection failed: %s',self.last_error)
                await asyncio.sleep(0.25)
        finally:self.runtime.collector_enabled=False
