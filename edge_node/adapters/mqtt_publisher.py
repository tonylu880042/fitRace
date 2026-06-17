import json
from edge_node.domain.models import TelemetryData


class MqttPublisher:
    def __init__(self, mqtt_client):
        """
        :param mqtt_client: An instance of our async MQTT client infrastructure.
        """
        self._mqtt_client = mqtt_client

    async def publish_telemetry(self, topic: str, telemetry_data: TelemetryData):
        """
        Serializes and publishes telemetry data over MQTT.
        """
        payload = json.dumps(telemetry_data.model_dump())
        await self._mqtt_client.publish(topic, payload)

    async def publish_node_status(self, edge_node_id: str, status: dict):
        payload = json.dumps(status)
        await self._mqtt_client.publish(f"fitrace/nodes/{edge_node_id}/status", payload)
