import pytest

from hub_server.adapters.mqtt_subscriber import MqttSubscriber
from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager


def _running_manager() -> RaceManager:
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=1000.0))
    manager.start_race()
    return manager


def _sample(*, sequence=None, timestamp=None, delta=10.0, node_id="bike-01"):
    payload = {
        "node_id": node_id,
        "equipment_type": "fan_bike",
        "delta_distance_m": delta,
        "elapsed_time_ms": 1000,
    }
    if sequence is not None:
        payload.update(
            {
                "producer_id": "edge-runtime-1",
                "producer_sequence": sequence,
            }
        )
    if timestamp is not None:
        payload["timestamp_epoch_ms"] = timestamp
    return payload


def test_same_producer_sample_is_scored_once():
    manager = _running_manager()
    sample = _sample(sequence=1, timestamp=manager.get_start_time_epoch_ms() + 1000)

    first = manager.ingest_telemetry(sample)
    duplicate = manager.ingest_telemetry(dict(sample))

    assert first["bike-01"]["distance_m"] == 10.0
    assert duplicate is None
    assert manager.get_leaderboard_progress()["bike-01"]["distance_m"] == 10.0


def test_out_of_order_unique_sequences_still_score_and_duplicate_sequence_does_not():
    manager = _running_manager()
    start = manager.get_start_time_epoch_ms()

    manager.ingest_telemetry(_sample(sequence=3, timestamp=start + 3000, delta=30))
    manager.ingest_telemetry(_sample(sequence=2, timestamp=start + 2000, delta=20))
    manager.ingest_telemetry(_sample(sequence=3, timestamp=start + 3000, delta=30))

    assert manager.get_leaderboard_progress()["bike-01"]["distance_m"] == 50.0


def test_producer_sequence_is_scoped_to_each_telemetry_stream():
    manager = _running_manager()
    start = manager.get_start_time_epoch_ms()

    manager.ingest_telemetry(
        _sample(sequence=1, timestamp=start + 1000, delta=20, node_id="bike-01")
    )
    manager.ingest_telemetry(
        _sample(sequence=1, timestamp=start + 1000, delta=20, node_id="rower-01")
    )

    progress = manager.get_leaderboard_progress()
    assert progress["bike-01"]["distance_m"] == 20.0
    assert progress["rower-01"]["distance_m"] == 20.0


def test_legacy_same_timestamp_is_scored_once_but_out_of_order_timestamp_is_kept():
    manager = _running_manager()
    start = manager.get_start_time_epoch_ms()

    manager.ingest_telemetry(_sample(timestamp=start + 3000, delta=30))
    manager.ingest_telemetry(_sample(timestamp=start + 2000, delta=20))
    manager.ingest_telemetry(_sample(timestamp=start + 3000, delta=30))

    assert manager.get_leaderboard_progress()["bike-01"]["distance_m"] == 50.0


def test_old_timestamp_from_previous_race_cannot_score_in_new_race(monkeypatch):
    import hub_server.usecases.race_manager as race_manager_module

    clock = [1_700_000_000.0]
    monkeypatch.setattr(race_manager_module.time, "time", lambda: clock[0])
    manager = _running_manager()
    old_start = manager.get_start_time_epoch_ms()
    old_sample = _sample(sequence=1, timestamp=old_start + 1, delta=50)
    manager.ingest_telemetry(old_sample)
    manager.stop_race()

    manager.configure(RaceConfig(race_type="distance", target_value=1000.0))
    clock[0] += 2
    manager.start_race()
    assert manager.get_start_time_epoch_ms() > old_start

    assert manager.ingest_telemetry(old_sample) is None
    assert "bike-01" not in manager.get_leaderboard_progress()


def test_seen_sample_history_is_bounded():
    manager = _running_manager()
    start = manager.get_start_time_epoch_ms()

    for sequence in range(manager.TELEMETRY_DEDUP_MAX_ENTRIES + 50):
        manager.ingest_telemetry(
            _sample(sequence=sequence + 1, timestamp=start + sequence + 1)
        )

    assert len(manager._telemetry_seen) == manager.TELEMETRY_DEDUP_MAX_ENTRIES


class _WebSocketRecorder:
    def __init__(self):
        self.broadcasts = []

    async def broadcast(self, payload):
        self.broadcasts.append(payload)


@pytest.mark.asyncio
async def test_mqtt_subscriber_preserves_identity_and_scores_distance_and_calories_once():
    manager = _running_manager()
    websocket = _WebSocketRecorder()
    subscriber = MqttSubscriber(None, manager, websocket)
    sample = _sample(
        sequence=1,
        timestamp=manager.get_start_time_epoch_ms() + 1000,
        delta=10.0,
    )
    sample["delta_energy_kcal"] = 2.0

    await subscriber._handle_telemetry(sample)
    await subscriber._handle_telemetry(dict(sample))

    progress = manager.get_leaderboard_progress()["bike-01"]
    assert progress["distance_m"] == 10.0
    assert progress["calories"] == 2.0
    assert ("producer", "bike-01", "edge-runtime-1", 1) in manager._telemetry_seen
