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

Phase 11 (doubles, spec section 5): a doubles team runs every 1 km segment on
two treadmills at once, so distance progress for a run stage is tracked per
member. The distance reducer's key gains an optional `member_tag` component
for this; every other reducer (and every non-doubles-run distance key) keeps
using `member_tag=None`, so individuals/relay callers are unaffected.
"""

from dataclasses import dataclass
from typing import Optional

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_venue import HyroxSensorClass, HyroxTargetType
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
        # Distance keys carry an optional member_tag (Phase 11 doubles-run
        # per-member tracking); None for individuals, relay, and every other
        # reducer.
        self._distance: dict[tuple[str, HyroxStage, Optional[str]], _DistanceState] = {}
        self._length: dict[tuple[str, HyroxStage], _LengthState] = {}
        self._rep: dict[tuple[str, HyroxStage], _RepState] = {}
        self._forced: set[tuple[str, HyroxStage]] = set()

    def seed_distance_baseline(
        self, subject_id: str, stage: HyroxStage, raw_distance_m: float,
        member_tag: Optional[str] = None,
    ) -> None:
        """Set the bind-time FTMS baseline without resetting existing progress."""
        state = self._distance.setdefault((subject_id, stage, member_tag), _DistanceState())
        if state.last_raw is None:
            state.last_raw = raw_distance_m

    def apply(
        self,
        event: HyroxTelemetryEvent,
        subject_id: str,
        stage: HyroxStage,
        target_type: HyroxTargetType,
        target_value: float,
        member_tag: Optional[str] = None,
    ) -> ProgressUpdate:
        if target_type == HyroxTargetType.DISTANCE_M:
            return self._distance_reduce(subject_id, stage, event, target_value, member_tag)
        if target_type == HyroxTargetType.LENGTHS:
            return self._length_reduce(subject_id, stage, event, target_value)
        if target_type == HyroxTargetType.REPS:
            return self._rep_reduce(subject_id, stage, event, target_value)
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

    def _distance_reduce(self, subject_id, stage, event, target, member_tag=None) -> ProgressUpdate:
        key = (subject_id, stage, member_tag)
        st = self._distance.setdefault(key, _DistanceState())
        raw = (event.metrics or {}).get("distance_m")
        if raw is not None and event.sensor_class != HyroxSensorClass.FTMS_MACHINE:
            return ProgressUpdate(st.accumulated, target, st.accumulated >= target, False)
        if raw is None:
            # Pulse-based machines add a fixed distance per node event. Only
            # genuine node telemetry counts -- an RFID read on the same unit
            # (e.g. the entry-gate bind tap) carries an endpoint and must not.
            if event.pulse_to_meter is not None and event.endpoint is None:
                st.accumulated += event.pulse_to_meter
                return ProgressUpdate(st.accumulated, target, st.accumulated >= target, True)
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

    def _rep_reduce(self, subject_id, stage, event, target) -> ProgressUpdate:
        key = (subject_id, stage)
        st = self._rep.setdefault(key, _RepState())
        # Only genuine rep-counter (node) events count. An RFID read on the same
        # resource -- e.g. the entry-gate bind tap -- carries an endpoint and
        # must not be counted as a rep.
        if event.endpoint is not None or "distance_m" in (event.metrics or {}):
            return ProgressUpdate(st.count, target, st.count >= target, False)
        st.count += 1
        return ProgressUpdate(st.count, target, st.count >= target, True)

    def value_of(self, subject_id: str, stage: HyroxStage,
                 target_type: HyroxTargetType, member_tag: Optional[str] = None) -> float:
        """Current accumulated progress for a (subject, stage), 0 if none.
        `member_tag` only applies to distance (doubles run stages); other
        target types are always tracked per-subject regardless of partner."""
        if target_type == HyroxTargetType.DISTANCE_M:
            st = self._distance.get((subject_id, stage, member_tag))
            return st.accumulated if st else 0.0
        key = (subject_id, stage)
        if target_type == HyroxTargetType.LENGTHS:
            st = self._length.get(key)
            return float(st.count) if st else 0.0
        if target_type == HyroxTargetType.REPS:
            st = self._rep.get(key)
            return float(st.count) if st else 0.0
        return 0.0

    def distance_values_for(self, subject_id: str, stage: HyroxStage) -> dict[str, float]:
        """Per-member accumulated distance for a doubles run stage: {tag:
        accumulated_m}. Empty for individuals/relay, where distance is not
        tracked per member tag."""
        return {
            tag: st.accumulated
            for (sid, stg, tag), st in self._distance.items()
            if sid == subject_id and stg == stage and tag is not None
        }

    # --- Persistence (Phase 7) ---

    def to_dict(self) -> dict:
        """Serialize all per-(subject, stage) accumulators. Dict keys are tuples
        and are not JSON-safe, so each map is flattened to a record list.

        `member_tag` is added additively to distance records (Phase 11,
        doubles per-member tracking); it is None for every non-doubles-run
        entry, so a version-1 restore (see restore()) round-trips unchanged."""
        return {
            "distance": [
                {"subject_id": sid, "stage": stage.value, "member_tag": member_tag,
                 "accumulated": st.accumulated, "last_raw": st.last_raw}
                for (sid, stage, member_tag), st in self._distance.items()
            ],
            "length": [
                {"subject_id": sid, "stage": stage.value,
                 "count": st.count, "last_endpoint": st.last_endpoint}
                for (sid, stage), st in self._length.items()
            ],
            "rep": [
                {"subject_id": sid, "stage": stage.value, "count": st.count}
                for (sid, stage), st in self._rep.items()
            ],
            "forced": [
                {"subject_id": sid, "stage": stage.value}
                for (sid, stage) in self._forced
            ],
        }

    def restore(self, data: dict) -> None:
        # Version 1 records predate member_tag and omit the key entirely;
        # .get(...) defaults those to None, the same value non-doubles-run
        # entries always use.
        self._distance = {
            (r["subject_id"], HyroxStage(r["stage"]), r.get("member_tag")):
                _DistanceState(accumulated=r["accumulated"], last_raw=r["last_raw"])
            for r in data.get("distance", [])
        }
        self._length = {
            (r["subject_id"], HyroxStage(r["stage"])):
                _LengthState(count=r["count"], last_endpoint=r["last_endpoint"])
            for r in data.get("length", [])
        }
        self._rep = {
            (r["subject_id"], HyroxStage(r["stage"])): _RepState(count=r["count"])
            for r in data.get("rep", [])
        }
        self._forced = {
            (r["subject_id"], HyroxStage(r["stage"])) for r in data.get("forced", [])
        }
