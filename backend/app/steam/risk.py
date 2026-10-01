"""Steam public ban/age facts, never a cheating verdict or automatic punishment."""
from collections import OrderedDict
from datetime import UTC, datetime
import asyncio
import json
from time import monotonic

import httpx

from app.errors import PanelError
from .service import STEAM_ID_RE, MAX_RESPONSE_BYTES


class SteamRiskService:
    def __init__(self, profiles):
        self.profiles = profiles
        self.cache = OrderedDict()
        self.lock = asyncio.Lock()
        self.next_fetch = 0.0

    async def _fetch(self, route, ids):
        try:
            async with self.profiles._client.stream('GET', 'https://api.steampowered.com/ISteamUser/'+route,
                params={'steamids': ','.join(ids)}, headers={'x-webapi-key': self.profiles._usable_key(), 'accept': 'application/json'}) as response:
                if response.status_code in (401,403):
                    raise PanelError('steam_auth_failed')
                if response.status_code == 429:
                    raise PanelError('steam_rate_limited')
                if response.status_code != 200:
                    raise PanelError('steam_unavailable')
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data)+len(chunk) > MAX_RESPONSE_BYTES:
                        raise PanelError('steam_bad_response')
                    data.extend(chunk)
            return json.loads(data)
        except httpx.TimeoutException as exc:
            raise PanelError('steam_timeout') from exc
        except httpx.HTTPError as exc:
            raise PanelError('steam_unavailable') from exc
        except (ValueError, RecursionError) as exc:
            raise PanelError('steam_bad_response') from exc

    async def lookup(self, ids):
        if not 1 <= len(ids) <= 100 or any(not isinstance(sid,str) or not STEAM_ID_RE.fullmatch(sid) for sid in ids):
            raise PanelError('invalid_steam_ids')
        if not self.profiles._usable_key():
            raise PanelError('steam_unconfigured')
        async with self.lock:
            now = monotonic()
            missing = list(dict.fromkeys(sid for sid in ids if sid not in self.cache or self.cache[sid][0] <= now))
            if missing:
                if now < self.next_fetch:
                    raise PanelError('steam_rate_limited')
                self.next_fetch = now+2
                try:
                    bans, summaries = await asyncio.gather(self._fetch('GetPlayerBans/v1/',missing), self._fetch('GetPlayerSummaries/v2/',missing))
                    raw_bans = bans.get('players') if isinstance(bans,dict) else None
                    raw_profiles = summaries.get('response',{}).get('players') if isinstance(summaries,dict) and isinstance(summaries.get('response'),dict) else None
                    if not isinstance(raw_bans,list) or not isinstance(raw_profiles,list) or len(raw_bans)>100 or len(raw_profiles)>100:
                        raise PanelError('steam_bad_response')
                    if any(not isinstance(r,dict) for r in raw_bans+raw_profiles) or any(not isinstance(r.get('SteamId'),str) for r in raw_bans) or any(not isinstance(r.get('steamid'),str) for r in raw_profiles):
                        raise PanelError('steam_bad_response')
                    by_ban = {r.get('SteamId'):r for r in raw_bans}
                    by_profile = {r.get('steamid'):r for r in raw_profiles}
                    stamp = datetime.now(UTC)
                    for sid in missing:
                        b,p = by_ban.get(sid,{}),by_profile.get(sid,{})
                        number = lambda v: v if isinstance(v,int) and not isinstance(v,bool) and v>=0 else None
                        created = number(p.get('timecreated'))
                        # Steam omits private account age; it remains unknown.
                        age = int((stamp.timestamp()-created)//86400) if created is not None and 0 < created <= stamp.timestamp() else None
                        data = {'steamId':sid,'vacBanned': b.get('VACBanned') if isinstance(b.get('VACBanned'),bool) else None,
                            'vacBans':number(b.get('NumberOfVACBans')),'gameBans':number(b.get('NumberOfGameBans')),
                            'communityBanned':b.get('CommunityBanned') if isinstance(b.get('CommunityBanned'),bool) else None,
                            'economyBan':b.get('EconomyBan') if b.get('EconomyBan') in ('none','probation','banned') else None,
                            'daysSinceLastBan':number(b.get('DaysSinceLastBan')),'accountAgeDays':age,
                            'updatedAt':stamp.isoformat(),'source':'Steam Web API','advisoryOnly':True}
                        data['signals'] = [name for name,test in (
                            ('vac_ban', data['vacBanned'] is True), ('game_ban', (data['gameBans'] or 0)>0),
                            ('community_ban',data['communityBanned'] is True), ('economy_ban',data['economyBan'] in ('banned','probation')),
                            ('new_account',age is not None and age<30)) if test]
                        self.cache[sid] = (monotonic()+600, data, None)
                except PanelError as exc:
                    for sid in missing:
                        self.cache[sid] = (monotonic()+30, None, exc.code)
                while len(self.cache)>1000:
                    self.cache.popitem(last=False)
            result=[]
            for sid in ids:
                _,data,error = self.cache[sid]
                if error:
                    raise PanelError(error)
                self.cache.move_to_end(sid)
                result.append(data)
            return result
