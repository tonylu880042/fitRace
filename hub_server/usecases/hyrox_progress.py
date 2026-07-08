"""Hyrox stage progress reducers.

Phase 4 of the architecture plan: turn attributed sensor events into stage
progress by target type, replacing hardcoded counters. Progress is tracked per
(subject_id, stage); the one-assignment invariant guarantees a subject uses one
resource per stage at a time, so the resource id need not be part of the key.

Reducers:
- distance_m: FTMS distance via baseline-delta, monotonic across counter resets.
- lengths:    alternating RFID endpoint crossings; duplicate endpoints ignored.
- reps:       one increment per rep-counter event.
- manual:     operator override only (force_complete).
"""

from dataclasses import dataclass
from typing import Optional

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_venue import HyroxTargetType
from hub_server.usecases.hyrox_sensor_registry import (
    FINISH_LINE,
    START_LINE,
    HyroxTelemetryEvent,
)


@dataclass
class ProgressUpdate:
    value: float
    target: float
    complete: bool
    counted: bool  # did this event actually change progress?


@dataclass
class _DistanceState:
    accumulated: float = 0.0
    last_raw: Optional[float] = None


@dataclass
class _LengthState:
    count: int = 0
    last_endpoint: Optional[str] = None


@dataclass
class _RepState:
    count: int = 0


class HyroxProgressTracker:
    def __init__(self):
        self._distance: dict[tuple[str, HyroxStage], _DistanceState] = {}
        self._length: dict[tuple[str, HyroxStage], _LengthState] = {}
        self._rep: dict[tuple[str, HyroxStage], _RepState] = {}
        self._forced: set[tuple[str, HyroxStage]] = set()

    def apply(
        self,
        event: HyroxTelemetryEvent,
        subject_id: str,
        stage: HyroxStage,
        target_type: HyroxTargetType,
        target_value: float,
    ) -> ProgressUpdate:
        if target_type == HyroxTargetType.DISTANCE_M:
            return self._distance_reduce(subject_id, stage, event, target_value)
        if target_type == HyroxTargetType.LENGTHS:
            return self._length_reduce(subject_id, stage, event, target_value)
        if target_type == HyroxTargetType.REPS:
            return self._rep_reduce(subject_id, stage, target_value)
        # manual / time_ms are not advanced by sensor events
        return ProgressUpdate(0.0, target_value, self._is_forced(subject_id, stage),
                              counted=False)

    def force_complete(
        self, subject_id: str, stage: HyroxStage, target_value: float = 0.0
    ) -> ProgressUpdate:
        """Operator override: mark the stage complete regardless of sensors."""
        self._forced.add((subject_id, stage))
        return ProgressUpdate(target_value, target_value, True, counted=True)

    def _is_forced(self, subject_id: str, stage: HyroxStage) -> bool:
        return (subject_id, stage) in self._forced

    def _distance_reduce(self, subject_id, stage, event, target) -> ProgressUpdate:
        key = (subject_id, stage)
        st = self._distance.setdefault(key, _DistanceState())
        raw = (event.metrics or {}).get("distance_m")
        if raw is None:
            return ProgressUpdate(st.accumulated, target, st.accumulated >= target, False)
        if st.last_raw is None:
            # First reading is the baseline; it adds nothing on its own.
            st.last_raw = raw
            return ProgressUpdate(st.accumulated, target, st.accumulated >= target, False)
        delta = raw - st.last_raw
        if delta > 0:
            st.accumulated += delta
        elif delta < 0:
            # Device counter reset: treat the new raw as distance since reset.
            # Progress only ever increases.
            st.accumulated += raw
        st.last_raw = raw
        counted = delta != 0
        return ProgressUpdate(st.accumulated, target, st.accumulated >= target, counted)

    def _length_reduce(self, subject_id, stage, event, target) -> ProgressUpdate:
        key = (subject_id, stage)
        st = self._length.setdefault(key, _LengthState())
        endpoint = event.endpoint
        if endpoint not in (START_LINE, FINISH_LINE):
            return ProgressUpdate(st.count, target, st.count >= target, False)
        if st.last_endpoint is None:
            # First crossing registers position; a length needs the opposite mat.
            st.last_endpoint = endpoint
            return ProgressUpdate(st.count, target, st.count >= target, False)
        if endpoint == st.last_endpoint:
            # Duplicate same-endpoint read: not a completed length.
            return ProgressUpdate(st.count, target, st.count >= target, False)
        st.count += 1
        st.last_endpoint = endpoint
        return ProgressUpdate(st.count, target, st.count >= target, True)

    def _rep_reduce(self, subject_id, stage, target) -> ProgressUpdate:
        key = (subject_id, stage)
        st = self._rep.setdefault(key, _RepState())
        st.count += 1
        return ProgressUpdate(st.count, target, st.count >= target, True)
