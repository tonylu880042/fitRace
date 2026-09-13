"""A relay race runs on ONE machine per team: members take turns, one leg
each. RaceManager.update_telemetry (hub_server/usecases/race_manager.py)
tracks legs for a "relay" competition_mode race by splitting the target
distance equally (leg_distance = target_value / relay_legs) and watching
the team's cumulative distance_m cross each leg boundary.

Payloads here are antenna-shaped: elapsed_time_ms=0, timestamp_epoch_ms
(absolute), delta_distance_m -- exactly what the production BLE/FTMS ->
edge -> hub path sends, and the same shape test_finish_time_interpolation.py
uses for RaceManager._interpolated_finish_time_ms, which relay split
computation reuses directly (same function, called once per newly crossed
leg boundary).

1000 m / 4 legs -> leg_distance = 250 m. Boundaries at 250/500/750/1000.
"""

from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager


def _configure_relay(rm, legs=4, target=1000.0):
    rm.assign_station(1, "treadmill-01")
    rm.register_athlete(
        1,
        None,
        team_name="Volt",
        relay_members=["Alice", "Bob", "Cara", "Dee"][:legs],
    )
    rm.configure(
        RaceConfig(
            race_type="distance",
            competition_mode="relay",
            relay_legs=legs,
            target_value=target,
        )
    )
    rm.start_race()
    return rm.get_start_time_epoch_ms()


def test_relay_progress_row_uses_team_name_as_athlete_name_and_carries_roster():
    rm = RaceManager()
    start_ms = _configure_relay(rm)
    progress = rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 24000,
            "delta_distance_m": 240.0,
        }
    )
    row = progress["treadmill-01"]
    assert row["athlete_name"] == "Volt"
    assert row["relay_legs"] == 4
    assert row["relay_members"] == ["Alice", "Bob", "Cara", "Dee"]
    assert row["relay_leg"] == 1
    assert row["relay_current_runner"] == "Alice"
    assert row["relay_splits"] == []


def test_relay_leg_and_runner_advance_with_interpolated_split():
    rm = RaceManager()
    start_ms = _configure_relay(rm)
    rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 24000,
            "delta_distance_m": 240.0,
        }
    )
    progress = rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 26000,
            "delta_distance_m": 20.0,  # 240 -> 260, crosses the 250 m leg 1 boundary
        }
    )
    row = progress["treadmill-01"]
    # 24000 + (250-240)/(260-240) * (26000-24000) = 25000
    assert row["relay_splits"] == [25000]
    assert row["relay_leg"] == 2
    assert row["relay_current_runner"] == "Bob"


def test_relay_single_sample_crossing_several_boundaries_appends_each_split():
    # A single telemetry sample jumps straight from 0 m to 1260 m (past the
    # 1000 m target) in one 126000 ms tick -- crossing all four leg
    # boundaries (250/500/750/1000) at once. 100 ms/m throughout makes each
    # interpolated split a round number: split_ms = threshold_m * 100.
    rm = RaceManager()
    start_ms = _configure_relay(rm)
    progress = rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 126000,
            "delta_distance_m": 1260.0,
        }
    )
    row = progress["treadmill-01"]
    assert row["relay_splits"] == [25000, 50000, 75000, 100000]
    assert row["relay_leg"] == 4
    assert row["relay_current_runner"] == "Dee"
    # The team's target distance was hit in this same sample -- finished_time_ms
    # must equal the last (target-boundary) split exactly.
    assert row["finished_time_ms"] == 100000
    assert row["relay_splits"][-1] == row["finished_time_ms"]


def test_relay_first_leg_split_recorded_before_multi_boundary_jump():
    rm = RaceManager()
    start_ms = _configure_relay(rm)
    rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 24000,
            "delta_distance_m": 240.0,
        }
    )
    progress = rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 26000,
            "delta_distance_m": 20.0,
        }
    )
    row = progress["treadmill-01"]
    assert row["relay_splits"] == [25000]

    progress = rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 100000,
            "delta_distance_m": 1000.0,  # 260 -> 1260: crosses 500, 750, 1000
        }
    )
    row = progress["treadmill-01"]
    # The leg-1 split from the previous tick is untouched (never
    # recomputed), and legs 2, 3, 4 are appended after it.
    assert row["relay_splits"] == [25000, 43760, 62260, 80760]


