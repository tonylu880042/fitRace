import logging

import pytest

from hub_server.adapters.mqtt_subscriber import MqttSubscriber
from hub_server.domain.models import RaceState
from hub_server.usecases.node_registry import NodeRegistry
from hub_server.usecases.race_manager import RaceManager
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
    def __init__(self, nodes=None):
        self.telemetry_payloads = []
        self.nodes = nodes or []

    def update_telemetry(self, payload):
        self.telemetry_payloads.append(payload)

    def list_nodes(self):
        return self.nodes


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
async def test_mqtt_subscriber_broadcasts_operator_node_labels():
    node_id = "fitrace-edge-01-01"
    race_manager = FakeRaceManager(
        progress={node_id: {"node_id": node_id, "progress_percent": 25}}
    )
    ws_manager = FakeWebSocketManager()
    node_registry = FakeNodeRegistry(
        nodes=[
            {
                "edge_node_id": "fitrace-edge-01",
                "display_name": "Node130",
                "equipment_streams": [
                    {
                        "node_id": node_id,
                        "equipment_id": "Vmax_1183",
                        "display_name": "Node130+Vmax_1183",
                    }
                ],
            }
        ]
    )
    subscriber = MqttSubscriber(
        async_mqtt_client=None,
        race_manager=race_manager,
        ws_manager=ws_manager,
        node_registry=node_registry,
    )

    await subscriber._handle_telemetry(
        {
            "node_id": node_id,
            "edge_node_id": "fitrace-edge-01",
            "equipment_id": "Vmax_1183",
        }
    )

    assert ws_manager.broadcasts[0][node_id]["node_display_name"] == (
        "Node130+Vmax_1183"
    )
    assert ws_manager.broadcasts[0][node_id]["node_id"] == node_id


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


# -- bindings_removed: Edge -> Hub equipment-removal propagation ----------
#
# The trigger is a one-shot MQTT event an Edge publishes only on an
# explicit operator action (see edge_node's config-save hook). It must
# never be confused with the periodic heartbeat/status topic, which fires
# on its own regardless of operator intent and drops out constantly under
# real Wi-Fi conditions -- treating a missing/empty heartbeat as "equipment
# removed" would wipe venue setup on every drop. See
# test_node_status_heartbeat_never_triggers_station_unassignment below.
from hub_server.domain.models import RaceConfig  # noqa: E402


@pytest.mark.asyncio
async def test_mqtt_subscriber_bindings_removed_unassigns_exactly_affected_stations():
    race_manager = RaceManager()
    race_manager.update_active_node("fitrace-edge-01-01", "fan_bike")
    race_manager.assign_station(1, "fitrace-edge-01-01")
    race_manager.register_athlete(1, "Athlete One")
    race_manager.update_active_node("fitrace-edge-01-02", "treadmill")
    race_manager.assign_station(2, "fitrace-edge-01-02")
    race_manager.update_active_node("fitrace-edge-02-01", "rowing_machine")
    race_manager.assign_station(3, "fitrace-edge-02-01")

    ws_manager = FakeWebSocketManager()
    subscriber = MqttSubscriber(
        async_mqtt_client=None,
        race_manager=race_manager,
        ws_manager=ws_manager,
    )

    await subscriber._handle_bindings_removed(
        {
            "edge_node_id": "fitrace-edge-01",
            "removed_node_ids": ["fitrace-edge-01-01", "fitrace-edge-01-02"],
        },
        "fitrace-edge-01",
    )

    status = race_manager.get_stations_status()
    assert 1 not in status["stations"]
    assert 2 not in status["stations"]
    # Station 3, bound to a different edge's node_id, is untouched.
    assert status["stations"][3]["node_id"] == "fitrace-edge-02-01"

    # The user's explicit call: athlete data goes with the equipment,
    # because assign_station(n, None) is reused rather than a partial-unbind
    # path. Re-fetching after the removal must show no leftover athlete.
    status_again = race_manager.get_stations_status()
    assert 1 not in status_again["stations"]


@pytest.mark.asyncio
async def test_mqtt_subscriber_bindings_removed_broadcasts_updated_stations():
    race_manager = RaceManager()
    race_manager.update_active_node("fitrace-edge-01-01", "fan_bike")
    race_manager.assign_station(1, "fitrace-edge-01-01")

    ws_manager = FakeWebSocketManager()
    subscriber = MqttSubscriber(
        async_mqtt_client=None,
        race_manager=race_manager,
        ws_manager=ws_manager,
    )

    await subscriber._handle_bindings_removed(
        {
            "edge_node_id": "fitrace-edge-01",
            "removed_node_ids": ["fitrace-edge-01-01"],
        },
        "fitrace-edge-01",
    )

    assert len(ws_manager.broadcasts) == 1
    assert ws_manager.broadcasts[0]["type"] == "state_change"


