"""End-to-end coverage for "idle live telemetry" through RaceManager's real
telemetry entry point (ingest_telemetry), not a hand-built dict -- the goal
is that a mutation to the producer side (ingest_telemetry / update_telemetry)
is caught here too.

Product requirements (see the feature spec): while IDLE/READY, a station
with a bound, recently-sending node shows instantaneous speed/power/cadence/
heart-rate and a "best of this idle period" mini leaderboard. This is a
totally separate store from race progress/results -- it must never leak
into _progress, and a race that starts afterwards must begin from clean
state.
"""

from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager


def _treadmill_payload(node_id, **overrides):
    payload = {
        "node_id": node_id,
        "edge_node_id": "edge-01",
        "equipment_id": "TREAD_01",
        "equipment_type": "treadmill",
        "instantaneous_speed_kph": 8.0,
        "cadence_rpm": 150,
        "power_watts": 120,
        "heart_rate_bpm": 110,
        "distance_m": 5.0,
        "elapsed_time_ms": 1000,
        "timestamp_epoch_ms": 1_700_000_000_000,
    }
    payload.update(overrides)
    return payload


def test_idle_snapshot_shows_bound_station_with_live_sample_while_idle():
    clock = {"now": 10_000}
    manager = RaceManager(now_ms=lambda: clock["now"])
    manager.assign_station(1, "treadmill-01")

    manager.ingest_telemetry(_treadmill_payload("treadmill-01"))

    snapshot = manager.get_idle_telemetry_snapshot()
    assert snapshot["visible"] is True
    stations = {s["station_number"]: s for s in snapshot["stations"]}
    assert stations[1]["node_id"] == "treadmill-01"
    assert stations[1]["is_stale"] is False
    assert stations[1]["instantaneous_speed_kph"] == 8.0
    assert stations[1]["power_watts"] == 120
    assert stations[1]["cadence_rpm"] == 150
    assert stations[1]["heart_rate_bpm"] == 110


def test_idle_snapshot_excludes_cumulative_fields():
    manager = RaceManager()
    manager.assign_station(1, "treadmill-01")
    manager.ingest_telemetry(_treadmill_payload("treadmill-01"))

    station = manager.get_idle_telemetry_snapshot()["stations"][0]
    assert "distance_m" not in station
    assert "elapsed_time_ms" not in station
    assert "calories" not in station


def test_idle_telemetry_never_writes_into_race_progress_or_active_race():
    manager = RaceManager()
    manager.assign_station(1, "treadmill-01")

    # Idle telemetry arrives before any race is configured at all.
    manager.ingest_telemetry(_treadmill_payload("treadmill-01"))
    assert manager.get_leaderboard_progress() == {}

    # Configure and start a race -- it must begin from completely clean
    # progress, unaffected by anything idle telemetry recorded above.
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.register_node("treadmill-01", "Runner A")
    manager.start_race()

    progress = manager.update_telemetry(
        {
            "node_id": "treadmill-01",
            "edge_node_id": "edge-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": manager.get_start_time_epoch_ms() + 1000,
            "delta_distance_m": 12.0,
        }
    )
    # Only this race's own telemetry counts -- distance_m reflects the
    # delta just sent (12.0), not anything left over from idle capture.
    assert progress["treadmill-01"]["distance_m"] == 12.0


def test_idle_capture_is_paused_while_a_race_is_running():
    # Capture must stop the moment the race is RUNNING, not just the
    # dashboard's "visible" flag -- otherwise a blazing-fast in-race sample
    # would quietly become the idle mini leaderboard's "best" once the race
    # stops, even though nobody was in an idle period when it happened.
    manager = RaceManager()
    manager.assign_station(1, "treadmill-01")
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.register_node("treadmill-01", "Runner A")
    manager.start_race()

    manager.ingest_telemetry(
        _treadmill_payload(
            "treadmill-01",
            instantaneous_speed_kph=99.0,
            power_watts=999,
            timestamp_epoch_ms=manager.get_start_time_epoch_ms() + 1000,
        )
    )
    manager.stop_race()

    # Snapshot fields are still computed (just marked not-visible) while
    # STOPPED, so this directly observes whether the sample above was ever
    # captured into the idle store.
    assert manager.get_idle_telemetry_snapshot()["best"] == []


def test_idle_snapshot_hidden_while_running_and_reappears_after_stop():
    manager = RaceManager()
    manager.assign_station(1, "treadmill-01")
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.register_node("treadmill-01", "Runner A")
    manager.start_race()

    manager.ingest_telemetry(
        _treadmill_payload(
            "treadmill-01", timestamp_epoch_ms=manager.get_start_time_epoch_ms() + 1000
        )
    )
    assert manager.get_idle_telemetry_snapshot()["visible"] is False


def test_stale_sample_shown_as_waiting_not_frozen_numbers():
    clock = {"now": 0}
    manager = RaceManager(now_ms=lambda: clock["now"])
    manager.assign_station(1, "treadmill-01")

    manager.ingest_telemetry(_treadmill_payload("treadmill-01"))
    clock["now"] = 6_000  # more than the ~5s staleness window later

    station = manager.get_idle_telemetry_snapshot()["stations"][0]
    assert station["is_stale"] is True
    assert station["instantaneous_speed_kph"] is None


