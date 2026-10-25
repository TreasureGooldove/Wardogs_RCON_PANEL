"""Public catalog data is parsed as data, never executed as JavaScript."""
import asyncio
import json

import httpx
import pytest

from app.game_rules.catalog import ItemCatalogService, parse_database
from app.storage.db import Database


def document(extra='', updated='2026-10-10'):
    return ('/* public data */ window.DATABASE = {updated:"'+updated+'",source:"https://wardogs.zone/database",items:['
            '{id:"m4",en:"M4",n:"M4",k:"weapons",t:"步枪"},'
            '{id:"test-helmet",en:"Helmet",n:"头盔",k:"equipment",t:"护甲"},'+extra+']};')


def test_database_supports_new_unquoted_keys_without_executing_scripts():
    result=parse_database(document('{id:"new-rifle",en:"New Rifle",n:"新步枪",k:"weapons",t:"步枪"},'))
    assert {i['id'] for i in result['items']}=={'m4','test-helmet','new-rifle'}
    assert result['sourceUpdatedAt']=='2026-10-10'
    for bad in (document()+'fetch("https://invalid.test")', document().replace('"M4"','alert(1)',1),
                document('{id:"m4",en:"Duplicate",n:"重复",k:"weapons",t:"步枪"},'),
                document().replace('k:"equipment"','k:"unknown"')):
        with pytest.raises(ValueError): parse_database(bad)


def test_catalog_sync_merges_new_ids_preserves_old_rules_and_survives_failure(tmp_path):
    async def scenario():
        database=Database(tmp_path/'panel.sqlite3'); database.initialize()
        failed=False
        def handler(request):
            assert request.url.host=='wardogs.t0ki.cn' and 'authorization' not in request.headers
            if failed: return httpx.Response(503)
            if request.url.path=='/weapons.html':
                return httpx.Response(200,text='<span id="db-updated">2026-10-19 S2W1</span>')
            return httpx.Response(200,text=document('{id:"new-rifle",en:"New Rifle",n:"新步枪",k:"weapons",t:"步枪"},'))
        service=ItemCatalogService(database,transport=httpx.MockTransport(handler))
        try:
            await service.refresh(force=True)
            current=service.view()
            assert current['sync']['status']=='ok'
            assert current['pageUpdatedAt']=='2026-10-19 S2W1'
            assert service.items['new-rifle']['name']=='新步枪'
            assert 'a91' in service.items and service.items['a91']['retired']
            assert not service.items['new-rifle']['retired']
            failed=True
            service.next_check=0
            service.last_manual=0
            await service.refresh(force=True)
            assert service.view()['sync']['status']=='unavailable'
            assert 'new-rifle' in service.items
            assert service.view()['sync']['lastSuccessAt']==current['sync']['lastSuccessAt']
            restored=ItemCatalogService(database,transport=httpx.MockTransport(handler))
            try: assert restored.items['new-rifle']['name']=='新步枪'
            finally: await restored.close()
        finally: await service.close()
    asyncio.run(scenario())


def test_manual_sync_is_throttled_and_bad_data_never_replaces_a_valid_catalog(tmp_path):
    async def scenario():
        database=Database(tmp_path/'panel.sqlite3'); database.initialize()
        calls=[]; invalid=False
        def handler(request):
            calls.append(str(request.url))
            if request.url.path=='/weapons.html': return httpx.Response(200,text='<span id="db-updated">2026-10-19</span>')
            return httpx.Response(200,text='window.DATABASE = alert(1)' if invalid else document())
        service=ItemCatalogService(database,transport=httpx.MockTransport(handler))
        try:
            await service.refresh(force=True)
            assert len(calls)==2
            before=service.view()
            await service.refresh(force=True)
            assert len(calls)==2
            invalid=True; service.last_manual=0
            await service.refresh(force=True)
            assert service.view()['sync']['status']=='invalid'
            assert service.view()['items']==before['items']
        finally: await service.close()
    asyncio.run(scenario())
