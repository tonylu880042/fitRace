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


# --- Phase 8: DQ, penalties, reinstate ---


def test_disqualify_freezes_stage_and_releases_resource():
    engine, store, _ = _engine()
    state = engine.register_subject("alex")
    engine.start(0)
    state.current_stage = HyroxStage.WALL_BALLS
    store.claim("wallball-1", "alex", "TAG_ALEX", HyroxStage.WALL_BALLS,
                ClaimSource.OPERATOR, 0)

    engine.disqualify("alex", "left station before work complete", 900)

    state = engine.state_of("alex")
    assert state.status == "disqualified"
    assert state.current_stage == HyroxStage.WALL_BALLS  # frozen
    assert state.dq_reason == "left station before work complete"
    assert state.terminal_at_ms == 900
    assert store.active_on("wallball-1") is None


def test_disqualify_is_a_noop_on_unknown_or_non_racing_subject():
    engine, store, _ = _engine()
    engine.register_subject("alex")
    engine.start(0)
    engine.abandon("alex", 100)

    engine.disqualify("alex", "too late", 200)
    assert engine.state_of("alex").status == "abandoned"  # unchanged

    engine.disqualify("ghost", "no such subject", 200)
    assert engine.state_of("ghost") is None


def test_add_penalty_appends_and_returns_false_for_unknown_subject():
    engine, store, _ = _engine()
    engine.register_subject("alex")
    engine.start(0)

    assert engine.add_penalty("alex", 10_000, "movement standard breach", 500) is True
    assert engine.add_penalty("alex", 5_000, "second breach", 600) is True
    assert engine.add_penalty("ghost", 1_000, "no such subject", 700) is False

    penalties = engine.state_of("alex").penalties
    assert [p.penalty_ms for p in penalties] == [10_000, 5_000]
    assert penalties[0].reason == "movement standard breach"
    assert penalties[1].issued_at_epoch_ms == 600


def test_reinstate_returns_status_to_racing_and_keeps_frozen_progress():
    engine, store, _ = _engine()
    state = engine.register_subject("alex")
    engine.start(0)
    state.current_stage = HyroxStage.SKI_ERG
    state.stage_arrived_ms[HyroxStage.SKI_ERG] = 50
    engine.abandon("alex", 900)

    assert engine.reinstate("alex", 1000) is True

    state = engine.state_of("alex")
    assert state.status == "racing"
    assert state.current_stage == HyroxStage.SKI_ERG          # frozen, not reset
    assert state.stage_arrived_ms[HyroxStage.SKI_ERG] == 50    # untouched
    assert any(d.kind == "reinstate" for d in engine.diagnostics)


def test_reinstate_works_from_disqualified_too():
    engine, store, _ = _engine()
    engine.register_subject("alex")
    engine.start(0)
    engine.disqualify("alex", "equipment misuse", 500)

    assert engine.reinstate("alex", 600) is True
    assert engine.state_of("alex").status == "racing"


def test_reinstate_guard_rejects_racing_or_finished_or_unknown_subject():
    engine, store, _ = _engine()
    engine.register_subject("alex")
    engine.start(0)

    assert engine.reinstate("alex", 100) is False   # still racing

    engine.force_complete_stage("alex", 100)
    engine.state_of("alex").status = "finished"
    assert engine.reinstate("alex", 200) is False   # finished, not DNF/DQ

    assert engine.reinstate("ghost", 200) is False  # unknown subject


def test_reinstate_is_idempotent_guarded():
    engine, store, _ = _engine()
    engine.register_subject("alex")
    engine.start(0)
    engine.abandon("alex", 900)

    assert engine.reinstate("alex", 1000) is True
    # Second call: status is now "racing", so the guard rejects it.
    assert engine.reinstate("alex", 1100) is False