def test_relay_finish_auto_stops_the_race():
    rm = RaceManager()
    start_ms = _configure_relay(rm)
    rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 5000,
            "delta_distance_m": 1000.0,
        }
    )
    from hub_server.domain.models import RaceState

    assert rm.get_state() == RaceState.STOPPED


def test_relay_splits_never_change_once_recorded():
    # legs=4, target=2000 -> leg_distance=500, so the team stays well short
    # of finishing while we keep ticking within leg 2 -- proving the
    # already-recorded leg-1 split is never recomputed, not merely that it
    # happens to be frozen by an auto-stop.
    rm = RaceManager()
    start_ms = _configure_relay(rm, legs=4, target=2000.0)
    rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 48000,
            "delta_distance_m": 480.0,
        }
    )
    progress = rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 52000,
            "delta_distance_m": 40.0,  # 480 -> 520, crosses the 500 m leg 1 boundary
        }
    )
    first_split = progress["treadmill-01"]["relay_splits"]
    assert first_split == [50000]  # 48000 + (500-480)/(520-480) * (52000-48000)

    for tick, distance_delta in enumerate((30.0, 40.0, 50.0), start=1):
        progress = rm.update_telemetry(
            {
                "node_id": "treadmill-01",
                "elapsed_time_ms": 0,
                "timestamp_epoch_ms": start_ms + 52000 + tick * 4000,
                "delta_distance_m": distance_delta,  # still short of 1000 m (leg 2)
            }
        )
        assert progress["treadmill-01"]["relay_splits"] == first_split


def test_relay_placeholder_row_before_telemetry_has_leg_one_and_first_runner():
    rm = RaceManager()
    rm.assign_station(1, "treadmill-01")
    rm.register_athlete(1, None, team_name="Volt", relay_members=["Alice", "Bob"])
    rm.configure(
        RaceConfig(
            race_type="distance",
            competition_mode="relay",
            relay_legs=2,
            target_value=1000,
        )
    )
    pre_start_progress = rm.get_leaderboard_progress()
    row = next(iter(pre_start_progress.values()))
    assert row["athlete_name"] == "Volt"
    assert row["relay_leg"] == 1
    assert row["relay_current_runner"] == "Alice"
    assert row["relay_splits"] == []

    rm.start_race()
    started_row = next(iter(rm.get_leaderboard_progress().values()))
    assert started_row["relay_leg"] == 1
    assert started_row["relay_current_runner"] == "Alice"
    assert started_row["relay_splits"] == []


def test_non_relay_race_row_has_no_relay_keys():
    rm = RaceManager()
    rm.configure(RaceConfig(race_type="distance", target_value=500.0))
    rm.register_node("treadmill-01", "Runner A")
    rm.start_race()
    start_ms = rm.get_start_time_epoch_ms()
    progress = rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 5000,
            "delta_distance_m": 100.0,
        }
    )
    row = progress["treadmill-01"]
    for key in (
        "relay_legs",
        "relay_members",
        "relay_leg",
        "relay_current_runner",
        "relay_splits",
    ):
        assert key not in row


def test_enrich_functions_preserve_relay_team_name():
    from hub_server.usecases.node_display_names import (
        enrich_progress_display_names,
        enrich_race_state_display_names,
    )

    rm = RaceManager()
    start_ms = _configure_relay(rm)
    progress = rm.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 5000,
            "delta_distance_m": 100.0,
        }
    )
    enriched = enrich_progress_display_names(progress, nodes=[])
    assert enriched["treadmill-01"]["athlete_name"] == "Volt"

    state_snapshot = {"leaderboard": progress, "team_leaderboard": []}
    enriched_state = enrich_race_state_display_names(state_snapshot, nodes=[])
    assert enriched_state["leaderboard"]["treadmill-01"]["athlete_name"] == "Volt"
