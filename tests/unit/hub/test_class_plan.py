import pytest
from pydantic import ValidationError

from hub_server.domain.class_models import ClassPlan, ClassSegment, segment_at


def _plan(*durations_and_kinds):
    return ClassPlan(
        segments=[
            ClassSegment(kind=kind, duration_sec=duration)
            for kind, duration in durations_and_kinds
        ]
    )


# -- ClassSegment / ClassPlan validation -------------------------------


def test_class_segment_accepts_every_valid_kind():
    for kind in ("warmup", "work", "rest", "cooldown"):
        segment = ClassSegment(kind=kind, duration_sec=60)
        assert segment.kind == kind


def test_class_segment_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        ClassSegment(kind="sprint", duration_sec=60)


def test_class_segment_rejects_duration_below_minimum():
    with pytest.raises(ValidationError):
        ClassSegment(kind="work", duration_sec=4)


def test_class_segment_accepts_minimum_duration():
    segment = ClassSegment(kind="work", duration_sec=5)
    assert segment.duration_sec == 5


def test_class_segment_rejects_duration_above_maximum():
    with pytest.raises(ValidationError):
        ClassSegment(kind="work", duration_sec=3601)


def test_class_segment_accepts_maximum_duration():
    segment = ClassSegment(kind="work", duration_sec=3600)
    assert segment.duration_sec == 3600


def test_class_plan_requires_at_least_one_segment():
    with pytest.raises(ValidationError):
        ClassPlan(segments=[])


def test_class_plan_rejects_more_than_200_segments():
    segments = [{"kind": "work", "duration_sec": 10} for _ in range(201)]
    with pytest.raises(ValidationError):
        ClassPlan(segments=segments)


def test_class_plan_accepts_exactly_200_segments():
    segments = [{"kind": "work", "duration_sec": 10} for _ in range(200)]
    plan = ClassPlan(segments=segments)
    assert len(plan.segments) == 200


def test_class_plan_rejects_total_duration_over_four_hours():
    # 3600 * 5 = 18000s = 5 hours, over the 4-hour cap.
    segments = [{"kind": "work", "duration_sec": 3600} for _ in range(5)]
    with pytest.raises(ValidationError):
        ClassPlan(segments=segments)


def test_class_plan_accepts_total_duration_at_exactly_four_hours():
    # 3600 * 4 = 14400s = exactly 4 hours.
    segments = [{"kind": "work", "duration_sec": 3600} for _ in range(4)]
    plan = ClassPlan(segments=segments)
    assert plan.total_duration_sec == 4 * 3600


def test_class_plan_total_duration_sec_sums_segments():
    plan = _plan(("warmup", 300), ("work", 1200), ("rest", 60), ("cooldown", 300))
    assert plan.total_duration_sec == 300 + 1200 + 60 + 300


# -- segment_at: basic behaviour -----------------------------------------


def test_segment_at_start_is_first_segment_not_finished():
    plan = _plan(("warmup", 300), ("work", 1200), ("cooldown", 300))
    result = segment_at(0, plan)
    assert result == {
        "index": 0,
        "kind": "warmup",
        "segment_remaining_ms": 300_000,
        "total_remaining_ms": 1800_000,
        "finished": False,
    }


def test_segment_at_negative_elapsed_clamps_to_first_segment():
    plan = _plan(("warmup", 300), ("work", 1200))
    result = segment_at(-5000, plan)
    assert result["index"] == 0
    assert result["finished"] is False
    assert result["segment_remaining_ms"] == 300_000


def test_segment_at_zero_elapsed_clamps_to_first_segment():
    plan = _plan(("warmup", 300), ("work", 1200))
    result = segment_at(0, plan)
    assert result["index"] == 0
    assert result["finished"] is False


def test_segment_at_mid_second_segment():
    plan = _plan(("warmup", 300), ("work", 1200), ("cooldown", 300))
    # 300s into the plan (start of "work") + 100s = 400s = 400_000ms
    result = segment_at(400_000, plan)
    assert result["index"] == 1
    assert result["kind"] == "work"
    assert result["segment_remaining_ms"] == 1200_000 - 100_000
    assert result["total_remaining_ms"] == 1800_000 - 400_000
    assert result["finished"] is False


def test_segment_at_past_total_duration_is_finished_pinned_to_last_segment():
    plan = _plan(("warmup", 300), ("work", 1200), ("cooldown", 300))
    result = segment_at(999_999_999, plan)
    assert result == {
        "index": 2,
        "kind": "cooldown",
        "segment_remaining_ms": 0,
        "total_remaining_ms": 0,
        "finished": True,
    }


