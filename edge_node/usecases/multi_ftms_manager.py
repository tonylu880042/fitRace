import asyncio
import logging
from typing import Awaitable, Callable

from edge_node.domain.models import EquipmentBinding, TelemetryData

logger = logging.getLogger("edge_node.multi_ftms_manager")


class MultiFtmsManager:
    def __init__(
        self,
        edge_node_id: str,
        bindings: list[EquipmentBinding],
        client_factory: Callable[
            [EquipmentBinding, Callable[[TelemetryData], Awaitable[None]]],
            object,
        ],
        on_telemetry: Callable[[TelemetryData], Awaitable[None]],
        max_connections: int = 5,
    ):
        if max_connections < 1 or max_connections > 10:
            raise ValueError("max_connections must be between 1 and 10")
        if len(bindings) > max_connections:
            raise ValueError("equipment bindings exceed max_connections")

        self._edge_node_id = edge_node_id
        self._bindings = bindings
        self._client_factory = client_factory
        self._on_telemetry = on_telemetry
        self._clients = []

    @property
    def connection_count(self) -> int:
        return len(self._bindings)

    async def start(self):
        self._clients = []
        for binding in self._bindings:
            client = self._client_factory(binding, self._handle_telemetry)
            self._clients.append(client)

        try:
            await asyncio.gather(*(client.start() for client in self._clients))
        except Exception:
            await self.stop()
            raise

        logger.info(
            "Started %s FTMS connection(s) for %s",
            len(self._clients),
            self._edge_node_id,
        )

    async def stop(self):
        if not self._clients:
            return
        await asyncio.gather(
            *(client.stop() for client in self._clients),
            return_exceptions=True,
        )
        self._clients = []

    async def _handle_telemetry(self, telemetry: TelemetryData):
        if telemetry.edge_node_id is None:
            telemetry = telemetry.model_copy(update={"edge_node_id": self._edge_node_id})
        await self._on_telemetry(telemetry)
