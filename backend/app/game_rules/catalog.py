"""Refresh the public item directory without running upstream JavaScript."""
import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from html import unescape
import json
import logging
from pathlib import Path
import re
import sqlite3
import time

import httpx

PAGE = 'https://wardogs.t0ki.cn/weapons.html'
DATA = 'https://wardogs.t0ki.cn/js/database.js'
UPSTREAM = 'https://wardogs.zone/database'
MAX_ITEMS = 2000
BUNDLED_CATALOG = json.loads(Path(__file__).with_name('item-catalog.json').read_text(encoding='utf-8'))
_WORD = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')
_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
_COLON = re.compile(r'\s*:')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate property')
        result[key] = value
    return result


def _literal(text):
    """Accept JSON strings/numbers and bare object keys, comments, trailing commas."""
    match = re.match(r'\A\s*(?:/\*[\s\S]*?\*/\s*)*window\.DATABASE\s*=\s*', text)
    if not match:
        raise ValueError('unsupported database envelope')
    text = text[match.end():].strip().removesuffix(';').strip()
    tokens = []
    decoder = json.JSONDecoder()
    position = 0
    while position < len(text):
        char = text[position]
        if char.isspace():
            position += 1
        elif text.startswith('/*', position):
            end = text.find('*/', position + 2)
            if end < 0: raise ValueError('unterminated comment')
            position = end + 2
        elif text.startswith('//', position):
            end = text.find('\n', position + 2)
            position = len(text) if end < 0 else end + 1
        elif char == '"':
            value, end = decoder.raw_decode(text, position)
            if not isinstance(value, str): raise ValueError('invalid string')
            tokens.append(text[position:end])
            position = end
        elif char in '{}[]:,':
            tokens.append(char)
            position += 1
        else:
            word = _WORD.match(text, position)
            number = _NUMBER.match(text, position) if not word else None
            if word:
                value = word.group()
                end = position + len(value)
                if _COLON.match(text, end) and tokens and tokens[-1] in ('{', ','):
                    tokens.append(json.dumps(value))
                elif value in ('true', 'false', 'null'):
                    tokens.append(value)
                else:
                    raise ValueError('executable or unsupported token')
                position = end
            elif number:
                tokens.append(number.group())
                position += len(number.group())
            else:
                raise ValueError('unsupported literal')
    tokens = [value for index, value in enumerate(tokens)
              if not (value == ',' and index + 1 < len(tokens) and tokens[index + 1] in (']', '}'))]
    return json.loads(''.join(tokens), object_pairs_hook=_unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite value')))


def _text(value, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError('invalid catalog text')
    return value.strip()


def _item(row):
    sid = _text(row['id'], 128)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', sid):
        raise ValueError('invalid item id')
    if row['kind'] not in ('weapons', 'equipment'):
        raise ValueError('invalid item kind')
    return {'id': sid, 'name': _text(row['name'], 256), 'englishName': _text(row['englishName'], 256),
            'kind': row['kind'], 'category': _text(row.get('category') or '未分类', 128),
            'retired': bool(row.get('retired', False))}


def parse_database(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 1048576:
        raise ValueError('catalog too large')
    try:
        raw = _literal(text)
        rows = raw['items']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 5000:
            raise ValueError('invalid item collection')
        date = _text(raw['updated'], 64)
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            raise ValueError('invalid data date')
        datetime.fromisoformat(date)
        selected = {}
        for row in rows:
            if not isinstance(row, dict): raise ValueError('invalid item')
            if row.get('k') not in ('weapons', 'equipment'): continue
            item = _item({'id': row['id'], 'name': row['n'], 'englishName': row['en'],
                          'kind': row['k'], 'category': row.get('t')})
            if item['id'] in selected: raise ValueError('duplicate item id')
            selected[item['id']] = item
        if (len(selected) > MAX_ITEMS or {item['kind'] for item in selected.values()} != {'weapons', 'equipment'}):
            raise ValueError('incomplete catalog')
        return {'source': PAGE, 'upstreamSource': UPSTREAM, 'sourceUpdatedAt': date, 'items': list(selected.values())}
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError('invalid database schema') from exc


class ItemCatalogService:
    def __init__(self, database, *, transport=None):
        self.database = database
        self.client = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(12, connect=5), headers={'User-Agent': 'Wardogs-Panel-Item-Catalog'})
        self.lock = asyncio.Lock()
        self.next_check = 0
        self.last_manual = 0
        self.document = deepcopy(BUNDLED_CATALOG)
        self.document['items'] = [_item(item) for item in self.document['items']]
        self.sync = {'status': 'bundled', 'lastAttemptAt': None, 'lastSuccessAt': None, 'lastError': None}
        with database._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS item_catalog_cache (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL, state TEXT NOT NULL)')
            row = db.execute('SELECT document,state FROM item_catalog_cache WHERE id=1').fetchone()
        if row:
            try:
                cached, state = json.loads(row['document']), json.loads(row['state'])
                if not isinstance(state, dict): raise ValueError('invalid sync state')
                if not isinstance(cached['items'], list) or not 1 <= len(cached['items']) <= MAX_ITEMS:
                    raise ValueError('invalid cached catalog')
                cached['items'] = [_item(item) for item in cached['items']]
                if len({item['id'] for item in cached['items']}) != len(cached['items']):
                    raise ValueError('duplicate cached item')
                if state.get('status') not in ('ok', 'unavailable', 'invalid', 'bundled', 'cache_error'):
                    raise ValueError('invalid sync state')
                self.document, self.sync = cached, state
            except (ValueError, TypeError, KeyError):
                pass  # A malformed cache never replaces the shipped directory.
        self.items = {item['id']: item for item in self.document['items']}

    async def _download(self, url):
        async with self.client.stream('GET', url) as response:
            response.raise_for_status()
            data = bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data) > 1048576: raise ValueError('catalog too large')
        return data.decode('utf-8-sig')

    def view(self):
        return {**deepcopy(self.document), 'sync': dict(self.sync),
                'refreshIntervalSeconds': 21600,
                'activeCount': sum(not item['retired'] for item in self.items.values())}

    async def refresh(self, *, force=False):
        async with self.lock:
            now = time.monotonic()
            if (not force and now < self.next_check) or (force and self.last_manual and now - self.last_manual < 60):
                return self.view()
            if force: self.last_manual = now
            stamp = datetime.now(UTC).isoformat()
            self.sync['lastAttemptAt'] = stamp
            try:
                incoming = parse_database(await self._download(DATA))
                page = await self._download(PAGE)
                match = re.search(r'<span\b[^>]*\bid=["\']db-updated["\'][^>]*>([^<]+)</span>', page)
                if match: incoming['pageUpdatedAt'] = _text(unescape(match[1]), 64)
                merged = {key: {**item, 'retired': True} for key, item in self.items.items()}
                for item in incoming['items']:
                    if item['id'] in merged and item['kind'] != merged[item['id']]['kind']:
                        raise ValueError('item kind changed')
                    merged[item['id']] = item
                if len(merged) > MAX_ITEMS: raise ValueError('catalog too large')
                incoming['items'] = list(merged.values())
                incoming['retrievedAt'] = stamp
                self.document, self.items = incoming, merged
                self.sync.update(status='ok', lastSuccessAt=stamp, lastError=None)
                self.next_check = time.monotonic() + 21600
            except (httpx.HTTPError, OSError):
                self.sync.update(status='unavailable', lastError='source_unavailable')
                self.next_check = time.monotonic() + 900
            except (ValueError, RecursionError):
                self.sync.update(status='invalid', lastError='source_invalid')
                self.next_check = time.monotonic() + 900
            try:
                with self.database._connect() as db:
                    db.execute('INSERT INTO item_catalog_cache VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document,state=excluded.state',
                               (json.dumps(self.document, ensure_ascii=False), json.dumps(self.sync)))
            except sqlite3.Error:
                self.sync.update(status='cache_error', lastError='cache_unavailable')
                self.next_check = time.monotonic() + 900
            logging.getLogger('wardogs.catalog').info('catalog_sync status=%s items=%d',self.sync['status'],len(self.items))
            return self.view()

    async def run(self):
        while True:
            await self.refresh()
            await asyncio.sleep(max(1, self.next_check - time.monotonic()))

    async def close(self):
        await self.client.aclose()
