import pytest

from hub_server.adapters.mqtt_subscriber import MqttSubscriber
from hub_server.domain.models import RaceState
from hub_server.usecases.race_result_store import RaceResultStore


class FakeRaceManager:
    def __init__(self, progress=None):
        self.payloads = []
        self._progress = progress

    def ingest_telemetry(self, payload):
        self.payloads.append(payload)
        return self._progress

    def get_state(self):
        return RaceState.RUNNING

    def get_state_snapshot(self):
        return {
            "state": self.get_state().value,
            "config": {"race_type": "distance"},
            "start_time_epoch_ms": 1000,
            "end_time_epoch_ms": 2000,
            "leaderboard": {},
            "team_leaderboard": [],
        }


class StoppedRaceManager(FakeRaceManager):
    def get_state(self):
        return RaceState.STOPPED


class FakeWebSocketManager:
    def __init__(self):
        self.broadcasts = []

    async def broadcast(self, payload):
        self.broadcasts.append(payload)


class FakeNodeRegistry:
    def __init__(self):
        self.telemetry_payloads = []

    def update_telemetry(self, payload):
        self.telemetry_payloads.append(payload)


@pytest.mark.asyncio
async def test_mqtt_subscriber_rejects_invalid_telemetry_payload():
    race_manager = FakeRaceManager()
    ws_manager = FakeWebSocketManager()
    subscriber = MqttSubscriber(
        async_mqtt_client=None,
        race_manager=race_manager,
        ws_manager=ws_manager,
    )

    await subscriber._handle_telemetry(
        {
            "node_id": "node-01",
            "distance_m": -1.0,
            "elapsed_time_ms": 1000,
        }
    )

    assert race_manager.payloads == []
    assert ws_manager.broadcasts == []


@pytest.mark.asyncio
async def test_mqtt_subscriber_normalizes_valid_telemetry_payload():
    race_manager = FakeRaceManager(progress={})
    ws_manager = FakeWebSocketManager()
    node_registry = FakeNodeRegistry()
    subscriber = MqttSubscriber(
        async_mqtt_client=None,
        race_manager=race_manager,
        ws_manager=ws_manager,
        node_registry=node_registry,
    )

    await subscriber._handle_telemetry(
        {
            "node_id": "node-01",
            "edge_node_id": "edge-01",
            "equipment_type": "fan_bike",
            "distance_m": 12.5,
            "elapsed_time_ms": 1000,
            "power_watts": 150,
        }
    )

    assert race_manager.payloads == [
        {
            "node_id": "node-01",
            "edge_node_id": "edge-01",
            "equipment_type": "fan_bike",
            "instantaneous_speed_kph": 0.0,
            "cadence_rpm": 0,
            "power_watts": 150,
            "heart_rate_bpm": 0,
            "distance_m": 12.5,
            "elapsed_time_ms": 1000,
        }
    ]
    assert node_registry.telemetry_payloads == race_manager.payloads
    assert ws_manager.broadcasts == [{}]


@pytest.mark.asyncio
async def test_mqtt_subscriber_preserves_type_specific_ftms_fields():
    race_manager = FakeRaceManager(progress={})
    ws_manager = FakeWebSocketManager()
    subscriber = MqttSubscriber(
        async_mqtt_client=None,
        race_manager=race_manager,
        ws_manager=ws_manager,
    )

    await subscriber._handle_telemetry(
        {
            "node_id": "rower-01",
            "equipment_type": "rowing_machine",
            "mac_address": "AA:BB:CC:DD:EE:03",
            "ftms_type": "ROWER",
            "rssi": -60,
            "cadence_rpm": 24,
            "pace_sec_per_500m": 125,
            "power_watts": 98,
            "distance_m": 850,
            "total_energy_kcal": 22,
            "calories": 22,
            "ftms_payload": {
                "kind": "rower",
                "stroke_rate": 24.5,
                "instantaneous_pace": 125,
            },
            "raw_payload": {
                "stroke_rate": 24.5,
                "instantaneous_pace": 125,
            },
        }
    )

    payload = race_manager.payloads[0]
    assert payload["mac_address"] == "AA:BB:CC:DD:EE:03"
    assert payload["ftms_type"] == "ROWER"
    assert payload["rssi"] == -60
    assert payload["pace_sec_per_500m"] == 125
    assert payload["total_energy_kcal"] == 22
    assert payload["ftms_payload"]["kind"] == "rower"
    assert payload["raw_payload"]["stroke_rate"] == 24.5


@pytest.mark.asyncio
async def test_mqtt_subscriber_persists_result_when_telemetry_auto_stops_race(tmp_path):
    race_manager = StoppedRaceManager(progress={"node-01": {"progress_percent": 100}})
    ws_manager = FakeWebSocketManager()
    result_store = RaceResultStore(tmp_path / "race_results.jsonl")
    subscriber = MqttSubscriber(
        async_mqtt_client=None,
        race_manager=race_manager,
        ws_manager=ws_manager,
        race_result_store=result_store,
    )

    await subscriber._handle_telemetry(
        {
            "node_id": "node-01",
            "distance_m": 100,
            "elapsed_time_ms": 1000,
        }
    )

    results = result_store.list_results()
    assert len(results) == 1
    assert results[0]["snapshot"]["state"] == "STOPPED"