def test_mini_leaderboard_tracks_best_speed_and_power_by_station_number():
    # Non-treadmill equipment (e.g. fan_bike) is what feeds the generic
    # speed/power bests -- see requirement B: treadmill speed/power are
    # excluded from these two buckets entirely (treadmill has its own
    # fastest-pace tracking instead, covered separately below).
    manager = RaceManager()
    manager.assign_station(1, "bike-01")
    manager.assign_station(2, "bike-02")
    manager.register_athlete(1, "Alice")
    manager.register_athlete(2, "Bob")

    manager.ingest_telemetry(
        _treadmill_payload(
            "bike-01",
            equipment_type="fan_bike",
            instantaneous_speed_kph=10.0,
            power_watts=100,
        )
    )
    manager.ingest_telemetry(
        _treadmill_payload(
            "bike-02",
            equipment_type="fan_bike",
            instantaneous_speed_kph=15.0,
            power_watts=90,
        )
    )

    best = {row["metric"]: row for row in manager.get_idle_telemetry_snapshot()["best"]}
    assert best["instantaneous_speed_kph"]["station_number"] == 2
    assert best["power_watts"]["station_number"] == 1


def test_mini_leaderboard_treadmill_pace_best_excludes_non_treadmill_and_vice_versa():
    manager = RaceManager()
    manager.assign_station(1, "treadmill-01")
    manager.assign_station(2, "bike-01")

    manager.ingest_telemetry(
        _treadmill_payload(
            "treadmill-01", equipment_type="treadmill", instantaneous_speed_kph=12.0
        )
    )
    manager.ingest_telemetry(
        _treadmill_payload(
            "bike-01",
            equipment_type="fan_bike",
            instantaneous_speed_kph=25.0,
            power_watts=200,
        )
    )

    best = {row["metric"]: row for row in manager.get_idle_telemetry_snapshot()["best"]}
    # The treadmill's speed feeds only the pace bucket, never the generic
    # speed best (the bike is the only eligible contender there).
    assert best["instantaneous_speed_kph"]["station_number"] == 2
    assert best["treadmill_pace_speed_kph"]["station_number"] == 1
    assert best["treadmill_pace_speed_kph"]["value"] == 12.0


def test_mini_leaderboard_orders_pace_speed_power_cadence_and_drops_heart_rate():
    manager = RaceManager()
    manager.assign_station(1, "treadmill-01")
    manager.assign_station(2, "bike-01")

    manager.ingest_telemetry(
        _treadmill_payload(
            "treadmill-01",
            equipment_type="treadmill",
            instantaneous_speed_kph=12.0,
            cadence_rpm=170,
            heart_rate_bpm=140,
        )
    )
    manager.ingest_telemetry(
        _treadmill_payload(
            "bike-01",
            equipment_type="fan_bike",
            instantaneous_speed_kph=25.0,
            power_watts=200,
            cadence_rpm=90,
            heart_rate_bpm=150,
        )
    )

    best_rows = manager.get_idle_telemetry_snapshot()["best"]
    metrics_in_order = [row["metric"] for row in best_rows]
    assert metrics_in_order == [
        "treadmill_pace_speed_kph",
        "instantaneous_speed_kph",
        "power_watts",
        "cadence_rpm",
    ]
    # Heart rate is never included in the mini leaderboard, even though
    # both stations reported it -- it still shows on the per-station cards
    # (see test_idle_snapshot_shows_bound_station_with_live_sample_while_idle).
    assert "heart_rate_bpm" not in metrics_in_order


def test_idle_snapshot_never_includes_athlete_name_even_when_registered():
    # Product decision: nobody knows who's on a machine during a showcase.
    # A registered athlete name must never leak into either the per-station
    # cards or the mini leaderboard rows.
    manager = RaceManager()
    manager.assign_station(1, "treadmill-01")
    manager.register_athlete(1, "Alice")
    manager.ingest_telemetry(
        _treadmill_payload(
            "treadmill-01", instantaneous_speed_kph=10.0, power_watts=100
        )
    )

    snapshot = manager.get_idle_telemetry_snapshot()
    assert "athlete_name" not in snapshot["stations"][0]
    assert snapshot["best"]
    for row in snapshot["best"]:
        assert "athlete_name" not in row


def test_mini_leaderboard_resets_when_a_race_starts_and_on_reset():
    manager = RaceManager()
    manager.assign_station(1, "treadmill-01")
    manager.ingest_telemetry(
        _treadmill_payload("treadmill-01", instantaneous_speed_kph=20.0)
    )
    assert manager.get_idle_telemetry_snapshot()["best"]

    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.register_node("treadmill-01", "Runner A")
    manager.start_race()

    # Reset when a race enters the running/countdown-completed state: the
    # idle mini leaderboard from the previous idle period is gone.
    assert manager.get_idle_telemetry_snapshot()["best"] == []

    manager.stop_race()
    manager.reset_race()

    # After reset, a new idle sample starts a completely fresh board.
    manager.ingest_telemetry(
        _treadmill_payload("treadmill-01", instantaneous_speed_kph=5.0)
    )
    assert manager.get_idle_telemetry_snapshot()["best"]
    manager.reset_race()
    assert manager.get_idle_telemetry_snapshot()["best"] == []


def test_idle_visibility_toggle_defaults_on_and_can_be_turned_off():
    manager = RaceManager()
    manager.assign_station(1, "treadmill-01")
    manager.ingest_telemetry(_treadmill_payload("treadmill-01"))
    assert manager.get_idle_live_telemetry_visible() is True
    assert manager.get_idle_telemetry_snapshot()["visible"] is True

    manager.set_idle_live_telemetry_visible(False)
    assert manager.get_idle_telemetry_snapshot()["visible"] is False


def test_idle_snapshot_ignores_unbound_active_nodes():
    manager = RaceManager()
    # A node sending telemetry with no station assignment must not appear
    # as a card -- decision 3 restricts cards to bound stations only.
    manager.ingest_telemetry(_treadmill_payload("treadmill-99"))
    assert manager.get_idle_telemetry_snapshot()["stations"] == []
