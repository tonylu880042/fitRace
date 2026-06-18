import asyncio
import json
import logging

from hub_server.domain.models import RaceState

logger = logging.getLogger("hub_server.mqtt_subscriber")


class MqttSubscriber:
    def __init__(
        self,
        async_mqtt_client,
        race_manager,
        ws_manager,
        node_registry=None,
        race_event_engine=None,
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

        progress = self._race_manager.ingest_telemetry(payload)
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
                state_change["type"] = "state_change"
                await self._ws_manager.broadcast(state_change)
        else:
            # Trigger frontend refresh for new active node discovery
            await self._ws_manager.broadcast({})
