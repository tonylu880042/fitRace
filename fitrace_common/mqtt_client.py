"""One async MQTT client for both the Edge Node runtime and the Central Hub.

Both used to carry their own byte-identical copy, which is how the reconnect
defects fixed here got repaired on one side and left in place on the other --
see tests/unit/common/test_mqtt_client_reconnect.py for what each of them
cost in the field.
"""

import asyncio
import logging
import paho.mqtt.client as mqtt

DEFAULT_LOGGER_NAME = "fitrace.mqtt_client"

# How long a publish waits for the connection to come back before giving the
# sample up. Telemetry is a 1 Hz stream: a sample that cannot be sent now is
# worth less than the memory a queue of awaiting publishes would cost.
DEFAULT_PUBLISH_TIMEOUT_SEC = 5.0


class AsyncMqttClient:
    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        logger_name: str = DEFAULT_LOGGER_NAME,
    ):
        # Each service keeps its own logger name so existing journal filters
        # (edge_node.mqtt_client / hub_server.mqtt_client) keep matching.
        self._logger = logging.getLogger(logger_name)
        self._host = host
        self._port = port
        self._client_id = client_id
        self._loop: asyncio.AbstractEventLoop | None = None
        # Topics this node wants, kept because paho does not restore
        # subscriptions after a reconnect -- see _on_connect.
        self._subscriptions: list[str] = []

        # Support both paho-mqtt v2.x and v1.x callback signatures
        try:
            self._client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self._client_id,
            )
        except AttributeError:
            self._client = mqtt.Client(client_id=self._client_id)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._connected = asyncio.Event()

    # -- callbacks: these run on the paho network thread ---------------------
    #
    # asyncio.Event is not thread-safe, so touching it here directly leaves
    # the waiting coroutine unscheduled: MQTT is back, every publish is still
    # blocked, and only restarting the process clears it. Everything below
    # hands the state change to the event loop instead.

    def _apply_on_loop(self, action):
        loop = self._loop
        if loop is None or not loop.is_running():
            # No loop yet (constructed but never connected, or already shut
            # down) -- nothing is awaiting the event, so set it directly.
            action()
            return
        loop.call_soon_threadsafe(action)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self._logger.info(f"MQTT Connected with code {reason_code}")
        # Support reason_code checking (0 is success)
        if getattr(reason_code, "value", reason_code) == 0:
            # Re-subscribe first: a reconnect gives paho a clean session, and
            # a node that silently stops receiving fitrace/nodes/+/command
            # looks perfectly healthy from the outside.
            for topic in list(self._subscriptions):
                self._client.subscribe(topic)
            self._apply_on_loop(self._connected.set)
        else:
            self._logger.error(f"Failed to connect, reason code: {reason_code}")

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties=None
    ):
        self._logger.warning(f"MQTT Disconnected with code {reason_code}")
        self._apply_on_loop(self._connected.clear)

    # -- connection ----------------------------------------------------------

    async def connect(self, timeout_sec: float = 10.0):
        """Start the connection and wait a little for it to establish.

        Never raises on an absent broker: connect_async plus loop_start keeps
        retrying in the background, so a hub that is not up yet when the edge
        boots (or that restarts later) is a delay, not a permanent standalone
        node that needs a reboot to rejoin.
        """
        self._logger.info(f"Connecting to MQTT broker at {self._host}:{self._port}")
        self._loop = asyncio.get_running_loop()
        self._client.connect_async(self._host, self._port)
        self._client.loop_start()
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout_sec)
            self._logger.info("Successfully established connection to MQTT broker")
        except asyncio.TimeoutError:
            self._logger.warning(
                "MQTT broker at %s:%s not reachable yet; retrying in the "
                "background and publishing as soon as it answers",
                self._host,
                self._port,
            )

    def subscribe(self, topic: str):
        """Subscribe now, and again after every reconnect.

        Sent unconditionally rather than only when connected: paho answers
        with an error code while offline (nothing breaks), and that avoids
        racing the connected flag, which is set by the network thread.
        """
        if topic not in self._subscriptions:
            self._subscriptions.append(topic)
        self._client.subscribe(topic)

    async def wait_connected(self, timeout_sec: float | None = None):
        if timeout_sec is None:
            await self._connected.wait()
            return
        await asyncio.wait_for(self._connected.wait(), timeout=timeout_sec)

    async def publish(
        self, topic: str, payload: str, timeout_sec: float = DEFAULT_PUBLISH_TIMEOUT_SEC
    ):
        try:
            await self.wait_connected(timeout_sec)
        except asyncio.TimeoutError:
            raise ConnectionError(
                f"MQTT broker not connected after {timeout_sec}s; dropped {topic}"
            )
        info = self._client.publish(topic, payload, qos=1)
        # Wait for the message to be published
        while not info.is_published():
            if not self._connected.is_set():
                raise ConnectionError("Disconnected while publishing message")
            await asyncio.sleep(0.05)

    async def disconnect(self):
        self._logger.info("Disconnecting from MQTT broker")
        self._client.disconnect()
        self._client.loop_stop()