def test_segment_at_never_auto_stops_finished_is_display_only():
    # Calling segment_at with huge elapsed time must not raise or mutate
    # anything -- it is a pure read, "finished" is advisory only.
    plan = _plan(
        ("work", 10),
    )
    first = segment_at(50_000, plan)
    second = segment_at(100_000, plan)
    assert first == second
    assert first["finished"] is True


# -- segment_at: exact boundaries (the load-bearing case) -----------------


def test_segment_at_exact_boundary_between_differently_sized_segments():
    # warmup=300s, work=1200s, rest=60s, cooldown=300s (18 real-world-ish
    # segments would be excessive here; boundaries are the point, not count)
    plan = _plan(("warmup", 300), ("work", 1200), ("rest", 60), ("cooldown", 300))

    # Exactly at the end of segment 0 (300_000ms) -> must be segment 1, not 0.
    at_end_of_warmup = segment_at(300_000, plan)
    assert at_end_of_warmup["index"] == 1
    assert at_end_of_warmup["kind"] == "work"
    assert at_end_of_warmup["segment_remaining_ms"] == 1200_000

    # One millisecond before that boundary -> still segment 0.
    just_before = segment_at(299_999, plan)
    assert just_before["index"] == 0
    assert just_before["kind"] == "warmup"
    assert just_before["segment_remaining_ms"] == 1

    # Exactly at the end of segment 1 (300_000 + 1200_000 = 1_500_000ms)
    # -> must be segment 2, not 1.
    at_end_of_work = segment_at(1_500_000, plan)
    assert at_end_of_work["index"] == 2
    assert at_end_of_work["kind"] == "rest"
    assert at_end_of_work["segment_remaining_ms"] == 60_000

    just_before_work_end = segment_at(1_499_999, plan)
    assert just_before_work_end["index"] == 1
    assert just_before_work_end["kind"] == "work"
    assert just_before_work_end["segment_remaining_ms"] == 1

    # Exactly at the end of segment 2 (1_500_000 + 60_000 = 1_560_000ms)
    # -> must be segment 3, not 2.
    at_end_of_rest = segment_at(1_560_000, plan)
    assert at_end_of_rest["index"] == 3
    assert at_end_of_rest["kind"] == "cooldown"

    just_before_rest_end = segment_at(1_559_999, plan)
    assert just_before_rest_end["index"] == 2
    assert just_before_rest_end["kind"] == "rest"

    # Exactly at the very end of the whole plan -> finished, pinned to last.
    total_ms = plan.total_duration_sec * 1000
    at_total_end = segment_at(total_ms, plan)
    assert at_total_end["finished"] is True
    assert at_total_end["index"] == 3

    just_before_total_end = segment_at(total_ms - 1, plan)
    assert just_before_total_end["finished"] is False
    assert just_before_total_end["index"] == 3
    assert just_before_total_end["segment_remaining_ms"] == 1


# -- a realistic 18-segment plan -------------------------------------------


def _realistic_18_segment_plan() -> ClassPlan:
    segments = [("warmup", 300)]
    for _ in range(8):
        segments.append(("work", 240))
        segments.append(("rest", 60))
    segments.append(("cooldown", 300))
    # 1 warmup + 8*(work+rest) + 1 cooldown = 18 segments
    return _plan(*segments)


def test_realistic_18_segment_plan_has_expected_shape():
    plan = _realistic_18_segment_plan()
    assert len(plan.segments) == 18
    assert plan.segments[0].kind == "warmup"
    assert plan.segments[-1].kind == "cooldown"
    expected_total = 300 + 8 * (240 + 60) + 300
    assert plan.total_duration_sec == expected_total


def test_realistic_18_segment_plan_walks_through_every_segment_in_order():
    plan = _realistic_18_segment_plan()
    cumulative_ms = 0
    for index, segment in enumerate(plan.segments):
        # Sample the midpoint of each segment and confirm segment_at agrees.
        midpoint_ms = cumulative_ms + (segment.duration_sec * 1000) // 2
        result = segment_at(midpoint_ms, plan)
        assert result["index"] == index
        assert result["kind"] == segment.kind
        assert result["finished"] is False
        cumulative_ms += segment.duration_sec * 1000

    # And once fully elapsed, it reports finished on the final segment.
    final = segment_at(cumulative_ms, plan)
    assert final["finished"] is True
    assert final["index"] == len(plan.segments) - 1
