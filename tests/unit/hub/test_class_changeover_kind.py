"""Tests for the new "changeover" segment kind on ClassSegment.

A training class used to distinguish only warmup / work / rest / cooldown.
Circuit-style venues rotate athletes between machines (rower to bike to
skierg) between work intervals -- a materially different instruction from
"catch your breath where you are" (the existing `rest` kind), because it
takes longer and the wall needs to tell athletes to move. This adds a fifth
kind, `changeover`, alongside the existing four.

Backward compatibility is the load-bearing property here: the venue already
has plans saved on disk using only the four original kinds. Those must
still validate and load completely unchanged -- see
test_legacy_four_kind_plan_still_validates below, which uses a literal
four-kind JSON payload (the shape every plan saved before this feature
existed has on disk).
"""

import pytest
from pydantic import ValidationError

from hub_server.domain.class_models import ClassPlan, ClassSegment, segment_at

# -- ClassSegment accepts the new kind --------------------------------------


def test_class_segment_accepts_changeover_kind():
    segment = ClassSegment(kind="changeover", duration_sec=45)
    assert segment.kind == "changeover"


def test_class_segment_still_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        ClassSegment(kind="sprint", duration_sec=60)


def test_class_segment_accepts_every_valid_kind_including_changeover():
    for kind in ("warmup", "work", "rest", "cooldown", "changeover"):
        segment = ClassSegment(kind=kind, duration_sec=60)
        assert segment.kind == kind


# -- Backward compatibility: legacy four-kind plans on disk -----------------


def test_legacy_four_kind_plan_still_validates():
    # The literal shape of a plan saved before this feature existed: only
    # the original four kinds, no changeover anywhere. Must load exactly
    # as it always has.
    legacy_payload = {
        "segments": [
            {"kind": "warmup", "duration_sec": 300, "target_watts": None},
            {"kind": "work", "duration_sec": 1200, "target_watts": 180},
            {"kind": "rest", "duration_sec": 60, "target_watts": None},
            {"kind": "cooldown", "duration_sec": 300, "target_watts": None},
        ]
    }
    plan = ClassPlan.model_validate(legacy_payload)
    assert [s.kind for s in plan.segments] == ["warmup", "work", "rest", "cooldown"]
    assert plan.segments[1].target_watts == 180
    assert plan.total_duration_sec == 300 + 1200 + 60 + 300


def test_legacy_plan_segment_at_output_unchanged_by_new_kind():
    legacy_payload = {
        "segments": [
            {"kind": "warmup", "duration_sec": 300},
            {"kind": "work", "duration_sec": 1200},
            {"kind": "cooldown", "duration_sec": 300},
        ]
    }
    plan = ClassPlan.model_validate(legacy_payload)
    result = segment_at(0, plan)
    assert result == {
        "index": 0,
        "kind": "warmup",
        "segment_remaining_ms": 300_000,
        "total_remaining_ms": 1800_000,
        "finished": False,
    }


# -- segment_at reports the new kind correctly -------------------------------


def test_segment_at_reports_changeover_kind():
    plan = ClassPlan(
        segments=[
            ClassSegment(kind="work", duration_sec=120),
            ClassSegment(kind="changeover", duration_sec=30),
            ClassSegment(kind="work", duration_sec=120),
        ]
    )
    result = segment_at(120_000, plan)
    assert result["index"] == 1
    assert result["kind"] == "changeover"
    assert result["segment_remaining_ms"] == 30_000


def test_changeover_segment_with_no_target_watts_omits_target_watts_key():
    # A changeover segment has no power target by nature -- it must behave
    # exactly like any other untargeted segment: no target_watts key in the
    # segment_at() result at all.
    plan = ClassPlan(
        segments=[
            ClassSegment(kind="changeover", duration_sec=30),
        ]
    )
    result = segment_at(0, plan)
    assert "target_watts" not in result
