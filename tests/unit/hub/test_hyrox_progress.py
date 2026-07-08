"""Phase 4 stage-reducer tests (architecture plan section 15)."""

import pytest

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_venue import HyroxSensorClass, HyroxTargetType
from hub_server.usecases.hyrox_sensor_registry import (
    FINISH_LINE,
    START_LINE,
    HyroxTelemetryEvent,
)
from hub_server.usecases.hyrox_progress import HyroxProgressTracker


def _ftms(resource_id, distance, ts=0):
    return HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.FTMS_MACHINE,
        resource_group_id="run_treadmills",
        resource_id=resource_id,
        tag_id=None,
        timestamp_epoch_ms=ts,
        metrics={"distance_m": distance},
    )


def _cross(resource_id, endpoint, ts=0):
    return HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.RFID_ENDPOINT_PAIR,
        resource_group_id="shared_turf_lanes",
        resource_id=resource_id,
        endpoint=endpoint,
        tag_id="TAG",
        timestamp_epoch_ms=ts,
    )


def _rep(resource_id, ts=0):
    return HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.REP_COUNTER,
        resource_group_id="wall_ball_targets",
        resource_id=resource_id,
        tag_id=None,
        timestamp_epoch_ms=ts,
    )


def _distance(t, subject, event):
    return t.apply(event, subject, HyroxStage.RUN_1, HyroxTargetType.DISTANCE_M, 1000)


def _length(t, subject, stage, event):
    return t.apply(event, subject, stage, HyroxTargetType.LENGTHS, 4)


# --- distance_m reducer (FTMS, baseline-delta) ---

def test_distance_baseline_then_accumulates_to_target():
    t = HyroxProgressTracker()
    assert _distance(t, "alex", _ftms("tm-1", 500)).value == 0     # baseline
    assert _distance(t, "alex", _ftms("tm-1", 800)).value == 300   # +300
    final = _distance(t, "alex", _ftms("tm-1", 1500))              # +700 -> 1000
    assert final.value == 1000 and final.complete is True


def test_distance_is_monotonic_across_counter_reset():
    # Acceptance: FTMS counter resets do not decrease progress.
    t = HyroxProgressTracker()
    _distance(t, "alex", _ftms("tm-1", 100))          # baseline
    assert _distance(t, "alex", _ftms("tm-1", 400)).value == 300
    # Device resets to a low reading; progress must not drop.
    after_reset = _distance(t, "alex", _ftms("tm-1", 50))
    assert after_reset.value == 350                    # 300 + 50, never below 300
    assert _distance(t, "alex", _ftms("tm-1", 90)).value == 390


def test_distance_ignores_event_without_distance_metric():
    t = HyroxProgressTracker()
    _distance(t, "alex", _ftms("tm-1", 100))
    ev = HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.FTMS_MACHINE, resource_group_id="g",
        resource_id="tm-1", timestamp_epoch_ms=1, metrics={},
    )
    upd = _distance(t, "alex", ev)
    assert upd.counted is False


# --- lengths reducer (alternating RFID endpoints) ---

def test_lengths_count_on_alternating_crossings():
    t = HyroxProgressTracker()
    # First crossing registers position, no length.
    assert _length(t, "alex", HyroxStage.SLED_PUSH, _cross("lane-1", START_LINE)).value == 0
    assert _length(t, "alex", HyroxStage.SLED_PUSH, _cross("lane-1", FINISH_LINE)).value == 1
    assert _length(t, "alex", HyroxStage.SLED_PUSH, _cross("lane-1", START_LINE)).value == 2
    assert _length(t, "alex", HyroxStage.SLED_PUSH, _cross("lane-1", FINISH_LINE)).value == 3
    final = _length(t, "alex", HyroxStage.SLED_PUSH, _cross("lane-1", START_LINE))
    assert final.value == 4 and final.complete is True


