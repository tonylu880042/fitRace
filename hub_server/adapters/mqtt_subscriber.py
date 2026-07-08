import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from hub_server.domain.models import RaceState

logger = logging.getLogger("hub_server.mqtt_subscriber")


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
        hyrox_service=None,
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
        self._hyrox_service = hyrox_service
        self._loop = asyncio.get_event_loop()

    def start_listening(self):
        """
        Configures callbacks and subscribes to telemetry and node status topics.
        """
        logger.info("Setting up MQTT subscriptions")
        self._mqtt_client._client.on_message = self._on_message
        self._mqtt_client._client.subscribe("gym/telemetry/#")
        self._mqtt_client._client.subscribe("fitrace/nodes/+/status")

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
            elif topic.startswith("gym/telemetry/rfid/"):
                future = self._handle_rfid(payload)
            elif topic.startswith("gym/telemetry/ftms/"):
                future = self._handle_ftms(payload)
            elif topic.startswith("gym/telemetry/wallball/"):
                future = self._handle_wallball(payload)
            elif topic.startswith("gym/telemetry/abandon/"):
                future = self._handle_abandon(payload)
            else:
                future = self._handle_telemetry(payload)

            asyncio.run_coroutine_threadsafe(future, self._loop)
        except Exception as e:
            logger.error(f"Failed to process incoming MQTT payload: {e}")

    async def _handle_rfid(self, payload: dict):
        # Resource-aware RFID: node_id + antenna_id address a read zone,
        # tag_id self-identifies the athlete. The Hyrox service resolves the
        # zone to a resource via the venue config.
        if not self._hyrox_service:
            return
        node_id = payload.get("node_id")
        antenna_id = payload.get("antenna_id")
        tag_id = payload.get("tag_id")
        timestamp_ms = payload.get("timestamp_epoch_ms")
        rssi = payload.get("rssi")

        # Spillover filter (cross-talk prevention) at the physical layer.
        if rssi is not None and float(rssi) < -60.0:
            return

        if node_id and antenna_id and tag_id:
            self._hyrox_service.ingest_rfid(
                str(node_id), str(antenna_id), str(tag_id),
                int(timestamp_ms) if timestamp_ms else None,
            )

    async def _handle_ftms(self, payload: dict):
        # FTMS machines (treadmill/row/ski): anonymous distance stream keyed by
        # node; attribution comes from the resource's active assignment.
        if not self._hyrox_service:
            return
        node_id = payload.get("node_id")
        timestamp_ms = payload.get("timestamp_epoch_ms")
        if node_id is None:
            return
        metrics = None
        if "distance_m" in payload:
            metrics = {"distance_m": float(payload["distance_m"])}
        self._hyrox_service.ingest_node(
            str(node_id), metrics=metrics,
            timestamp_ms=int(timestamp_ms) if timestamp_ms else None,
        )

    async def _handle_wallball(self, payload: dict):
        # A valid wall-ball rep is a node-addressed rep-counter event.
        if not self._hyrox_service:
            return
        node_id = payload.get("node_id")
        timestamp_ms = payload.get("timestamp_epoch_ms")
        if node_id:
            self._hyrox_service.ingest_node(
                str(node_id), metrics=None,
                timestamp_ms=int(timestamp_ms) if timestamp_ms else None,
            )

    async def _handle_abandon(self, payload: dict):
        # Abandon button co-located with a reader; the read tag identifies who.
        if not self._hyrox_service:
            return
        tag_id = payload.get("tag_id")
        timestamp_ms = payload.get("timestamp_epoch_ms")
        if tag_id:
            self._hyrox_service.abandon_by_tag(
                str(tag_id), int(timestamp_ms) if timestamp_ms else None
            )

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
            await self._ws_manager.broadcast(progress)
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
                state_change = self._race_manager.get_state_snapshot()
                if self._race_result_store:
                    self._race_result_store.save_finished_snapshot(state_change)
                state_change["type"] = "state_change"
                await self._ws_manager.broadcast(state_change)
        else:
            # Trigger frontend refresh for new active node discovery
            await self._ws_manager.broadcast({})
