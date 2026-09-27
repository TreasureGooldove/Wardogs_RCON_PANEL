"""Panel-owned reserved-slot expiry. Never repeats an uncertain config write."""

import asyncio
from datetime import datetime, timezone
import logging

from app.errors import PanelError
from app.rcon.config_doc import read_config, replace_reserved_ids, reserved_ids_from_text, send_config
from app.rcon.routes import RouteName, WriteName, route_for, write_route_for


_LOG = logging.getLogger(__name__)


class ReservationExpirer:
    def __init__(self, runtime, database) -> None:
        self.runtime = runtime
        self.database = database

    async def run_once(self) -> None:
        runtime = self.runtime
        async with runtime.lock:
            target = runtime.client.target
            if target is None:
                return
            origin = target.origin
            for steam_id in self.database.due_reserved_slots(origin, datetime.now(timezone.utc)):
                try:
                    read_spec = route_for(RouteName.CONFIG)
                    write_spec = write_route_for(WriteName.CONFIG_APPLY)
                    await runtime.capabilities.require_advertised(read_spec.method, read_spec.path)
                    await runtime.capabilities.require_advertised(write_spec.method, write_spec.path)
                    document = await read_config(runtime.client)
                    if not document["writable"]:
                        return
                    configured = reserved_ids_from_text(document["text"])
                    if steam_id not in configured:
                        self.database.remove_reserved_metadata(origin, steam_id)
                        continue
                    updated = replace_reserved_ids(
                        document["text"], [item for item in configured if item != steam_id]
                    )
                    if not self.database.mark_reserved_status(origin, steam_id, "active", "processing"):
                        continue
                    try:
                        await send_config(
                            runtime.client, updated, apply=True, revision=document["revision"]
                        )
                    except Exception:
                        self.database.mark_reserved_status(origin, steam_id, "processing", "attention")
                        raise
                    self.database.remove_reserved_metadata(origin, steam_id)
                    runtime.read_service.invalidate(RouteName.ROTATION)
                except PanelError as exc:
                    _LOG.warning("Reserved-slot expiry needs attention: code=%s", exc.code)
                except Exception:
                    _LOG.exception("Reserved-slot expiry needs attention")

    async def run(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(30)
