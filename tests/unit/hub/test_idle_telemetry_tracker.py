"""Unit tests for the standalone idle-telemetry usecase store.

This store is deliberately independent of RaceManager's race progress --
see hub_server/usecases/idle_telemetry_tracker.py. These tests exercise the
tracker in isolation; end-to-end wiring through RaceManager is covered in
tests/unit/hub/test_race_manager_idle_telemetry.py.
"""

from hub_server.usecases.idle_telemetry_tracker import IdleTelemetryTracker


def test_record_sample_and_get_sample_round_trip():
    clock = {"now": 1_000}
    tracker = IdleTelemetryTracker(now_ms=lambda: clock["now"])

    tracker.record_sample(
        "node-1",
        {
            "instantaneous_speed_kph": 10.5,
            "power_watts": 180,
            "cadence_rpm": 160,
            "heart_rate_bpm": 130,
            "equipment_type": "treadmill",
        },
    )

    sample = tracker.get_sample("node-1")
    assert sample["instantaneous_speed_kph"] == 10.5
    assert sample["power_watts"] == 180
    assert sample["received_epoch_ms"] == 1_000


def test_get_sample_returns_none_for_unknown_node():
    tracker = IdleTelemetryTracker()
    assert tracker.get_sample("nope") is None


def test_best_tracks_the_max_value_and_who_holds_it():
    tracker = IdleTelemetryTracker()
    tracker.record_sample(
        "node-1", {"instantaneous_speed_kph": 10.0, "power_watts": 150}
    )
    tracker.record_sample(
        "node-2", {"instantaneous_speed_kph": 15.0, "power_watts": 120}
    )
    tracker.record_sample(
        "node-1", {"instantaneous_speed_kph": 8.0, "power_watts": 200}
    )

    best = tracker.get_best()
    assert best["instantaneous_speed_kph"]["value"] == 15.0
    assert best["instantaneous_speed_kph"]["node_id"] == "node-2"
    assert best["power_watts"]["value"] == 200
    assert best["power_watts"]["node_id"] == "node-1"


def test_zero_and_missing_values_never_become_a_best():
    tracker = IdleTelemetryTracker()
    tracker.record_sample("node-1", {"instantaneous_speed_kph": 0.0, "power_watts": 0})
    assert tracker.get_best() == {}


def test_reset_clears_samples_and_best():
    tracker = IdleTelemetryTracker()
    tracker.record_sample("node-1", {"instantaneous_speed_kph": 12.0})
    tracker.reset()
    assert tracker.get_sample("node-1") is None
    assert tracker.get_best() == {}
