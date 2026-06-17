import json
import logging
import asyncio
from hub_server.domain.models import RaceState

logger = logging.getLogger("hub_server.mqtt_subscriber")


class MqttSubscriber:
    def __init__(self, async_mqtt_client, race_manager, ws_manager, node_registry=None):
        """
        :param async_mqtt_client: An instance of our AsyncMqttClient.
        :param race_manager: Instance of RaceManager.
        :param ws_manager: Instance of WebSocketManager.
        """
        self._mqtt_client = async_mqtt_client
        self._race_manager = race_manager
        self._ws_manager = ws_manager
        self._node_registry = node_registry
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
                edge_node_id = topic.removeprefix("fitrace/nodes/").removesuffix("/status")
                future = self._handle_node_status(payload, edge_node_id)
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

    async def _handle_telemetry(self, payload: dict):
        node_id = payload.get("node_id")
        if not node_id:
            return

        eq_type = payload.get("equipment_type", "unknown")
        self._race_manager.update_active_node(node_id, eq_type)

        # Only process telemetry if the race is actively RUNNING
        if self._race_manager.get_state() == RaceState.RUNNING:
            # Auto-register node if not registered to handle mock nodes joining dynamically
            if node_id not in self._race_manager.get_registered_nodes():
                self._race_manager._registered_nodes[node_id] = f"Athlete {node_id}"
                # Initialize progress
                self._race_manager._progress[node_id] = {
                    "node_id": node_id,
                    "athlete_name": f"Athlete {node_id}",
                    "distance_m": 0.0,
                    "elapsed_time_ms": 0,
                    "instantaneous_speed_kph": 0.0,
                    "progress_percent": 0.0,
                }

            progress = self._race_manager.update_telemetry(payload)
            await self._ws_manager.broadcast(progress)
            logger.debug(f"Broadcasted telemetry update for node: {node_id}")

            # If the race state just transitioned to STOPPED, broadcast state change
            if self._race_manager.get_state() == RaceState.STOPPED:
                config = self._race_manager.get_config()
                await self._ws_manager.broadcast({
                    "type": "state_change",
                    "state": self._race_manager.get_state().value,
                    "config": config.model_dump() if config else None,
                    "registered_nodes": self._race_manager.get_registered_nodes(),
                    "start_time_epoch_ms": self._race_manager._start_time_epoch_ms,
                    "end_time_epoch_ms": getattr(self._race_manager, "_end_time_epoch_ms", None),
                })
        else:
            # Trigger frontend refresh for new active node discovery
            await self._ws_manager.broadcast({})
