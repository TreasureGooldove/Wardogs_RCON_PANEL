"""Authenticated panel release information."""

from fastapi import APIRouter, Depends, Request, Response
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from app.api.auth import require_admin
from app.errors import PanelError
from app.upgrade import atomic_json, directory, enqueue, update_state

router = APIRouter(prefix="/api/panel", dependencies=[Depends(require_admin)])


@router.get("/updates")
async def updates(request: Request, response: Response, refresh: bool = False,
                  source: Literal['auto', 'github', 'gitee'] = 'auto'):
    response.headers["Cache-Control"] = "no-store"
    return await request.app.state.release_checker.check(refresh=refresh, source=source)


def owner(request):
    request.app.state.auth_service.require_owner(request)


@router.get('/updates/state')
async def state(request: Request, response: Response):
    owner(request)
    response.headers['Cache-Control'] = 'no-store'
    return update_state(request.app.state.settings)


class InstallRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: Literal['auto', 'github', 'gitee'] = 'auto'
    password: str = Field(min_length=1, max_length=256)


class PolicyRequest(InstallRequest):
    autoInstall: StrictBool = False


def verify(request, password):
    owner(request)
    request.app.state.auth_service.check_origin(request)
    request.app.state.auth_service.verify_current_password(request, password)


@router.post('/updates/install')
async def install(payload: InstallRequest, request: Request):
    verify(request, payload.password)
    release = await request.app.state.release_checker.check(refresh=True, source=payload.source)
    if not release['updateAvailable'] or not release['installable']:
        raise PanelError('update_not_available')
    return enqueue(request.app.state.settings, payload.source, release['latestVersion'])


@router.put('/updates/policy')
async def policy(payload: PolicyRequest, request: Request):
    verify(request, payload.password)
    settings = request.app.state.settings
    if payload.autoInstall and not update_state(settings)['agentAvailable']:
        raise PanelError('updater_unavailable')
    value = {'autoInstall': payload.autoInstall, 'source': payload.source}
    atomic_json(directory(settings) / 'policy.json', value)
    return value
