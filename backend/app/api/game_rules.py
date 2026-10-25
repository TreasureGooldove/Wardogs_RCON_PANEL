"""Read-only rule views and owner-confirmed automation configuration."""
import asyncio
from datetime import UTC, datetime
import json
from typing import Literal
from hashlib import sha256

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, SecretStr, StrictBool

from app.api.auth import require_admin
from app.api.community import _check, _target
from app.errors import PanelError
from app.game_rules.models import ClosedModel, FactionSettings, ItemSettings, faction_name
from app.game_rules.shuffle import context_for, deliver_shuffle, fresh_sample, roster_state, shuffle_plan, verify_context
from app.rcon.routes import WriteName, write_route_for

router = APIRouter(prefix='/api/game-rules', dependencies=[Depends(require_admin)], tags=['game rules'])


class FactionBody(FactionSettings):
    # Require the explicit semantics marker from writers, so a stale v1 page
    # cannot accidentally turn its old "unlimited" zero into a closed faction.
    limitsVersion: Literal[2]
    targetRevision: str = Field(min_length=1, max_length=128)
    acknowledgeRisk: StrictBool = False
    password: SecretStr | None = None


class ItemBody(ItemSettings):
    targetRevision: str = Field(min_length=1, max_length=128)
    acknowledgeRisk: StrictBool = False
    password: SecretStr | None = None


class ShuffleBody(ClosedModel):
    targetRevision: str = Field(min_length=1, max_length=128)


class ShuffleConfirm(ShuffleBody):
    password: SecretStr
    acknowledgeRisk: StrictBool


def _shuffle_owner(request, revision):
    origin, actor = _check(request, revision, 'changeFaction')
    if actor.role != 'owner':
        raise PanelError('permission_denied')
    request.app.state.rcon_runtime.ensure_running()
    return origin, actor


