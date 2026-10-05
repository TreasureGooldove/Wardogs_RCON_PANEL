"""Owner-confirmed persistent emergency RCON stop, independent of the I/O lock."""
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from app.api.auth import require_admin, require_owner

router = APIRouter(prefix='/api/server/connection', tags=['connection'])


class ConnectionCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: SecretStr = Field(min_length=1, max_length=256)


@router.get('', dependencies=[Depends(require_admin)])
async def state(request: Request, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return request.app.state.rcon_runtime.connection_state()


async def change(payload, request, actor, paused):
    auth = request.app.state.auth_service
    auth.check_origin(request)
    auth.verify_current_password(request, payload.password.get_secret_value())
    runtime = request.app.state.rcon_runtime
    result = await (runtime.stop_connection() if paused else runtime.resume_connection())
    if paused:
        request.app.state.rules_engine.reset()
        request.app.state.game_rules_engine.reset()
        request.app.state.warmup_engine.reset_observation()
    request.app.state.database.append_moderation_audit(actor.id,
        'rconEmergencyStop' if paused else 'rconResume', '', 'accepted', '',
        request.state.request_id, runtime.target_revision,
        runtime.client.target.origin if runtime.client.target else '')
    return result


@router.post('/stop')
async def stop(payload: ConnectionCommand, request: Request, actor=Depends(require_owner)):
    return await change(payload, request, actor, True)


@router.post('/resume')
async def resume(payload: ConnectionCommand, request: Request, actor=Depends(require_owner)):
    return await change(payload, request, actor, False)
