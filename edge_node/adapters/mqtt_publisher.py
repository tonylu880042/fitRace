import json
import uuid

from edge_node.domain.models import TelemetryData


class MqttPublisher:
    def __init__(self, mqtt_client, event_log=None):
        """
        :param mqtt_client: An instance of our async MQTT client infrastructure.
        """
        self._mqtt_client = mqtt_client
        self._event_log = event_log
        self._producer_id = uuid.uuid4().hex
        self._next_sequence_by_stream: dict[str, int] = {}

    async def publish_telemetry(self, topic: str, telemetry_data: TelemetryData):
        """
        Serializes and publishes telemetry data over MQTT.
        """
        # Assign identity before the first await. This makes retrying the same
        # TelemetryData object, including two concurrent publish attempts,
        # preserve one sample identity instead of allocating two sequences.
        if telemetry_data.producer_id is None:
            telemetry_data.producer_id = self._producer_id
        if telemetry_data.producer_sequence is None:
            stream_id = telemetry_data.node_id
            next_sequence = self._next_sequence_by_stream.get(stream_id, 0) + 1
            self._next_sequence_by_stream[stream_id] = next_sequence
            telemetry_data.producer_sequence = next_sequence
        elif telemetry_data.producer_id == self._producer_id:
            # A caller may be retrying a sample that was already assigned by
            # this publisher. Keep newly generated samples ahead of that
            # pre-populated sequence so the runtime cannot issue a collision.
            stream_id = telemetry_data.node_id
            self._next_sequence_by_stream[stream_id] = max(
                self._next_sequence_by_stream.get(stream_id, 0),
                telemetry_data.producer_sequence,
            )

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