@pytest.mark.asyncio
async def test_mqtt_subscriber_bindings_removed_during_running_unassigns_nothing(
    caplog,
):
    race_manager = RaceManager()
    race_manager.update_active_node("fitrace-edge-01-01", "fan_bike")
    race_manager.assign_station(1, "fitrace-edge-01-01")
    race_manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    race_manager.start_race()
    assert race_manager.get_state() == RaceState.RUNNING

    ws_manager = FakeWebSocketManager()
    subscriber = MqttSubscriber(
        async_mqtt_client=None,
        race_manager=race_manager,
        ws_manager=ws_manager,
    )

    with caplog.at_level(logging.WARNING, logger="hub_server.mqtt_subscriber"):
        await subscriber._handle_bindings_removed(
            {
                "edge_node_id": "fitrace-edge-01",
                "removed_node_ids": ["fitrace-edge-01-01"],
            },
            "fitrace-edge-01",
        )

    status = race_manager.get_stations_status()
    assert status["stations"][1]["node_id"] == "fitrace-edge-01-01"
    assert ws_manager.broadcasts == []
    assert any(
        "RUNNING" in record.message and "fitrace-edge-01-01" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_mqtt_subscriber_bindings_removed_unknown_node_id_is_a_noop():
    race_manager = RaceManager()
    race_manager.update_active_node("fitrace-edge-01-01", "fan_bike")
    race_manager.assign_station(1, "fitrace-edge-01-01")

    ws_manager = FakeWebSocketManager()
    subscriber = MqttSubscriber(
        async_mqtt_client=None,
        race_manager=race_manager,
        ws_manager=ws_manager,
    )

    await subscriber._handle_bindings_removed(
        {
            "edge_node_id": "fitrace-edge-02",
            "removed_node_ids": ["fitrace-edge-02-99"],
        },
        "fitrace-edge-02",
    )

    status = race_manager.get_stations_status()
    assert status["stations"][1]["node_id"] == "fitrace-edge-01-01"
    assert ws_manager.broadcasts == []


@pytest.mark.asyncio
async def test_mqtt_subscriber_bindings_removed_rejects_invalid_payload():
    race_manager = RaceManager()
    race_manager.update_active_node("fitrace-edge-01-01", "fan_bike")
    race_manager.assign_station(1, "fitrace-edge-01-01")

    ws_manager = FakeWebSocketManager()
    subscriber = MqttSubscriber(
        async_mqtt_client=None,
        race_manager=race_manager,
        ws_manager=ws_manager,
    )

    # Missing required edge_node_id field.
    await subscriber._handle_bindings_removed(
        {"removed_node_ids": ["fitrace-edge-01-01"]},
        "fitrace-edge-01",
    )

    status = race_manager.get_stations_status()
    assert status["stations"][1]["node_id"] == "fitrace-edge-01-01"
    assert ws_manager.broadcasts == []


@pytest.mark.asyncio
async def test_node_status_heartbeat_never_triggers_station_unassignment():
    """Pinning the user's core safety requirement: an empty or absent
    heartbeat/status message must never unassign a station, no matter how
    it looks. A heartbeat reporting equipment_streams=[] (exactly what a
    dropped-Wi-Fi edge looks like) must leave every station bound exactly
    as it was -- only the explicit bindings_removed event may unassign.
    """
    race_manager = RaceManager()
    race_manager.update_active_node("fitrace-edge-01-01", "fan_bike")
    race_manager.assign_station(1, "fitrace-edge-01-01")
    race_manager.register_athlete(1, "Athlete One")

    ws_manager = FakeWebSocketManager()
    node_registry = NodeRegistry()
    subscriber = MqttSubscriber(
        async_mqtt_client=None,
        race_manager=race_manager,
        ws_manager=ws_manager,
        node_registry=node_registry,
    )

    # A heartbeat with an empty equipment_streams list -- what the bug
    # report actually observed on a dropped edge -- goes through the
    # ordinary node_status handler.
    await subscriber._handle_node_status(
        {"equipment_streams": [], "status": "online"},
        "fitrace-edge-01",
    )
    # And an absent/None node status payload for good measure.
    await subscriber._handle_node_status({}, "fitrace-edge-01")

    status = race_manager.get_stations_status()
    assert status["stations"][1]["node_id"] == "fitrace-edge-01-01"
    assert status["stations"][1]["athlete_name"] == "Athlete One"
