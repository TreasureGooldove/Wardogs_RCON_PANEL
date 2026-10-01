"""Owner-controlled experimental automation with explicit risk acknowledgement."""
import json
from hashlib import sha256

from fastapi import APIRouter, Depends, Request, Response
from pydantic import Field, SecretStr, StrictBool

from app.api.auth import require_admin
from app.api.community import _check, _target
from app.errors import PanelError
from app.history.anticheat import AntiSettings
from app.rcon.players import normalize_players
from app.rcon.routes import RouteName, WriteName

router = APIRouter(prefix='/api/anticheat', dependencies=[Depends(require_admin)])


class SettingsBody(AntiSettings):
    targetRevision: str = Field(min_length=1, max_length=128)
    acknowledgeRisk: StrictBool = False
    password: SecretStr | None = None


@router.get('')
async def view(request: Request, response: Response):
    origin, revision = await _target(request, response)
    return {**request.app.state.anticheat_store.view(origin, revision), 'targetRevision': revision}


@router.put('/settings')
async def save(payload: SettingsBody, request: Request):
    runtime = request.app.state.rcon_runtime
    async with runtime.lock:
        origin, actor = _check(request, payload.targetRevision, 'notes')
        if actor.role != 'owner':
            raise PanelError('permission_denied')
        if payload.enabled:
            if not payload.acknowledgeRisk or payload.password is None:
                raise PanelError('invalid_selection')
            request.app.state.auth_service.verify_current_password(request, payload.password.get_secret_value())
            if payload.action != 'alert':
                await runtime.capabilities.require_write(WriteName(payload.action))
        settings = payload.model_dump(exclude={'targetRevision', 'acknowledgeRisk', 'password'})
        request.app.state.database.append_moderation_audit(actor.id, 'anticheatSettings', '', 'accepted',
            'enabled=' + str(payload.enabled) + '; action=' + payload.action + '; sha256=' + sha256(json.dumps(settings).encode()).hexdigest(), request.state.request_id, runtime.target_revision, origin)
        request.app.state.anticheat_store.save(origin, runtime.target_revision, actor.id, settings)
    return {'ok': True}


class AntiEngine:
    def __init__(self, runtime, store):
        self.runtime, self.store = runtime, store

    async def evaluate(self):
        runtime = self.runtime
        async with runtime.lock:
            if runtime.target is None:
                return
            origin = runtime.target.origin
            config = self.store.config(origin)
            if not config or config['revision'] != runtime.target_revision:
                return
            settings = AntiSettings.model_validate(json.loads(config['settings']))
            if not settings.enabled:
                return
            actor = self.store.db.get_admin_by_id(config['actor'])
            if actor is None or actor.disabled or actor.role != 'owner':
                return
            findings = self.store.detect(origin, config)
            self.store.record(origin, findings)
            if settings.action == 'alert':
                return
            if not findings:
                return
            action = WriteName(settings.action)
            try:
                await runtime.capabilities.require_write(action)
                await runtime.capabilities.require_advertised('GET', '/v1/players')
                roster = normalize_players(await runtime.client.request(RouteName.PLAYERS))['players']
            except PanelError:
                return
            online = {p.get('steamId') for p in roster}
            for finding in findings:
                if finding['steamId'] not in online:
                    continue
                key = self.store.claim(origin, finding, settings.action)
                if not key:
                    continue
                reason = 'Experimental anti-cheat suspicion: ' + finding['rule']
                outcome = 'uncertain'
                try:
                    # Never replay an ambiguous write, including after a process restart.
                    await runtime.client.moderate(action, finding['steamId'], reason)
                    outcome = 'accepted'
                    runtime.invalidate_players_unlocked()
                except PanelError as exc:
                    outcome = 'uncertain' if exc.code == 'action_uncertain' else 'rejected'
                finally:
                    self.store.finish(key, outcome)
                    self.store.db.append_moderation_audit(actor.id, 'anticheat_' + settings.action,
                        finding['steamId'], outcome, reason + '; evidence=' + finding['fingerprint'], key, runtime.target_revision, origin)
