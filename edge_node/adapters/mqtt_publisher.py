import json
from edge_node.domain.models import TelemetryData


class MqttPublisher:
    def __init__(self, mqtt_client, event_log=None):
        """
        :param mqtt_client: An instance of our async MQTT client infrastructure.
        """
        self._mqtt_client = mqtt_client
        self._event_log = event_log

    async def publish_telemetry(self, topic: str, telemetry_data: TelemetryData):
        """
        Serializes and publishes telemetry data over MQTT.
        """
        payload_data = telemetry_data.model_dump()
        payload = json.dumps(payload_data)
        await self._mqtt_client.publish(topic, payload)
        self._record_publish(topic, payload_data)

    async def publish_node_status(self, edge_node_id: str, status: dict):
        payload = json.dumps(status)
        topic = f"fitrace/nodes/{edge_node_id}/status"
        await self._mqtt_client.publish(topic, payload)
        self._record_publish(topic, status)

    def _record_publish(self, topic: str, payload: dict):
        if not self._event_log:
            return
        self._event_log.record(
            "mqtt",
            "publish",
            topic=topic,
            payload=payload,
        )
