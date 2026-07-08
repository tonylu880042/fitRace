"""Phase 3 resource-assignment lifecycle and attribution tests."""

import pytest

from hub_server.domain.models import HyroxStage
from hub_server.usecases.hyrox_sensor_registry import HyroxTelemetryEvent
from hub_server.domain.hyrox_venue import HyroxSensorClass
from hub_server.usecases.hyrox_assignment_store import (
    AssignmentCloseReason,
    ClaimSource,
    HyroxAssignmentStore,
)


def _ftms_event(resource_id, ts=1000, distance=100.0):
    return HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.FTMS_MACHINE,
        resource_group_id="run_treadmills",
        resource_id=resource_id,
        tag_id=None,
        timestamp_epoch_ms=ts,
        metrics={"distance_m": distance},
    )


def _rfid_event(resource_id, tag_id, ts=1000):
    return HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.RFID_ENDPOINT_PAIR,
        resource_group_id="shared_turf_lanes",
        resource_id=resource_id,
        endpoint="finish_line",
        tag_id=tag_id,
        timestamp_epoch_ms=ts,
    )


def _claim(store, resource, subject, tag, stage=HyroxStage.RUN_1, ts=0):
    return store.claim(resource, subject, tag, stage, ClaimSource.OPERATOR, ts)


def test_ftms_only_credits_the_assigned_subject():
    # Acceptance: FTMS data from treadmill-01 only updates the athlete assigned to it.
    store = HyroxAssignmentStore()
    _claim(store, "treadmill-01", "alex", "TAG_ALEX")
    _claim(store, "treadmill-02", "bella", "TAG_BELLA")

    assert store.attribute(_ftms_event("treadmill-01")).subject_id == "alex"
    assert store.attribute(_ftms_event("treadmill-02")).subject_id == "bella"


def test_rfid_read_only_credits_the_assigned_lane_occupant():
    # Acceptance: RFID lane reads only update the athlete assigned to that lane.
    store = HyroxAssignmentStore()
    _claim(store, "turf-lane-1", "alex", "TAG_ALEX", stage=HyroxStage.SLED_PUSH)

    ok = store.attribute(_rfid_event("turf-lane-1", "TAG_ALEX"))
    assert ok.subject_id == "alex"

    # A different tag read on that lane is a conflict, credited to nobody.
    conflict = store.attribute(_rfid_event("turf-lane-1", "TAG_BELLA"))
    assert conflict.subject_id is None
    assert conflict.diagnostic is not None


def test_lane_conflict_produces_diagnostic():
    # Acceptance: lane conflict produces a diagnostic event.
    store = HyroxAssignmentStore()
    _claim(store, "turf-lane-1", "alex", "TAG_ALEX", stage=HyroxStage.SLED_PUSH)
    store.attribute(_rfid_event("turf-lane-1", "TAG_BELLA"))
    assert any(d.kind == "conflict" for d in store.diagnostics)


def test_claim_on_occupied_resource_is_rejected_with_diagnostic():
    store = HyroxAssignmentStore()
    assert _claim(store, "turf-lane-1", "alex", "TAG_ALEX") is not None
    # Second athlete cannot claim the same lane.
    assert _claim(store, "turf-lane-1", "bella", "TAG_BELLA", ts=10) is None
    assert any(d.kind == "conflict" for d in store.diagnostics)
    # Original assignment is untouched.
    assert store.active_on("turf-lane-1").subject_id == "alex"


def test_new_claim_supersedes_the_subjects_stale_assignment():
    store = HyroxAssignmentStore()
    _claim(store, "turf-lane-1", "alex", "TAG_ALEX", stage=HyroxStage.SLED_PUSH, ts=0)
    # Alex is read at a treadmill without lane-1 being released first.
    _claim(store, "treadmill-03", "alex", "TAG_ALEX", stage=HyroxStage.RUN_2, ts=100)

    # lane-1 is auto-freed (superseded); treadmill-03 now holds alex.
    assert store.active_on("turf-lane-1") is None
    assert store.active_on("treadmill-03").subject_id == "alex"
    superseded = [a for a in store._closed if a.close_reason == AssignmentCloseReason.SUPERSEDED]
    assert len(superseded) == 1 and superseded[0].resource_id == "turf-lane-1"


def test_reclaim_same_resource_is_idempotent_and_refreshes_tag():
    store = HyroxAssignmentStore()
    a1 = _claim(store, "turf-lane-1", "team-alpha", "TAG_TOM")
    # Relay handoff: same team, new active member tag.
    a2 = _claim(store, "turf-lane-1", "team-alpha", "TAG_JERRY", ts=50)
    assert a1.assignment_id == a2.assignment_id
    assert store.active_on("turf-lane-1").active_tag_id == "TAG_JERRY"


def test_close_reasons_and_idempotent_close():
    store = HyroxAssignmentStore()
    _claim(store, "turf-lane-1", "alex", "TAG_ALEX")
    closed = store.close("turf-lane-1", AssignmentCloseReason.COMPLETED, 500)
    assert closed.close_reason == AssignmentCloseReason.COMPLETED
    assert store.active_on("turf-lane-1") is None
    # Closing again is a no-op.
    assert store.close("turf-lane-1", AssignmentCloseReason.OPERATOR_RELEASE, 600) is None


def test_abandon_closes_assignment():
    store = HyroxAssignmentStore()
    _claim(store, "turf-lane-1", "alex", "TAG_ALEX", stage=HyroxStage.WALL_BALLS)
    store.close("turf-lane-1", AssignmentCloseReason.ABANDONED, 700)
    assert store.active_on("turf-lane-1") is None
    assert store._closed[-1].close_reason == AssignmentCloseReason.ABANDONED


def test_anonymous_ftms_without_binding_is_diagnostic_not_credited():
    store = HyroxAssignmentStore()
    result = store.attribute(_ftms_event("treadmill-09"))
    assert result.subject_id is None
    assert any(d.kind == "anonymous_no_binding" for d in store.diagnostics)


def test_availability_is_a_projection():
    store = HyroxAssignmentStore()
    _claim(store, "treadmill-01", "alex", "TAG_ALEX")
    avail = store.availability(["treadmill-01", "treadmill-02"])
    assert avail == {"treadmill-01": "in_use", "treadmill-02": "free"}
