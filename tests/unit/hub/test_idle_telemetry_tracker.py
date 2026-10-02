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


# ---------------------------------------------------------------------------
# Equipment-aware best tracking (requirement B): a treadmill's speed/power
# are not comparable to other equipment types -- treadmill "speed" instead
# feeds the separate fastest-pace tracking, and treadmill power isn't shown
# in the idle view at all (see hub_server/static/index.html's
# renderIdleStationCard).
# ---------------------------------------------------------------------------


def test_treadmill_samples_never_win_the_generic_speed_or_power_best():
    tracker = IdleTelemetryTracker()
    tracker.record_sample(
        "tread-1",
        {
            "instantaneous_speed_kph": 20.0,
            "power_watts": 500,
            "equipment_type": "treadmill",
        },
    )
    tracker.record_sample(
        "bike-1",
        {
            "instantaneous_speed_kph": 5.0,
            "power_watts": 50,
            "equipment_type": "fan_bike",
        },
    )

    best = tracker.get_best()
    # The bike's much lower numbers still win because the treadmill's much
    # higher numbers are never eligible for these two metrics at all.
    assert best["instantaneous_speed_kph"]["node_id"] == "bike-1"
    assert best["power_watts"]["node_id"] == "bike-1"


def test_curved_treadmill_is_treated_the_same_as_treadmill():
    tracker = IdleTelemetryTracker()
    tracker.record_sample(
        "curved-1",
        {
            "instantaneous_speed_kph": 20.0,
            "power_watts": 500,
            "equipment_type": "curved_treadmill",
        },
    )
    assert tracker.get_best() == {
        "treadmill_pace_speed_kph": {"value": 20.0, "node_id": "curved-1"}
    }


def test_treadmill_pace_speed_tracks_the_fastest_treadmill_only():
    tracker = IdleTelemetryTracker()
    tracker.record_sample(
        "tread-1", {"instantaneous_speed_kph": 10.0, "equipment_type": "treadmill"}
    )
    tracker.record_sample(
        "tread-2", {"instantaneous_speed_kph": 14.0, "equipment_type": "treadmill"}
    )
    # A non-treadmill machine going even faster must not affect the
    # treadmill-only fastest-pace tracking.
    tracker.record_sample(
        "bike-1", {"instantaneous_speed_kph": 30.0, "equipment_type": "fan_bike"}
    )

    best = tracker.get_best()
    assert best["treadmill_pace_speed_kph"]["value"] == 14.0
    assert best["treadmill_pace_speed_kph"]["node_id"] == "tread-2"


def test_treadmill_pace_speed_ignores_zero_speed():
    tracker = IdleTelemetryTracker()
    tracker.record_sample(
        "tread-1", {"instantaneous_speed_kph": 0.0, "equipment_type": "treadmill"}
    )
    assert "treadmill_pace_speed_kph" not in tracker.get_best()


def test_cadence_best_is_not_equipment_restricted():
    tracker = IdleTelemetryTracker()
    tracker.record_sample(
        "tread-1",
        {"cadence_rpm": 180, "heart_rate_bpm": 160, "equipment_type": "treadmill"},
    )
    best = tracker.get_best()
    assert best["cadence_rpm"]["node_id"] == "tread-1"


def test_heart_rate_never_appears_in_best_at_all():
    # Product decision: the mini "best of session" leaderboard drops heart
    # rate entirely (keeps the row fitting on one line) -- it still shows on
    # every per-station card as usual, but is never tracked as a "best".
    tracker = IdleTelemetryTracker()
    tracker.record_sample("n1", {"heart_rate_bpm": 190, "equipment_type": "fan_bike"})
    assert "heart_rate_bpm" not in tracker.get_best()


def test_last_moving_epoch_ms_tracks_the_latest_sample_with_speed_above_zero():
    clock = {"now": 1_000}
    tracker = IdleTelemetryTracker(now_ms=lambda: clock["now"])

    tracker.record_sample("node-1", {"instantaneous_speed_kph": 8.0})
    assert tracker.get_sample("node-1")["last_moving_epoch_ms"] == 1_000

    clock["now"] = 2_000
    tracker.record_sample("node-1", {"instantaneous_speed_kph": 0.0})
    assert tracker.get_sample("node-1")["last_moving_epoch_ms"] == 1_000

    clock["now"] = 3_000
    tracker.record_sample("node-1", {"instantaneous_speed_kph": 5.0})
    assert tracker.get_sample("node-1")["last_moving_epoch_ms"] == 3_000


def test_last_moving_epoch_ms_is_none_for_a_node_that_never_moved():
    tracker = IdleTelemetryTracker(now_ms=lambda: 1_000)

    tracker.record_sample("node-1", {"instantaneous_speed_kph": 0.0})
    tracker.record_sample("node-1", {})

    assert tracker.get_sample("node-1")["last_moving_epoch_ms"] is None


def test_last_moving_epoch_ms_is_per_node_and_cleared_by_reset():
    tracker = IdleTelemetryTracker(now_ms=lambda: 1_000)
    tracker.record_sample("node-1", {"instantaneous_speed_kph": 8.0})
    tracker.record_sample("node-2", {"instantaneous_speed_kph": 0.0})

    assert tracker.get_sample("node-2")["last_moving_epoch_ms"] is None

    tracker.reset()
    tracker.record_sample("node-1", {"instantaneous_speed_kph": 0.0})
    assert tracker.get_sample("node-1")["last_moving_epoch_ms"] is None
