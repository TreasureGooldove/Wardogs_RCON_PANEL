"""Observed round awards. Single-attempt announcements, never invented income."""
import asyncio
from datetime import UTC, datetime
import json
import logging

from app.errors import PanelError
from app.rcon.actions import ActionService
from app.rcon.routes import WriteName, write_route_for

DEFAULT_TITLES = {'kills': '击杀王', 'cash_gain': '许家印', 'deaths': '区王'}


class AwardsStore:
    def __init__(self, database): self.db=database

    def initialize(self):
        with self.db._connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS award_players (
                match_id TEXT NOT NULL,steam_id TEXT NOT NULL,name TEXT NOT NULL,
                kills INTEGER,deaths INTEGER,last_cash INTEGER,cash_gain INTEGER,last_seen TEXT NOT NULL,
                PRIMARY KEY(match_id,steam_id));
              CREATE TABLE IF NOT EXISTS award_config (
                origin TEXT PRIMARY KEY,enabled INTEGER NOT NULL,actor TEXT NOT NULL,updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS award_jobs (
                new_match TEXT PRIMARY KEY,old_match TEXT NOT NULL,origin TEXT NOT NULL,actor TEXT NOT NULL,
                outcome TEXT NOT NULL,parts TEXT NOT NULL,created_at TEXT NOT NULL,error TEXT);
            ''')
            columns={r['name'] for r in db.execute('PRAGMA table_info(award_config)')}
            if 'titles' not in columns:
                db.execute("ALTER TABLE award_config ADD COLUMN titles TEXT NOT NULL DEFAULT '{}'")
            db.execute("UPDATE award_jobs SET outcome='uncertain',error='interrupted' WHERE outcome='processing'")
            db.execute("UPDATE award_jobs SET outcome='skipped',error='interrupted' WHERE outcome='queued'")

    @staticmethod
    def observe(db,origin,mid,players,stamp,previous,reason,trust_gap=30):
        if previous is not None and reason and reason not in ('observation_gap','target_changed'):
            config=db.execute('SELECT * FROM award_config WHERE origin=? AND enabled=1',(origin,)).fetchone()
            if config and previous['sample_count']>=2:
                board=AwardsStore.board_in(db,previous['id'],json.loads(config['titles']))
                parts=[{'message':m,'outcome':'queued','error':None} for m in AwardsStore.messages(board)]
                if parts:
                    db.execute('INSERT OR IGNORE INTO award_jobs VALUES(?,?,?,?,?,?,?,NULL)',
                        (mid,previous['id'],origin,config['actor'],'queued',json.dumps(parts,ensure_ascii=False),stamp))
        for player in players:
            sid=player.get('steamId')
            if not sid: continue
            old=db.execute('SELECT * FROM award_players WHERE match_id=? AND steam_id=?',(mid,sid)).fetchone()
            valid=lambda value:value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None
            kills,deaths,cash=(valid(player.get(k)) for k in ('kills','deaths','cash'))
            gain=old['cash_gain'] if old else None
            if old and cash is not None and old['last_cash'] is not None and 0<=(datetime.fromisoformat(stamp)-datetime.fromisoformat(old['last_seen'])).total_seconds()<=trust_gap:
                gain=(gain or 0)+max(0,cash-old['last_cash'])
            # Unknown cash breaks the comparison chain; the first wallet value is not income.
            peak=lambda value,key:max(value,old[key]) if old and value is not None and old[key] is not None else value if value is not None else old[key] if old else None
            db.execute('''INSERT INTO award_players VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(match_id,steam_id) DO UPDATE SET name=excluded.name,kills=excluded.kills,
                deaths=excluded.deaths,last_cash=excluded.last_cash,cash_gain=excluded.cash_gain,last_seen=excluded.last_seen''',
                (mid,sid,player['name'],peak(kills,'kills'),peak(deaths,'deaths'),cash,gain,stamp))

    @staticmethod
    def board_in(db,mid,titles=None):
        titles={**DEFAULT_TITLES, **(titles or {})}
        rows=[dict(r) for r in db.execute('SELECT * FROM award_players WHERE match_id=?',(mid,))]
        board=[]
        for title,key,unit in [('击杀王','kills','次'),('许家印','cash_gain','现金增加（估算）'),('区王','deaths','次')]:
            known=[r for r in rows if r[key] is not None]
            value=max((r[key] for r in known),default=None)
            winners=sorted((r for r in known if r[key]==value),key=lambda r:r['steam_id']) if value is not None and value>0 else []
            board.append({'title':titles[key],'metric':key,'value':value,'unit':unit,'winners':[{'steamId':r['steam_id'],'name':r['name']} for r in winners[:5]],'tiedPlayers':len(winners)})
        return board

    @staticmethod
    def messages(board):
        messages=[]
        for award in board:
            if not award['winners']: continue
            names='、'.join(' '.join(p['name'].split())[:30] or p['steamId'] for p in award['winners'])
            if award['tiedPlayers']>5:names+=f" 等{award['tiedPlayers']}人"
            if award['tiedPlayers']>1:names+='（并列）'
            messages.append(f"[上一局观测] {award['title']}：{names}，{award['value']} {award['unit']}"[:200])
        return messages

    def view(self,origin):
        with self.db._connect() as db:
            config=db.execute('SELECT * FROM award_config WHERE origin=?',(origin,)).fetchone()
            previous=db.execute("SELECT id,map,end_reason FROM observed_matches WHERE origin=? AND ended_seen IS NOT NULL AND end_reason NOT IN ('observation_gap','target_changed') ORDER BY ended_seen DESC LIMIT 1",(origin,)).fetchone()
            titles={**DEFAULT_TITLES, **(json.loads(config['titles']) if config else {})}
            board=self.board_in(db,previous['id'],titles) if previous else []
            jobs=[dict(r) for r in db.execute('SELECT old_match,new_match,outcome,parts,created_at,error FROM award_jobs WHERE origin=? ORDER BY created_at DESC LIMIT 30',(origin,))]
        for row in jobs:row['parts']=json.loads(row['parts'])
        return {'enabled':bool(config and config['enabled']),'titles':titles,'previousMatch':dict(previous) if previous else None,'board':board,'previewMessages':self.messages(board),'jobs':jobs,'scope':'panel_observations','cashMetric':'observed_positive_changes'}

    def save(self,origin,enabled,actor,titles=None):
        with self.db._connect() as db:
            old=db.execute('SELECT titles FROM award_config WHERE origin=?',(origin,)).fetchone()
            labels={**DEFAULT_TITLES, **(titles if titles is not None else json.loads(old['titles']) if old else {})}
            db.execute('INSERT INTO award_config(origin,enabled,actor,updated_at,titles) VALUES(?,?,?,?,?) ON CONFLICT(origin) DO UPDATE SET enabled=excluded.enabled,actor=excluded.actor,updated_at=excluded.updated_at,titles=excluded.titles',
                (origin,int(enabled),actor,datetime.now(UTC).isoformat(),json.dumps(labels,ensure_ascii=False)))
            db.execute("UPDATE award_jobs SET outcome='skipped',error='settings_changed' WHERE origin=? AND outcome='queued'",(origin,))


class AwardsEngine:
    def __init__(self,runtime,store):self.runtime=runtime;self.store=store;self.ready=set()

    def observe(self,origin,revision):
        key=(origin,revision)
        if key not in self.ready:
            with self.store.db._connect() as db:
                db.execute("UPDATE award_jobs SET outcome='skipped',error='startup_baseline' WHERE origin=? AND outcome='queued'",(origin,))
            self.ready.add(key)

    async def tick(self):
        async with self.runtime.lock:
            target=self.runtime.target
            if not target or (target.origin,self.runtime.target_revision) not in self.ready:return
            with self.store.db._connect() as db:
                row=db.execute("SELECT j.*,m.last_seen,m.ended_seen,c.enabled,c.actor config_actor FROM award_jobs j JOIN observed_matches m ON m.id=j.new_match JOIN award_config c ON c.origin=j.origin WHERE j.origin=? AND j.outcome='queued' ORDER BY j.created_at LIMIT 1",(target.origin,)).fetchone()
                if not row:return
                actor=self.store.db.get_admin_by_id(row['actor'])
                age=(datetime.now(UTC)-datetime.fromisoformat(row['created_at'])).total_seconds()
                fresh=(datetime.now(UTC)-datetime.fromisoformat(row['last_seen'])).total_seconds()<=15
                if not row['enabled'] or row['actor']!=row['config_actor'] or not actor or actor.disabled or (actor.role!='owner' and 'broadcast' not in actor.permissions) or row['ended_seen'] or age>120 or not fresh:
                    db.execute("UPDATE award_jobs SET outcome='skipped',error='stale_or_disabled' WHERE new_match=?",(row['new_match'],));return
            try:
                spec=write_route_for(WriteName.BROADCAST)
                await self.runtime.capabilities.require_advertised(spec.method,spec.path)
            except PanelError as exc:
                with self.store.db._connect() as db:db.execute("UPDATE award_jobs SET outcome='rejected',error=? WHERE new_match=?",(exc.code,row['new_match']))
                return
            with self.store.db._connect() as db:
                db.execute("UPDATE award_jobs SET outcome='processing' WHERE new_match=?",(row['new_match'],))
            parts=json.loads(row['parts']);overall='accepted';error=None
            try:
                for index,part in enumerate(parts):
                    part['outcome']='processing'
                    with self.store.db._connect() as db:db.execute('UPDATE award_jobs SET parts=? WHERE new_match=?',(json.dumps(parts,ensure_ascii=False),row['new_match']))
                    try:
                        await ActionService(self.runtime.client).send(WriteName.BROADCAST,message=part['message'])
                        part['outcome']='accepted'
                    except PanelError as exc:
                        part['outcome']='uncertain' if exc.code=='action_uncertain' else 'rejected';part['error']=exc.code
                    except Exception:part['outcome']='uncertain';part['error']='unexpected'
                    self.store.db.append_moderation_audit(actor.id,'roundAwards','',part['outcome'],'',row['new_match'],self.runtime.target_revision,target.origin)
                    if part['outcome']!='accepted':
                        overall,error=part['outcome'],part['error']
                        for remaining in parts[index+1:]:remaining['outcome']='skipped'
                        break
                    if index<len(parts)-1:await asyncio.sleep(0.35)
            finally:
                if any(p['outcome'] in ('processing','queued') for p in parts):
                    overall='uncertain'
                    for part in parts:
                        if part['outcome']=='processing':part['outcome']='uncertain'
                        elif part['outcome']=='queued':part['outcome']='skipped'
                with self.store.db._connect() as db:
                    db.execute('UPDATE award_jobs SET outcome=?,parts=?,error=? WHERE new_match=?',
                        (overall if all(p['outcome']!='processing' for p in parts) else 'uncertain',json.dumps(parts,ensure_ascii=False),error,row['new_match']))

    async def run(self):
        while True:
            try:await self.tick()
            except asyncio.CancelledError:raise
            except Exception:logging.getLogger(__name__).warning('Awards sender cycle failed')
            await asyncio.sleep(0.5)