@router.post('/factions/shuffle/preview')
async def preview_shuffle(payload: ShuffleBody, request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    runtime, store = request.app.state.rcon_runtime, request.app.state.shuffle_store
    async with runtime.lock:
        origin, actor = _shuffle_owner(request, payload.targetRevision)
        if store.active(origin):
            raise PanelError('shuffle_busy')
        rules = request.app.state.game_rules_store.config(origin, 'factions')
        if not rules or rules['revision'] != runtime.target_revision:
            raise PanelError('shuffle_two_factions')
        spec = write_route_for(WriteName.CHANGE_FACTION)
        await runtime.capabilities.require_advertised(spec.method, spec.path)
        players, status = await fresh_sample(runtime)
        plan = shuffle_plan(players, FactionSettings.model_validate_json(rules['settings']))
        names = {row['name'].strip().casefold() for row in status.get('factionScores') or []}
        available = {faction_name(name) for name in names}
        if not status.get('map') or any(player['to'] not in available for player in plan):
            raise PanelError('invalid_upstream')
        return store.public(store.preview(origin, actor.id, runtime.target_revision, rules['generation'], plan, context_for(status)))


@router.post('/factions/shuffle/{key}/execute', status_code=202)
async def execute_shuffle(key: str, payload: ShuffleConfirm, request: Request):
    runtime, store = request.app.state.rcon_runtime, request.app.state.shuffle_store
    async with runtime.lock:
        origin, actor = _shuffle_owner(request, payload.targetRevision)
        if not payload.acknowledgeRisk:
            raise PanelError('invalid_selection')
        request.app.state.auth_service.verify_current_password(request, payload.password.get_secret_value())
        job = store.get(key, origin)
        if job['actor'] != actor.id or job['revision'] != runtime.target_revision:
            raise PanelError('shuffle_stale')
        if job['phase'] != 'preview':
            return store.public(job)  # Same preview ID never executes twice.
        rules = request.app.state.game_rules_store.config(origin, 'factions')
        if datetime.now(UTC) >= datetime.fromisoformat(job['expires']) or not rules or rules['generation'] != job['generation']:
            raise PanelError('shuffle_stale')
        players, status = await fresh_sample(runtime)
        verify_context(job['context'], status)
        if roster_state(players) != {p['steamId']: p['from'] for p in job['plan']}:
            raise PanelError('shuffle_stale')
        if store.start(job):
            try:
                request.app.state.database.append_moderation_audit(actor.id, 'shuffleFactionsStart', '', 'accepted',
                    'players=' + str(len(job['plan'])), key, runtime.target_revision, origin)
            except Exception:
                job['phase'], job['error'] = 'stopped', 'audit_failed'
                store.update(job)
                raise PanelError('action_rejected') from None
            task = asyncio.create_task(deliver_shuffle(request.app, key, origin))
            request.app.state.community_tasks.add(task)
            task.add_done_callback(request.app.state.community_tasks.discard)
        return store.public(store.get(key, origin))


@router.get('/factions/shuffle/{key}')
async def shuffle_status(key: str, request: Request, response: Response):
    actor = request.app.state.auth_service.require_admin(request)
    if actor.role != 'owner':
        raise PanelError('permission_denied')
    origin, _ = await _target(request, response)
    return request.app.state.shuffle_store.public(request.app.state.shuffle_store.get(key, origin))


@router.get('')
async def view(request: Request, response: Response):
    origin, revision = await _target(request, response)
    runtime = request.app.state.rcon_runtime
    runtime.touch_interest()
    store = request.app.state.game_rules_store
    observation = request.app.state.game_rules_engine.last_factions
    if request.app.state.game_rules_engine.observed_revision != revision:
        observation = {'counts': dict.fromkeys(('Lonestar', 'Valkyra', 'Manticore'), 0), 'state': 'no_samples', 'observedAt': None}
    return {'targetRevision': revision, 'factions': store.view(origin, 'factions', revision),
            'latestShuffle': request.app.state.shuffle_store.latest(origin) if request.app.state.auth_service.require_admin(request).role == 'owner' else None,
            'items': store.view(origin, 'items', revision), 'observation': observation,
            'evidence': store.evidence_view(origin), 'historyEnabled': request.app.state.settings.history_enabled,
            'feedConfigured': bool(request.app.state.settings.feed_token and request.app.state.settings.feed_origin == origin),
            'equipmentInventoryAvailable': False, 'catalog': request.app.state.item_catalog.view()}


@router.post('/catalog/refresh')
async def refresh_catalog(request: Request, response: Response):
    request.app.state.auth_service.require_owner(request)
    request.app.state.auth_service.check_origin(request)
    response.headers['Cache-Control'] = 'no-store'
    return await request.app.state.item_catalog.refresh(force=True)


async def _save(payload, kind, request):
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        origin, actor = _check(request, payload.targetRevision, 'changeFaction' if kind == 'factions' else 'kill')
        if actor.role != 'owner':
            raise PanelError('permission_denied')
        # A v1 browser can copy limitsVersion from a fresh GET while retaining
        # its old zero-means-unlimited form. Require a writer-owned marker too.
        if kind == 'factions' and request.headers.get('X-Wardogs-Faction-Limits-Version') != '2':
            raise PanelError('frontend_outdated')
        if kind == 'items' and any(item.itemId not in request.app.state.item_catalog.items for item in payload.items):
            raise PanelError('invalid_selection')
        if payload.enabled:
            if not payload.acknowledgeRisk or payload.password is None or not request.app.state.settings.history_enabled:
                raise PanelError('invalid_selection')
            request.app.state.auth_service.verify_current_password(request, payload.password.get_secret_value())
            if kind == 'items' and (not request.app.state.settings.feed_token or request.app.state.settings.feed_origin != origin):
                raise PanelError('invalid_selection')
            spec = write_route_for(WriteName.CHANGE_FACTION if kind == 'factions' else WriteName.KILL)
            await runtime.capabilities.require_advertised(spec.method, spec.path)
        settings = payload.model_dump(exclude={'targetRevision', 'acknowledgeRisk', 'password'})
        request.app.state.database.append_moderation_audit(actor.id, 'gameRulesSettings_' + kind, '', 'accepted',
            'enabled=' + str(payload.enabled) + '; sha256=' + sha256(json.dumps(settings).encode()).hexdigest(),
            request.state.request_id, runtime.target_revision, origin)
        request.app.state.game_rules_store.save(origin, kind, revision=runtime.target_revision, actor=actor.id, settings=settings)
        if kind == 'factions':
            request.app.state.game_rules_engine.reset()
    return {'ok': True}


@router.put('/factions')
async def save_factions(payload: FactionBody, request: Request):
    return await _save(payload, 'factions', request)


@router.put('/items')
async def save_items(payload: ItemBody, request: Request):
    return await _save(payload, 'items', request)
