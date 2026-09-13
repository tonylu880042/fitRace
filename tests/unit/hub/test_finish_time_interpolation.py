"""The antenna reports distance/calories at a fixed poll interval (0.25s-1s+),
not continuously. Before this fix, `finished_time_ms` was set to the
elapsed time of the FIRST sample whose progress reached 100% -- so a
runner's recorded finish was always late by up to one whole sample
interval, and that lateness varies per lane depending on exactly when
their antenna happened to poll. In a close 500 m sprint that is enough to
flip who "won" on paper.

This fixes it by linearly interpolating between the last sample that was
still under the target and the first sample at/over it, estimating the
instant the metric actually crossed the line. See
RaceManager._interpolated_finish_time_ms in hub_server/usecases/
race_manager.py.

Payloads here are antenna-shaped: elapsed_time_ms=0, timestamp_epoch_ms
(absolute), delta_distance_m / delta_energy_kcal -- exactly what the
production BLE/FTMS -> edge -> hub path sends. RaceManager derives elapsed
time from timestamp_epoch_ms - start_time_epoch_ms and accumulates the
metric from deltas (see _elapsed_time_ms / _session_metric_value).
"""

from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.race_results_query import RaceResultsQuery


def test_distance_finish_time_interpolates_between_samples():
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.register_node("treadmill-01", "Runner A")
    manager.start_race()
    start_ms = manager.get_start_time_epoch_ms()

    # Sample 1: still short of the line (490 m at 88000 ms elapsed).
    manager.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 88000,
            "delta_distance_m": 490.0,
        }
    )
    # Sample 2: crosses the line (502 m at 90000 ms elapsed).
    progress = manager.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 90000,
            "delta_distance_m": 12.0,
        }
    )

    # 88000 + (500-490)/(502-490) * (90000-88000) = 89666.67 -> 89667
    assert progress["treadmill-01"]["finished_time_ms"] == 89667


def test_finish_time_interpolation_can_flip_cross_lane_ranking():
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.register_node("node-a", "Runner A")
    manager.register_node("node-b", "Runner B")
    manager.start_race()
    start_ms = manager.get_start_time_epoch_ms()

    # Both lanes' crossing samples land at the SAME timestamp (2000 ms),
    # but B was much closer to the line on the previous sample than A was,
    # so B actually crossed first and must rank ahead of A.
    manager.update_telemetry(
        {
            "node_id": "node-a",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 1000,
            "delta_distance_m": 490.0,
        }
    )
    manager.update_telemetry(
        {
            "node_id": "node-b",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 1000,
            "delta_distance_m": 480.0,
        }
    )
    manager.update_telemetry(
        {
            "node_id": "node-a",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 2000,
            "delta_distance_m": 12.0,  # 490 -> 502
        }
    )
    progress = manager.update_telemetry(
        {
            "node_id": "node-b",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 2000,
            "delta_distance_m": 30.0,  # 480 -> 510
        }
    )

    finish_a = progress["node-a"]["finished_time_ms"]
    finish_b = progress["node-b"]["finished_time_ms"]
    assert finish_a == 1833  # 1000 + 10/12*1000
    assert finish_b == 1667  # 1000 + 20/30*1000
    assert finish_b < finish_a

    # And the consumer that actually orders results by finished_time_ms
    # (RaceResultsQuery, used for the post-race results/records pages)
    # must rank B ahead of A -- not tied, as the pre-fix same-timestamp
    # samples would have produced.
    ordered = RaceResultsQuery._order_by_race_type(
        [progress["node-a"], progress["node-b"]], "distance"
    )
    assert [row["node_id"] for row in ordered] == ["node-b", "node-a"]


def test_finish_time_falls_back_to_elapsed_when_no_prior_subtarget_sample():
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.start_race()
    start_ms = manager.get_start_time_epoch_ms()

    # This node was never registered (no register_node / station
    # assignment before the race started), so it has no zero-initialized
    # progress row waiting for it -- the very first telemetry sample seen
    # for it already reads past the target, with no earlier sub-target row
    # to interpolate from, so the old behaviour (use this sample's own
    # elapsed time) applies.
    progress = manager.update_telemetry(
        {
            "node_id": "treadmill-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 3000,
            "delta_distance_m": 550.0,
        }
    )

    assert progress["treadmill-01"]["finished_time_ms"] == 3000


def test_calorie_finish_time_interpolates_between_samples():
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="calories", target_value=50.0))
    manager.register_node("bike-01", "Rider A")
    manager.start_race()
    start_ms = manager.get_start_time_epoch_ms()

    manager.update_telemetry(
        {
            "node_id": "bike-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 40000,
            "delta_energy_kcal": 40.0,
        }
    )
    progress = manager.update_telemetry(
        {
            "node_id": "bike-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 50000,
            "delta_energy_kcal": 20.0,  # 40 -> 60 kcal
        }
    )

    # 40000 + (50-40)/(60-40) * (50000-40000) = 45000
    assert progress["bike-01"]["finished_time_ms"] == 45000
