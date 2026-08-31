"""Tests for the target_watts prescription on ClassSegment.

A training class plan used to prescribe only WHAT KIND of segment and HOW
LONG. This adds an optional target power in watts per segment so a plan can
prescribe INTENSITY too.

Backward compatibility is the load-bearing property here: the live venue hub
already has a saved plan on disk whose segments have no target_watts key at
all. That exact payload must still validate and load, with target_watts
coming back as None on every segment -- see
test_legacy_plan_without_target_watts_still_validates below, which uses the
literal JSON shape from the field report.

segment_at()'s existing return-dict shape for plans with no target_watts is
also load-bearing: tests/unit/hub/test_class_plan.py asserts several of its
outputs with exact dict equality (no target_watts key at all). This module
does not touch that file. Instead, segment_at() only adds the target_watts
key to its result when the active segment actually has one -- a plan with no
targets produces byte-for-byte the same result shape it always has.
"""

import pytest
from pydantic import ValidationError

from hub_server.domain.class_models import ClassPlan, ClassSegment, segment_at

# -- ClassSegment.target_watts validation -----------------------------------


def test_class_segment_target_watts_defaults_to_none():
    segment = ClassSegment(kind="work", duration_sec=60)
    assert segment.target_watts is None


def test_class_segment_accepts_explicit_none_target_watts():
    segment = ClassSegment(kind="work", duration_sec=60, target_watts=None)
    assert segment.target_watts is None


def test_class_segment_accepts_target_watts_minimum():
    segment = ClassSegment(kind="work", duration_sec=60, target_watts=1)
    assert segment.target_watts == 1


def test_class_segment_accepts_target_watts_maximum():
    segment = ClassSegment(kind="work", duration_sec=60, target_watts=2000)
    assert segment.target_watts == 2000


def test_class_segment_accepts_a_typical_target_watts():
    segment = ClassSegment(kind="work", duration_sec=60, target_watts=180)
    assert segment.target_watts == 180


def test_class_segment_rejects_target_watts_below_minimum():
    with pytest.raises(ValidationError):
        ClassSegment(kind="work", duration_sec=60, target_watts=0)


def test_class_segment_rejects_negative_target_watts():
    with pytest.raises(ValidationError):
        ClassSegment(kind="work", duration_sec=60, target_watts=-50)


def test_class_segment_rejects_target_watts_above_maximum():
    with pytest.raises(ValidationError):
        ClassSegment(kind="work", duration_sec=60, target_watts=2001)


# -- Backward compatibility: the exact live-venue payload shape -------------


_LEGACY_PLAN_PAYLOAD = {
    "segments": [
        {"kind": "warmup", "duration_sec": 300},
        {"kind": "work", "duration_sec": 1200},
        {"kind": "cooldown", "duration_sec": 300},
    ]
}


def test_legacy_plan_without_target_watts_still_validates():
    # Literal payload shape from the venue's already-saved plan on disk --
    # no target_watts key anywhere. Must still validate and load, or the
    # venue's saved class disappears on upgrade.
    plan = ClassPlan.model_validate(_LEGACY_PLAN_PAYLOAD)
    assert len(plan.segments) == 3
    for segment in plan.segments:
        assert segment.target_watts is None


def test_legacy_plan_round_trips_through_model_dump():
    # race_manager.py persists class_plan via model_dump(); confirm the
    # legacy payload survives a validate -> dump round trip with
    # target_watts present as None (the shape callers like race_manager.py
    # already rely on for every other field).
    plan = ClassPlan.model_validate(_LEGACY_PLAN_PAYLOAD)
    dumped = plan.model_dump()
    for segment in dumped["segments"]:
        assert segment["target_watts"] is None


# -- segment_at: target_watts exposure ---------------------------------------


def _plan(*segments):
    return ClassPlan(segments=[ClassSegment(**segment) for segment in segments])


def test_segment_at_exposes_target_watts_for_active_segment():
    plan = _plan(
        {"kind": "warmup", "duration_sec": 300, "target_watts": 100},
        {"kind": "work", "duration_sec": 1200, "target_watts": 180},
    )
    result = segment_at(0, plan)
    assert result["target_watts"] == 100

    result = segment_at(400_000, plan)
    assert result["target_watts"] == 180


def test_segment_at_target_watts_follows_the_active_segment_not_the_first():
    plan = _plan(
        {"kind": "warmup", "duration_sec": 300, "target_watts": 100},
        {"kind": "work", "duration_sec": 1200, "target_watts": 220},
        {"kind": "cooldown", "duration_sec": 300, "target_watts": 50},
    )
    # Deep into the work segment -- must report the work target, not the
    # warmup target the plan started with.
    result = segment_at(900_000, plan)
    assert result["kind"] == "work"
    assert result["target_watts"] == 220


def test_segment_at_pinned_to_last_segment_exposes_its_target_watts():
    plan = _plan(
        {"kind": "warmup", "duration_sec": 300, "target_watts": 100},
        {"kind": "cooldown", "duration_sec": 300, "target_watts": 40},
    )
    result = segment_at(999_999_999, plan)
    assert result["finished"] is True
    assert result["target_watts"] == 40


def test_segment_at_legacy_plan_with_no_targets_omits_the_key_entirely():
    # The load-bearing backward-compat case: a plan with no target_watts
    # anywhere must produce a segment_at() result with the EXACT same key
    # set it always had -- no "target_watts": None noise -- so the existing
    # exact-equality assertions in tests/unit/hub/test_class_plan.py keep
    # passing untouched.
    plan = ClassPlan.model_validate(_LEGACY_PLAN_PAYLOAD)
    result = segment_at(0, plan)
    assert "target_watts" not in result
    assert result == {
        "index": 0,
        "kind": "warmup",
        "segment_remaining_ms": 300_000,
        "total_remaining_ms": 1800_000,
        "finished": False,
    }


def test_segment_at_mixed_plan_only_some_segments_targeted():
    # One segment has a target, the next does not -- the reported value
    # must track which segment is active, not "sticky" from an earlier one.
    plan = _plan(
        {"kind": "warmup", "duration_sec": 300, "target_watts": 90},
        {"kind": "rest", "duration_sec": 60},
    )
    warmup_result = segment_at(0, plan)
    assert warmup_result["target_watts"] == 90

    rest_result = segment_at(300_000, plan)
    assert rest_result["kind"] == "rest"
    assert "target_watts" not in rest_result


# -- Mutation-style regression: target_watts must reach the returned dict,
# not merely exist as a validated field on the segment.


def test_segment_at_target_watts_is_the_correct_segments_value_not_a_stale_one():
    plan = _plan(
        {"kind": "warmup", "duration_sec": 300, "target_watts": 111},
        {"kind": "work", "duration_sec": 300, "target_watts": 222},
        {"kind": "cooldown", "duration_sec": 300, "target_watts": 333},
    )
    seen_targets = []
    for elapsed_ms in (0, 300_000, 600_000):
        result = segment_at(elapsed_ms, plan)
        seen_targets.append(result["target_watts"])
    assert seen_targets == [111, 222, 333]
