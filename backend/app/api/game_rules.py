"""Read-only rule views and owner-confirmed automation configuration."""
import json
from hashlib import sha256

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, SecretStr, StrictBool

from app.api.auth import require_admin
from app.api.community import _check, _target
from app.errors import PanelError
from app.game_rules.models import CATALOG, FactionSettings, ItemSettings
from app.rcon.routes import WriteName

router = APIRouter(prefix='/api/game-rules', dependencies=[Depends(require_admin)], tags=['game rules'])


class FactionBody(FactionSettings):
    targetRevision: str = Field(min_length=1, max_length=128)
    acknowledgeRisk: StrictBool = False
    password: SecretStr | None = None


class ItemBody(ItemSettings):
    targetRevision: str = Field(min_length=1, max_length=128)
    acknowledgeRisk: StrictBool = False
    password: SecretStr | None = None


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
            'items': store.view(origin, 'items', revision), 'observation': observation,
            'evidence': store.evidence_view(origin), 'historyEnabled': request.app.state.settings.history_enabled,
            'feedConfigured': bool(request.app.state.settings.feed_token and request.app.state.settings.feed_origin == origin),
            'equipmentInventoryAvailable': False, 'catalog': CATALOG}


async def _save(payload, kind, request):
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        origin, actor = _check(request, payload.targetRevision, 'changeFaction' if kind == 'factions' else 'kill')
        if actor.role != 'owner':
            raise PanelError('permission_denied')
        if payload.enabled:
            if not payload.acknowledgeRisk or payload.password is None or not request.app.state.settings.history_enabled:
                raise PanelError('invalid_selection')
            request.app.state.auth_service.verify_current_password(request, payload.password.get_secret_value())
            if kind == 'items' and (not request.app.state.settings.feed_token or request.app.state.settings.feed_origin != origin):
                raise PanelError('invalid_selection')
            await runtime.capabilities.require_write(WriteName.CHANGE_FACTION if kind == 'factions' else WriteName.KILL)
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
