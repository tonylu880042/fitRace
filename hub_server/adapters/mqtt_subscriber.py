import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from hub_server.domain.models import RaceState
from hub_server.usecases.binding_removal_reconciler import (
    stations_to_unassign_for_removed_bindings,
)
from hub_server.usecases.node_display_names import (
    enrich_progress_display_names,
    enrich_race_state_display_names,
)

logger = logging.getLogger("hub_server.mqtt_subscriber")


class BindingsRemovedPayload(BaseModel):
    edge_node_id: str = Field(..., min_length=1)
    removed_node_ids: list[str] = Field(default_factory=list)


class TelemetryPayload(BaseModel):
    node_id: str = Field(..., min_length=1)
    edge_node_id: str | None = None
    mac_address: str | None = None
    equipment_id: str | None = None
    equipment_type: str = "unknown"
    ftms_type: str | None = None
    rssi: int | float | None = None
    instantaneous_speed_kph: float = Field(0.0, ge=0.0)
    cadence_rpm: int = Field(0, ge=0)
    pace_sec_per_500m: int | float | None = Field(None, ge=0)
    power_watts: int = Field(0, ge=0)
    heart_rate_bpm: int = Field(0, ge=0)
    distance_m: float = Field(0.0, ge=0.0)
    raw_total_distance_m: float | None = Field(None, ge=0.0)
    delta_distance_m: float | None = Field(None, ge=0.0)
    total_energy_kcal: int | None = Field(None, ge=0)
    elapsed_time_ms: int = Field(0, ge=0)
    calories: float | None = Field(None, ge=0.0)
    raw_total_energy_kcal: float | None = Field(None, ge=0.0)
    delta_energy_kcal: float | None = Field(None, ge=0.0)
    timestamp_epoch_ms: int | None = Field(None, ge=0)
    ftms_payload: dict[str, Any] | None = None
    raw_payload: dict[str, Any] | None = None


