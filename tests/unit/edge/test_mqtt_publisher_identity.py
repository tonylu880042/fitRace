import asyncio
import json

import pytest

from edge_node.adapters.mqtt_publisher import MqttPublisher
from edge_node.domain.models import TelemetryData


class _FakeMqttClient:
    def __init__(self):
        self.published = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))
        self.started.set()
        await self.release.wait()


def _telemetry(node_id="bike-01"):
    return TelemetryData(
        node_id=node_id,
        equipment_id=node_id,
        equipment_type="fan_bike",
        distance_m=10.0,
        timestamp_epoch_ms=123456789,
    )


@pytest.mark.asyncio
async def test_publisher_adds_runtime_identity_and_monotonic_per_stream_sequence():
    mqtt = _FakeMqttClient()
    publisher = MqttPublisher(mqtt)

    first = _telemetry()
    second = _telemetry()
    mqtt.release.set()
    await publisher.publish_telemetry("gym/telemetry/bike-01", first)
    await publisher.publish_telemetry("gym/telemetry/bike-01", second)

    first_payload = mqtt.published[0][1]
    second_payload = mqtt.published[1][1]
    assert first_payload["producer_id"] == second_payload["producer_id"]
    assert first_payload["producer_sequence"] == 1
    assert second_payload["producer_sequence"] == 2


@pytest.mark.asyncio
async def test_republishing_same_sample_keeps_identity_even_concurrently():
    mqtt = _FakeMqttClient()
    publisher = MqttPublisher(mqtt)
    sample = _telemetry()

    first_task = asyncio.create_task(
        publisher.publish_telemetry("gym/telemetry/bike-01", sample)
    )
    await mqtt.started.wait()
    second_task = asyncio.create_task(
        publisher.publish_telemetry("gym/telemetry/bike-01", sample)
    )
    mqtt.release.set()
    await asyncio.gather(first_task, second_task)

    identities = {
        (payload["producer_id"], payload["producer_sequence"])
        for _, payload in mqtt.published
    }
    assert len(identities) == 1


@pytest.mark.asyncio
async def test_publisher_sequences_are_independent_per_stream():
    mqtt = _FakeMqttClient()
    publisher = MqttPublisher(mqtt)
    mqtt.release.set()

    await publisher.publish_telemetry("gym/telemetry/bike-01", _telemetry("bike-01"))
    await publisher.publish_telemetry("gym/telemetry/rower-01", _telemetry("rower-01"))

    assert [payload["producer_sequence"] for _, payload in mqtt.published] == [1, 1]


@pytest.mark.asyncio
async def test_new_publisher_runtime_gets_new_identity_when_sequence_restarts():
    mqtt = _FakeMqttClient()
    first_publisher = MqttPublisher(mqtt)
    second_publisher = MqttPublisher(mqtt)
    mqtt.release.set()

    await first_publisher.publish_telemetry(
        "gym/telemetry/bike-01", _telemetry("bike-01")
    )
    await second_publisher.publish_telemetry(
        "gym/telemetry/bike-01", _telemetry("bike-01")
    )

    first_payload = mqtt.published[0][1]
    second_payload = mqtt.published[1][1]
    assert first_payload["producer_id"] != second_payload["producer_id"]
    assert first_payload["producer_sequence"] == 1
    assert second_payload["producer_sequence"] == 1


@pytest.mark.asyncio
async def test_prepopulated_sequence_advances_this_publishers_counter():
    mqtt = _FakeMqttClient()
    publisher = MqttPublisher(mqtt)
    mqtt.release.set()
    retry = _telemetry()
    retry.producer_id = publisher._producer_id
    retry.producer_sequence = 5

    await publisher.publish_telemetry("gym/telemetry/bike-01", retry)
    await publisher.publish_telemetry("gym/telemetry/bike-01", _telemetry())

    assert mqtt.published[1][1]["producer_id"] == publisher._producer_id
    assert mqtt.published[1][1]["producer_sequence"] == 6
