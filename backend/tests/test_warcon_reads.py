"""Read compatibility checks use fake responses only."""
from datetime import UTC,datetime,timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from app.errors import PanelError
from app.rcon.client import RconClient
from app.rcon.players import normalize_players
from app.rcon.catalog import normalize_catalog
from app.rcon.routes import RouteName
from app.history.collector import HistoryCollector


def test_ping_fallback_catalog_display_and_unknown_statistics():
    player=normalize_players({'players':[{'steamId':'76561190000000001','name':'Fictional','ping':42}]})['players'][0]
    assert player['pingMs']==42 and player['kills'] is None
    row=normalize_catalog({'maps':[{'id':'MapA','displayName':'Test map'}]},'maps')['items'][0]
    assert row['label']=='Test map'


def test_retry_after_matches_warcon_seconds_dates_bounds_and_fallback():
    client=RconClient(None)
    for value,expected in [('bad',5.),('',5.),('0',1.),('600',60.),('12',12.)]:
        client._hold_hint(httpx.Response(429,headers={'Retry-After':value}))
        assert client.retry_after==expected
    date=format_datetime(datetime.now(UTC)+timedelta(seconds=20),usegmt=True)
    client._hold_hint(httpx.Response(429,headers={'Retry-After':date}))
    assert 18<=client.retry_after<=20


@pytest.mark.asyncio
async def test_health_transient_error_does_not_disable_future_reads():
    runtime=SimpleNamespace(capabilities=SimpleNamespace(require=AsyncMock()),client=SimpleNamespace(request=AsyncMock(side_effect=PanelError('rcon_timeout'))))
    collector=HistoryCollector(runtime,None)
    assert await collector.optional_read(RouteName.HEALTH) is None
    assert not collector.health_unserved
    runtime.client.request.side_effect=PanelError('route_unsupported')
    await collector.optional_read(RouteName.HEALTH)
    assert collector.health_unserved
