"""Phase 5 course state-machine tests (architecture plan section 15)."""

import pytest

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_venue import (
    HyroxSensorClass,
    default_hyrox_course_profile,
)
from hub_server.usecases.hyrox_sensor_registry import (
    FINISH_LINE,
    START_LINE,
    HyroxTelemetryEvent,
)
from hub_server.usecases.hyrox_assignment_store import (
    ClaimSource,
    HyroxAssignmentStore,
)
from hub_server.usecases.hyrox_progress import HyroxProgressTracker
from hub_server.usecases.hyrox_course_engine import HyroxCourseEngine


def _engine():
    store = HyroxAssignmentStore()
    tracker = HyroxProgressTracker()
    engine = HyroxCourseEngine(default_hyrox_course_profile(), store, tracker)
    return engine, store, tracker


def _ftms(resource_id, group, distance, ts=0):
    return HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.FTMS_MACHINE,
        resource_group_id=group,
        resource_id=resource_id,
        tag_id=None,
        timestamp_epoch_ms=ts,
        metrics={"distance_m": distance},
    )


def _cross(resource_id, endpoint, tag, ts=0):
    return HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.RFID_ENDPOINT_PAIR,
        resource_group_id="shared_turf_lanes",
        resource_id=resource_id,
        endpoint=endpoint,
        tag_id=tag,
        timestamp_epoch_ms=ts,
    )


def test_stage_order_is_config_driven():
    engine, _, _ = _engine()
    state = engine.register_subject("alex")
    assert state.current_stage == HyroxStage.RUN_1
    assert engine._next_stage(HyroxStage.RUN_1) == HyroxStage.SKI_ERG
    assert engine._next_stage(HyroxStage.WALL_BALLS) == HyroxStage.FINISHED


def test_run_completion_advances_and_releases_treadmill():
    engine, store, _ = _engine()
    engine.register_subject("alex")
    engine.start(0)
    store.claim("treadmill-01", "alex", "TAG_ALEX", HyroxStage.RUN_1,
                ClaimSource.OPERATOR, 0)

    engine.process(_ftms("treadmill-01", "run_treadmills", 0), 1)      # baseline
    engine.process(_ftms("treadmill-01", "run_treadmills", 1000), 2)   # reaches 1000m

    state = engine.state_of("alex")
    assert state.current_stage == HyroxStage.SKI_ERG        # advanced
    assert store.active_on("treadmill-01") is None          # resource released


def test_cannot_skip_to_row_through_sensor_noise():
    # Acceptance: an athlete cannot skip from run_2 to row through sensor noise.
    engine, store, _ = _engine()
    state = engine.register_subject("alex")
    engine.start(0)
    state.current_stage = HyroxStage.RUN_2
    # Alex is bound to a treadmill for run_2, but a rower streams noise.
    store.claim("rower-01", "alex", "TAG_ALEX", HyroxStage.ROW, ClaimSource.OPERATOR, 0)

    engine.process(_ftms("rower-01", "row_pool", 5000), 1)  # row event while on run_2

    assert engine.state_of("alex").current_stage == HyroxStage.RUN_2  # no skip
    assert any(d.kind == "out_of_sequence" for d in engine.diagnostics)


def test_shared_lane_reads_are_interpreted_as_current_stage():
    # Acceptance: shared-lane reads infer the stage from athlete state.
    engine, store, _ = _engine()
    state = engine.register_subject("alex")
    engine.start(0)
    state.current_stage = HyroxStage.SLED_PUSH
    store.claim("turf-lane-1", "alex", "TAG_ALEX", HyroxStage.SLED_PUSH,
                ClaimSource.OPERATOR, 0)

    # 4 lengths on the shared lane (first crossing registers position).
    engine.process(_cross("turf-lane-1", START_LINE, "TAG_ALEX"), 1)
    engine.process(_cross("turf-lane-1", FINISH_LINE, "TAG_ALEX"), 2)
    engine.process(_cross("turf-lane-1", START_LINE, "TAG_ALEX"), 3)
    engine.process(_cross("turf-lane-1", FINISH_LINE, "TAG_ALEX"), 4)
    engine.process(_cross("turf-lane-1", START_LINE, "TAG_ALEX"), 5)

    # Interpreted as sled_push (current stage), completed, advanced to run_3.
    assert engine.state_of("alex").current_stage == HyroxStage.RUN_3
    assert store.active_on("turf-lane-1") is None  # released on completion


def test_abandon_freezes_stage_and_releases_resource():
    engine, store, _ = _engine()
    state = engine.register_subject("alex")
    engine.start(0)
    state.current_stage = HyroxStage.WALL_BALLS
    store.claim("wallball-1", "alex", "TAG_ALEX", HyroxStage.WALL_BALLS,
                ClaimSource.OPERATOR, 0)

    engine.abandon("alex", 900)

    assert engine.state_of("alex").status == "abandoned"
    assert engine.state_of("alex").current_stage == HyroxStage.WALL_BALLS  # frozen
    assert store.active_on("wallball-1") is None


def test_force_complete_stage_advances():
    engine, store, _ = _engine()
    engine.register_subject("alex")
    engine.start(0)
    store.claim("ski-1", "alex", "TAG_ALEX", HyroxStage.RUN_1, ClaimSource.OPERATOR, 0)

    engine.force_complete_stage("alex", 500)
    assert engine.state_of("alex").current_stage == HyroxStage.SKI_ERG


def test_clock_starts_on_first_activity_per_athlete():
    # Two athletes registered together but starting at different times must get
    # independent run_1 start stamps (no shared gun).
    engine, store, _ = _engine()
    engine.register_subject("alex")
    engine.register_subject("bella")
    engine.start(0)  # no global stamp
    assert HyroxStage.RUN_1 not in engine.state_of("alex").stage_start_ms

    store.claim("treadmill-01", "alex", "TAG_ALEX", HyroxStage.RUN_1, ClaimSource.OPERATOR, 0)
    store.claim("treadmill-02", "bella", "TAG_BELLA", HyroxStage.RUN_1, ClaimSource.OPERATOR, 0)
    engine.process(_ftms("treadmill-01", "run_treadmills", 0), 1000)   # alex starts at 1000
    engine.process(_ftms("treadmill-02", "run_treadmills", 0), 5000)   # bella starts at 5000

    assert engine.state_of("alex").stage_start_ms[HyroxStage.RUN_1] == 1000
    assert engine.state_of("bella").stage_start_ms[HyroxStage.RUN_1] == 5000


def test_finished_after_last_stage():
    engine, store, _ = _engine()
    state = engine.register_subject("alex")
    engine.start(0)
    state.current_stage = HyroxStage.WALL_BALLS
    engine.force_complete_stage("alex", 100)
    assert engine.state_of("alex").current_stage == HyroxStage.FINISHED
    assert engine.state_of("alex").status == "finished"
