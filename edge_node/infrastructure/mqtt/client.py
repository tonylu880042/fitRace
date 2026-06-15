import asyncio
import logging
import paho.mqtt.client as mqtt

logger = logging.getLogger("edge_node.mqtt_client")


class AsyncMqttClient:
    def __init__(self, host: str, port: int, client_id: str):
        self._host = host
        self._port = port
        self._client_id = client_id

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

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        logger.info(f"MQTT Connected with code {reason_code}")
        # Support reason_code checking (0 is success)
        if getattr(reason_code, "value", reason_code) == 0:
            self._connected.set()
        else:
            logger.error(f"Failed to connect, reason code: {reason_code}")

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties=None
    ):
        logger.warning(f"MQTT Disconnected with code {reason_code}")
        self._connected.clear()

    async def connect(self):
        logger.info(f"Connecting to MQTT broker at {self._host}:{self._port}")
        self._client.connect_async(self._host, self._port)
        self._client.loop_start()
        # Wait up to 10 seconds for initial connection
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=10.0)
            logger.info("Successfully established connection to MQTT broker")
        except asyncio.TimeoutError:
            logger.error("Timeout waiting for MQTT connection")
            raise ConnectionError("Failed to connect to MQTT broker within timeout")

    async def wait_connected(self):
        await self._connected.wait()

    async def publish(self, topic: str, payload: str):
        await self.wait_connected()
        info = self._client.publish(topic, payload, qos=1)
        # Wait for the message to be published
        while not info.is_published():
            if not self._connected.is_set():
                raise ConnectionError("Disconnected while publishing message")
            await asyncio.sleep(0.05)

    async def disconnect(self):
        logger.info("Disconnecting from MQTT broker")
        self._client.disconnect()
        self._client.loop_stop()
