"""Training class domain model.

A training class is a coach-run, timed sequence of segments (warm-up /
work / rest / cool-down) that runs on the same hardware, stations, and
telemetry pipeline as a race -- but has no ranking, no finish detection,
and no auto-stop. The coach ends it manually.

This module is pure domain: no I/O, no FastAPI/MQTT/bleak imports, no
wall-clock access. `segment_at` in particular takes an already-computed
`elapsed_ms` so it stays trivially testable.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

MAX_TOTAL_DURATION_SEC = 4 * 3600  # 4 hours


class ClassSegment(BaseModel):
    kind: Literal["warmup", "work", "rest", "cooldown"]
    duration_sec: int = Field(
        ...,
        ge=5,
        le=3600,
        description="Segment duration in seconds, 5..3600 inclusive",
    )
    target_watts: int | None = Field(
        default=None,
        ge=1,
        le=2000,
        description=(
            "Prescribed target power in watts, 1..2000 inclusive when "
            "present. None means no prescribed intensity -- a plain timer "
            "segment, which is how every plan saved before this field "
            "existed reads back."
        ),
    )


class ClassPlan(BaseModel):
    segments: list[ClassSegment] = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Ordered list of 1..200 class segments",
    )

    @model_validator(mode="after")
    def validate_total_duration(self):
        total = sum(segment.duration_sec for segment in self.segments)
        if total > MAX_TOTAL_DURATION_SEC:
            raise ValueError(
                "total class duration must not exceed 4 hours "
                f"({MAX_TOTAL_DURATION_SEC} seconds); got {total} seconds"
            )
        return self

    @property
    def total_duration_sec(self) -> int:
        return sum(segment.duration_sec for segment in self.segments)


def _pinned_to_last(plan: ClassPlan) -> dict:
    last_index = len(plan.segments) - 1
    return _segment_result(
        index=last_index,
        segment=plan.segments[last_index],
        segment_remaining_ms=0,
        total_remaining_ms=0,
        finished=True,
    )


def _segment_result(
    *,
    index: int,
    segment: ClassSegment,
    segment_remaining_ms: int,
    total_remaining_ms: int,
    finished: bool,
) -> dict:
    # `target_watts` is only added to the result when the segment actually
    # prescribes one. Plans saved before this field existed have every
    # segment's target_watts at None, so their segment_at() output keeps
    # the exact key set it always had -- callers that already destructure a
    # fixed shape (or assert one with equality) see no change at all.
    result = {
        "index": index,
        "kind": segment.kind,
        "segment_remaining_ms": segment_remaining_ms,
        "total_remaining_ms": total_remaining_ms,
        "finished": finished,
    }
    if segment.target_watts is not None:
        result["target_watts"] = segment.target_watts
    return result


def segment_at(elapsed_ms: int, plan: ClassPlan) -> dict:
    """Pure lookup: which segment is active at `elapsed_ms` into `plan`.

    No I/O, no clock access -- callers pass in already-computed elapsed
    time. The plan itself never auto-stops: once elapsed_ms reaches or
    passes the plan's total duration, `finished` becomes True as a
    display signal only, pinned to the last segment with zero remaining
    time. Negative/zero elapsed_ms clamps to the first segment.
    """
    if elapsed_ms < 0:
        elapsed_ms = 0

    total_ms = plan.total_duration_sec * 1000

    if elapsed_ms >= total_ms:
        return _pinned_to_last(plan)

    cumulative_ms = 0
    for index, segment in enumerate(plan.segments):
        segment_end_ms = cumulative_ms + segment.duration_sec * 1000
        if elapsed_ms < segment_end_ms:
            return _segment_result(
                index=index,
                segment=segment,
                segment_remaining_ms=segment_end_ms - elapsed_ms,
                total_remaining_ms=total_ms - elapsed_ms,
                finished=False,
            )
        cumulative_ms = segment_end_ms

    # Unreachable given the total_ms check above, but keeps the function
    # total rather than falling off the end.
    return _pinned_to_last(plan)