class MqttSubscriber:
    def __init__(
        self,
        async_mqtt_client,
        race_manager,
        ws_manager,
        node_registry=None,
        race_event_engine=None,
        race_result_store=None,
    ):
        """
        :param async_mqtt_client: An instance of AsyncMqttClient.
        :param race_manager: Instance of RaceManager.
        :param ws_manager: Instance of WebSocketManager.
        :param race_event_engine: Optional RaceEventEngine for generating race events.
        """
        self._mqtt_client = async_mqtt_client
        self._race_manager = race_manager
        self._ws_manager = ws_manager
        self._node_registry = node_registry
        self._race_event_engine = race_event_engine
        self._race_result_store = race_result_store
        self._loop = asyncio.get_event_loop()

    def _registered_nodes(self) -> list[dict]:
        list_nodes = getattr(self._node_registry, "list_nodes", None)
        return list_nodes() if callable(list_nodes) else []

    def start_listening(self):
        """
        Configures callbacks and subscribes to telemetry and node status topics.
        """
        logger.info("Setting up MQTT subscriptions")
        self._mqtt_client._client.on_message = self._on_message
        self._mqtt_client._client.subscribe("gym/telemetry/#")
        self._mqtt_client._client.subscribe("fitrace/nodes/+/status")
        self._mqtt_client._client.subscribe("fitrace/nodes/+/bindings_removed")

    def _on_message(self, client, userdata, message):
        """
        Thread-safe callback triggered by the MQTT background thread.
        """
        try:
            payload_str = message.payload.decode("utf-8")
            payload = json.loads(payload_str)
            topic = getattr(message, "topic", "")

            if topic.startswith("fitrace/nodes/") and topic.endswith("/status"):
                edge_node_id = topic.removeprefix("fitrace/nodes/").removesuffix(
                    "/status"
                )
                future = self._handle_node_status(payload, edge_node_id)
            elif topic.startswith("fitrace/nodes/") and topic.endswith(
                "/bindings_removed"
            ):
                edge_node_id = topic.removeprefix("fitrace/nodes/").removesuffix(
                    "/bindings_removed"
                )
                future = self._handle_bindings_removed(payload, edge_node_id)
            else:
                future = self._handle_telemetry(payload)

            asyncio.run_coroutine_threadsafe(future, self._loop)
        except Exception as e:
            logger.error(f"Failed to process incoming MQTT payload: {e}")

    async def _handle_node_status(self, payload: dict, edge_node_id: str):
        if not self._node_registry:
            return

        status = self._node_registry.update_status(payload, edge_node_id=edge_node_id)
        await self._ws_manager.broadcast(
            {
                "type": "node_status",
                "node": status.model_dump(),
                "nodes": self._node_registry.list_nodes(),
            }
        )

    async def _handle_bindings_removed(self, payload: dict, edge_node_id: str):
        """React to an Edge's one-shot `bindings_removed` event.

        This is the ONLY trigger that may unassign a station for equipment
        removal -- never the periodic node_status heartbeat, which can go
        missing or empty on any Wi-Fi hiccup and must never be interpreted
        as an operator removing equipment (see
        test_node_status_heartbeat_never_triggers_station_unassignment).
        """
        try:
            event = BindingsRemovedPayload.model_validate(payload)
        except ValidationError as e:
            logger.warning("Rejected invalid bindings_removed payload: %s", e)
            return

        if not event.removed_node_ids:
            return

        race_state = self._race_manager.get_state()
        stations = self._race_manager.get_station_node_ids()
        stations_to_unassign = stations_to_unassign_for_removed_bindings(
            event.removed_node_ids,
            stations,
            race_state,
        )

        if race_state == RaceState.RUNNING:
            logger.warning(
                "Ignoring bindings_removed from edge %s (removed_node_ids=%s): "
                "race is RUNNING, so station unassignment is blocked until it "
                "stops. Unassign manually once the race is no longer RUNNING.",
                edge_node_id,
                event.removed_node_ids,
            )
            return

        if not stations_to_unassign:
            return

        for station_number in stations_to_unassign:
            node_id = stations.get(station_number)
            logger.info(
                "Auto-unassigning station %s (node_id=%s) after bindings_removed "
                "from edge %s",
                station_number,
                node_id,
                edge_node_id,
            )
            self._race_manager.assign_station(station_number, None)

        state_change = enrich_race_state_display_names(
            self._race_manager.get_state_snapshot(),
            self._registered_nodes(),
        )
        state_change["type"] = "state_change"
        await self._ws_manager.broadcast(state_change)

    async def _handle_telemetry(self, payload: dict):
        try:
            telemetry = TelemetryPayload.model_validate(payload)
        except ValidationError as e:
            logger.warning("Rejected invalid MQTT telemetry payload: %s", e)
            return

        telemetry_payload = telemetry.model_dump(exclude_none=True)
        node_id = telemetry_payload["node_id"]
        if self._node_registry:
            self._node_registry.update_telemetry(telemetry_payload)

        progress = self._race_manager.ingest_telemetry(telemetry_payload)
        if progress is not None:
            display_progress = enrich_progress_display_names(
                progress,
                self._registered_nodes(),
            )
            await self._ws_manager.broadcast(display_progress)
            logger.debug(f"Broadcasted telemetry update for node: {node_id}")

            # Check and broadcast race events
            if self._race_event_engine:
                events = self._race_event_engine.evaluate(self._race_manager, progress)
                for event in events:
                    await self._ws_manager.broadcast(
                        {
                            "type": "race_event",
                            "event": event,
                        }
                    )

            # If the race state just transitioned to STOPPED, broadcast state change
            if self._race_manager.get_state() == RaceState.STOPPED:
                state_change = enrich_race_state_display_names(
                    self._race_manager.get_state_snapshot(),
                    self._registered_nodes(),
                )
                if self._race_result_store:
                    self._race_result_store.save_finished_snapshot(state_change)
                state_change["type"] = "state_change"
                await self._ws_manager.broadcast(state_change)
        else:
            # Trigger frontend refresh for new active node discovery
            await self._ws_manager.broadcast({})
