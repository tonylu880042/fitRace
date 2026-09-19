"""Edge and hub clocks can drift (no RTC battery, no NTP on race day).
Antenna-sourced telemetry carries no elapsed_time_ms (see
antenna_ftms_manager.py), so the hub derives elapsed time from
timestamp_epoch_ms (the EDGE's clock) minus start_time_epoch_ms (the HUB's
clock). If the two disagree, every lane on that edge is shifted by the
same amount.

RaceManager is a usecase and must not import the node registry or any
adapter -- the corrective clock_offset_ms is passed in as an injected
callable, `clock_offset_ms_fn(edge_node_id) -> int`, resolved from the
telemetry payload's own edge_node_id. See RaceManager._elapsed_time_ms /
_clock_offset_ms in hub_server/usecases/race_manager.py.
"""

from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager


def test_elapsed_time_is_corrected_by_the_injected_clock_offset():
    manager = RaceManager(
        clock_offset_ms_fn=lambda edge_node_id: 800 if edge_node_id == "edge-02" else 0
    )
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.register_node("treadmill-01", "Runner A")
    manager.register_node("treadmill-02", "Runner B")
    manager.start_race()
    start_ms = manager.get_start_time_epoch_ms()

    # edge-01 has zero offset -- its timestamp is already hub-equivalent.
    progress = manager.update_telemetry(
        {
            "node_id": "treadmill-01",
            "edge_node_id": "edge-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 5000,
            "delta_distance_m": 50.0,
        }
    )
    assert progress["treadmill-01"]["elapsed_time_ms"] == 5000

    # edge-02's clock reads 800ms behind the hub's, so its raw timestamp is
    # 800ms earlier than the true (hub-equivalent) instant -- the offset
    # must be added back to correct it.
    progress = manager.update_telemetry(
        {
            "node_id": "treadmill-02",
            "edge_node_id": "edge-02",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 5000 - 800,
            "delta_distance_m": 50.0,
        }
    )
    assert progress["treadmill-02"]["elapsed_time_ms"] == 5000


def test_two_lanes_finishing_together_on_skewed_edges_correct_to_near_identical_times():
    manager = RaceManager(
        clock_offset_ms_fn=lambda edge_node_id: 800 if edge_node_id == "edge-02" else 0
    )
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.register_node("treadmill-01", "Runner A")
    manager.register_node("treadmill-02", "Runner B")
    manager.start_race()
    start_ms = manager.get_start_time_epoch_ms()

    # Both runners genuinely cross the line at the same real instant. Edge
    # 01 (offset 0) reports it on hub-equivalent time; edge-02 (offset
    # +800ms) reports the exact same real moments, just 800ms earlier on
    # its own (slow) clock.
    manager.update_telemetry(
        {
            "node_id": "treadmill-01",
            "edge_node_id": "edge-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 88000,
            "delta_distance_m": 490.0,
        }
    )
    progress_1 = manager.update_telemetry(
        {
            "node_id": "treadmill-01",
            "edge_node_id": "edge-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 90000,
            "delta_distance_m": 12.0,
        }
    )

    manager.update_telemetry(
        {
            "node_id": "treadmill-02",
            "edge_node_id": "edge-02",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 88000 - 800,
            "delta_distance_m": 490.0,
        }
    )
    progress_2 = manager.update_telemetry(
        {
            "node_id": "treadmill-02",
            "edge_node_id": "edge-02",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 90000 - 800,
            "delta_distance_m": 12.0,
        }
    )

    finish_1 = progress_1["treadmill-01"]["finished_time_ms"]
    finish_2 = progress_2["treadmill-02"]["finished_time_ms"]
    assert finish_1 is not None and finish_2 is not None
    # Corrected, the two should be near-identical -- far tighter than the
    # 800ms of raw clock skew between the two edges.
    assert abs(finish_1 - finish_2) <= 50


def test_elapsed_time_is_unchanged_when_no_clock_offset_fn_is_injected():
    # Regression: a manager built the old way (no clock_offset_ms_fn) must
    # behave exactly as before.
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.register_node("treadmill-01", "Runner A")
    manager.start_race()
    start_ms = manager.get_start_time_epoch_ms()

    progress = manager.update_telemetry(
        {
            "node_id": "treadmill-01",
            "edge_node_id": "edge-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 5000,
            "delta_distance_m": 50.0,
        }
    )
    assert progress["treadmill-01"]["elapsed_time_ms"] == 5000


def test_elapsed_time_unaffected_by_offset_when_edge_has_no_recorded_offset():
    # A node/edge the callable doesn't know about must fall back to 0
    # correction -- today's existing behaviour.
    manager = RaceManager(clock_offset_ms_fn=lambda edge_node_id: 0)
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.register_node("treadmill-01", "Runner A")
    manager.start_race()
    start_ms = manager.get_start_time_epoch_ms()

    progress = manager.update_telemetry(
        {
            "node_id": "treadmill-01",
            "edge_node_id": "edge-01",
            "elapsed_time_ms": 0,
            "timestamp_epoch_ms": start_ms + 5000,
            "delta_distance_m": 50.0,
        }
    )
    assert progress["treadmill-01"]["elapsed_time_ms"] == 5000