def test_duplicate_same_endpoint_read_does_not_count():
    # Acceptance: duplicate same-endpoint RFID reads do not increment lengths.
    t = HyroxProgressTracker()
    _length(t, "alex", HyroxStage.SLED_PUSH, _cross("lane-1", START_LINE))    # position
    r1 = _length(t, "alex", HyroxStage.SLED_PUSH, _cross("lane-1", FINISH_LINE))  # length 1
    dup = _length(t, "alex", HyroxStage.SLED_PUSH, _cross("lane-1", FINISH_LINE))  # duplicate
    assert r1.value == 1
    assert dup.value == 1 and dup.counted is False


def test_same_lane_serves_different_stages_independently():
    # Acceptance: same physical lane can serve different stages by athlete state.
    t = HyroxProgressTracker()
    # Same resource id, two different stages -> independent length counts.
    _length(t, "alex", HyroxStage.SLED_PUSH, _cross("lane-1", START_LINE))
    _length(t, "alex", HyroxStage.SLED_PUSH, _cross("lane-1", FINISH_LINE))  # sled_push=1
    _length(t, "alex", HyroxStage.FARMERS_CARRY, _cross("lane-1", START_LINE))
    fc = _length(t, "alex", HyroxStage.FARMERS_CARRY, _cross("lane-1", FINISH_LINE))
    sled = t.apply(_cross("lane-1", START_LINE), "alex", HyroxStage.SLED_PUSH,
                   HyroxTargetType.LENGTHS, 4)
    assert fc.value == 1
    assert sled.value == 2  # sled_push kept its own count


# --- reps reducer ---

def test_reps_increment_to_target():
    t = HyroxProgressTracker()
    for i in range(74):
        t.apply(_rep("wb-1"), "alex", HyroxStage.WALL_BALLS, HyroxTargetType.REPS, 75)
    final = t.apply(_rep("wb-1"), "alex", HyroxStage.WALL_BALLS, HyroxTargetType.REPS, 75)
    assert final.value == 75 and final.complete is True


def test_pulse_to_meter_node_events_add_distance():
    t = HyroxProgressTracker()
    ev = HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.FTMS_MACHINE, resource_group_id="run_treadmills",
        resource_id="tm-1", endpoint=None, timestamp_epoch_ms=1, pulse_to_meter=250.0,
    )
    for _ in range(3):
        upd = t.apply(ev, "alex", HyroxStage.RUN_1, HyroxTargetType.DISTANCE_M, 1000)
    assert upd.value == 750
    upd = t.apply(ev, "alex", HyroxStage.RUN_1, HyroxTargetType.DISTANCE_M, 1000)
    assert upd.value == 1000 and upd.complete is True


def test_entry_gate_tap_does_not_add_pulse_distance():
    # A pulse-based treadmill's entry-gate bind tap carries pulse_to_meter but
    # an endpoint; it must not add phantom distance.
    t = HyroxProgressTracker()
    gate = HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.RFID_ENTRY_GATE, resource_group_id="run_treadmills",
        resource_id="tm-1", endpoint="entry_gate", tag_id="TAG", timestamp_epoch_ms=1,
        pulse_to_meter=250.0,
    )
    upd = t.apply(gate, "alex", HyroxStage.RUN_1, HyroxTargetType.DISTANCE_M, 1000)
    assert upd.value == 0 and upd.counted is False


def test_entry_gate_rfid_read_does_not_count_as_a_rep():
    # The bind tap that co-locates with a wall-ball target must not add a rep.
    t = HyroxProgressTracker()
    gate = HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.RFID_ENTRY_GATE, resource_group_id="wall_ball_targets",
        resource_id="wb-1", endpoint="entry_gate", tag_id="TAG", timestamp_epoch_ms=1,
    )
    upd = t.apply(gate, "alex", HyroxStage.WALL_BALLS, HyroxTargetType.REPS, 75)
    assert upd.value == 0 and upd.counted is False


# --- manual override ---

def test_manual_force_complete():
    t = HyroxProgressTracker()
    upd = t.force_complete("alex", HyroxStage.SKI_ERG, target_value=1000)
    assert upd.complete is True
    # A manual-type stage is not advanced by sensor events, but reads complete
    # once forced.
    ev = _ftms("ski-1", 10)
    state = t.apply(ev, "alex", HyroxStage.SKI_ERG, HyroxTargetType.MANUAL, 0)
    assert state.complete is True
