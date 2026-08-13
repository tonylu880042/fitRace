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
    last_index = len(plan.segments) - 1

    if elapsed_ms >= total_ms:
        return {
            "index": last_index,
            "kind": plan.segments[last_index].kind,
            "segment_remaining_ms": 0,
            "total_remaining_ms": 0,
            "finished": True,
        }

    cumulative_ms = 0
    for index, segment in enumerate(plan.segments):
        segment_end_ms = cumulative_ms + segment.duration_sec * 1000
        if elapsed_ms < segment_end_ms:
            return {
                "index": index,
                "kind": segment.kind,
                "segment_remaining_ms": segment_end_ms - elapsed_ms,
                "total_remaining_ms": total_ms - elapsed_ms,
                "finished": False,
            }
        cumulative_ms = segment_end_ms

    # Unreachable given the total_ms check above, but keeps the function
    # total rather than falling off the end.
    return {
        "index": last_index,
        "kind": plan.segments[last_index].kind,
        "segment_remaining_ms": 0,
        "total_remaining_ms": 0,
        "finished": True,
    }
