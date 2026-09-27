"""Wardogs panel application: local sessions and managed RCON routes."""

from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from app.api.auth import build_auth_router
from app.api.actions import router as actions_router
from app.api.capabilities import router as capabilities_router
from app.api.catalog import router as catalog_router
from app.api.config_doc import router as config_doc_router
from app.api.diagnostics import router as diagnostics_router
from app.api.history import router as history_router
from app.api.moderation import router as moderation_router
from app.api.players import router as players_router
from app.api.reserved_slots import router as reserved_slots_router
from app.api.rules import router as rules_router
from app.api.rotation import router as rotation_router
from app.api.settings import router as settings_router
from app.api.steam import router as steam_router
from app.api.status import router as status_router
from app.api.subusers import router as subusers_router
from app.auth.sessions import AuthService
from app.config import PanelSettings, load_settings
from app.errors import install_error_handlers
from app.history.collector import HistoryCollector
from app.history.store import HistoryStore
from app.rcon.runtime import RconRuntime, RuntimeCapabilityService, RuntimeReadService
from app.reservations import ReservationExpirer
from app.rules.engine import RulesEngine
from app.rules.store import RulesStore
from app.storage.db import Database
from app.steam.service import SteamProfileService


class FrontendFiles(StaticFiles):
    """Serve the compiled Vue app at the same origin as the panel API."""

    async def get_response(self, path: str, scope: dict) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if (
                exc.status_code == 404
                and path
                and not scope.get("path", "").startswith("/api/")
                and not Path(path).suffix  # History-mode route, never an absent asset.
            ):
                return await super().get_response("index.html", scope)
            raise


def create_app(
    settings: PanelSettings | None = None,
    *,
    rcon_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    database = Database(settings.db_path)
    database.initialize()
    history_store = HistoryStore(database)
    history_store.initialize()
    rules_store = RulesStore(database)
    rules_store.initialize()
    auth_service = AuthService(database, settings)
    rcon_runtime = RconRuntime(settings, database, transport=rcon_transport)
    steam_service = SteamProfileService.from_environment()
    rules_engine = RulesEngine(rcon_runtime, rules_store)
    history_collector = HistoryCollector(rcon_runtime, history_store, rules_engine)
    reservation_expirer = ReservationExpirer(rcon_runtime, database)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        history_task = (
            asyncio.create_task(history_collector.run()) if settings.history_enabled else None
        )
        rules_task = asyncio.create_task(rules_engine.run())
        reservation_task = asyncio.create_task(reservation_expirer.run())
        try:
            yield
        finally:
            rules_task.cancel()
            reservation_task.cancel()
            try:
                await rules_task
            except asyncio.CancelledError:
                pass
            try:
                await reservation_task
            except asyncio.CancelledError:
                pass
            if history_task is not None:
                history_task.cancel()
                try:
                    await history_task
                except asyncio.CancelledError:
                    pass
            await rcon_runtime.close()
            await steam_service.close()

    app = FastAPI(title="Wardogs RCON Panel", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.auth_service = auth_service
    app.state.rcon_runtime = rcon_runtime
    app.state.rcon_client = rcon_runtime
    app.state.steam_service = steam_service
    app.state.history_store = history_store
    app.state.history_collector = history_collector
    app.state.rules_store = rules_store
    app.state.rules_engine = rules_engine
    app.state.reservation_expirer = reservation_expirer
    app.state.capability_service = RuntimeCapabilityService(rcon_runtime)
    app.state.read_service = RuntimeReadService(rcon_runtime)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    install_error_handlers(app)
    app.include_router(build_auth_router(auth_service))
    for router in (
        capabilities_router, status_router, players_router,
        rotation_router, catalog_router, settings_router, moderation_router,
        subusers_router, config_doc_router, reserved_slots_router, actions_router,
        diagnostics_router,
        history_router,
        rules_router,
        steam_router,
    ):
        app.include_router(router)

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (frontend_dist / "index.html").is_file():
        app.mount("/", FrontendFiles(directory=frontend_dist, html=True), name="frontend")
    return app


app = create_app()
