"""MqttSubscriber must broadcast the "idle live telemetry" snapshot to
dashboards whenever a telemetry sample arrives while the race is not
running -- but throttled to ~2Hz total (one merged message for every
station), never one broadcast per sample. See RaceManager.
get_idle_telemetry_snapshot() for what "visible" means.

A RaceManager-like collaborator that doesn't implement
get_idle_telemetry_snapshot() (an older fake, or a future collaborator that
hasn't been updated) must not break telemetry handling -- the subscriber
degrades to "no idle broadcast" rather than raising.
"""

import pytest

from hub_server.adapters.mqtt_subscriber import MqttSubscriber
from hub_server.domain.models import RaceState


class FakeWebSocketManager:
    def __init__(self):
        self.broadcasts = []

    async def broadcast(self, payload):
        self.broadcasts.append(payload)


class IdleFakeRaceManager:
    """Minimal collaborator: never running, always has an idle snapshot."""

    def __init__(self, snapshot):
        self._snapshot = snapshot

    def ingest_telemetry(self, payload):
        return None  # not running -- mirrors RaceManager.ingest_telemetry

    def get_state(self):
        return RaceState.IDLE

    def get_idle_telemetry_snapshot(self):
        return self._snapshot


class NoIdleSupportFakeRaceManager:
    """Old-style collaborator with no idle telemetry support at all."""

    def ingest_telemetry(self, payload):
        return None

    def get_state(self):
        return RaceState.IDLE


def _visible_snapshot():
    return {
        "visible": True,
        "stations": [{"station_number": 1, "instantaneous_speed_kph": 9.0}],
        "best": [],
    }


@pytest.mark.asyncio
async def test_idle_telemetry_is_broadcast_when_snapshot_is_visible():
    race_manager = IdleFakeRaceManager(_visible_snapshot())
    ws_manager = FakeWebSocketManager()
    subscriber = MqttSubscriber(
        async_mqtt_client=None, race_manager=race_manager, ws_manager=ws_manager
    )

    await subscriber._handle_telemetry(
        {"node_id": "node-01", "equipment_type": "treadmill", "elapsed_time_ms": 0}
    )

    idle_messages = [
        m for m in ws_manager.broadcasts if m.get("type") == "idle_telemetry"
    ]
    assert len(idle_messages) == 1
    assert idle_messages[0]["stations"] == _visible_snapshot()["stations"]


@pytest.mark.asyncio
async def test_idle_telemetry_broadcast_is_throttled_to_one_per_burst():
    race_manager = IdleFakeRaceManager(_visible_snapshot())
    ws_manager = FakeWebSocketManager()
    subscriber = MqttSubscriber(
        async_mqtt_client=None, race_manager=race_manager, ws_manager=ws_manager
    )

    for _ in range(5):
        await subscriber._handle_telemetry(
            {"node_id": "node-01", "equipment_type": "treadmill", "elapsed_time_ms": 0}
        )

    idle_messages = [
        m for m in ws_manager.broadcasts if m.get("type") == "idle_telemetry"
    ]
    # Five samples fired back-to-back (well under the ~500ms gate) must
    # collapse into a single dashboard broadcast, not five.
    assert len(idle_messages) == 1


@pytest.mark.asyncio
async def test_no_idle_broadcast_when_snapshot_not_visible():
    race_manager = IdleFakeRaceManager({"visible": False, "stations": [], "best": []})
    ws_manager = FakeWebSocketManager()
    subscriber = MqttSubscriber(
        async_mqtt_client=None, race_manager=race_manager, ws_manager=ws_manager
    )

    await subscriber._handle_telemetry(
        {"node_id": "node-01", "equipment_type": "treadmill", "elapsed_time_ms": 0}
    )

    assert all(m.get("type") != "idle_telemetry" for m in ws_manager.broadcasts)


@pytest.mark.asyncio
async def test_missing_idle_snapshot_support_does_not_break_telemetry_handling():
    race_manager = NoIdleSupportFakeRaceManager()
    ws_manager = FakeWebSocketManager()
    subscriber = MqttSubscriber(
        async_mqtt_client=None, race_manager=race_manager, ws_manager=ws_manager
    )

    await subscriber._handle_telemetry(
        {"node_id": "node-01", "equipment_type": "treadmill", "elapsed_time_ms": 0}
    )

    assert all(m.get("type") != "idle_telemetry" for m in ws_manager.broadcasts)
