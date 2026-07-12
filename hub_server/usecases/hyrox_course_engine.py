"""Hyrox course state machine.

Phase 5 of the architecture plan: make stage order and targets config-driven,
enforce ordered transitions, emit out-of-sequence diagnostics, and release the
resource assignment when a stage completes. This is the integration layer that
ties the sensor registry (Phase 2), assignment store (Phase 3), and progress
reducers (Phase 4) to a course profile (Phase 1).

The engine is standalone and does not replace the live HyroxManager yet; the
cutover happens with the Phase 6 UI.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_venue import HyroxCourseProfile, HyroxStageDefinition
from hub_server.usecases.hyrox_sensor_registry import HyroxTelemetryEvent
from hub_server.usecases.hyrox_assignment_store import (
    AssignmentCloseReason,
    HyroxAssignmentStore,
)
from hub_server.usecases.hyrox_progress import HyroxProgressTracker


@dataclass
class Penalty:
    """A time sanction added to an athlete's final result (Phase 8). Not
    terminal -- the athlete keeps racing; the penalty is summed into
    total_ms when the result is finalized."""
    penalty_ms: int
    reason: str
    issued_at_epoch_ms: int


@dataclass
class SubjectState:
    subject_id: str
    current_stage: HyroxStage
    status: str = "racing"  # racing | finished | abandoned | disqualified
    stage_start_ms: dict[HyroxStage, int] = field(default_factory=dict)  # became current
    stage_arrived_ms: dict[HyroxStage, int] = field(default_factory=dict)  # first activity
    stage_end_ms: dict[HyroxStage, int] = field(default_factory=dict)  # completed
    stage_resource: dict[HyroxStage, str] = field(default_factory=dict)  # resource used
    dq_reason: Optional[str] = None  # required once status == disqualified
    penalties: list[Penalty] = field(default_factory=list)
    terminal_at_ms: Optional[int] = None  # timestamp of abandon/disqualify, for reinstate audit


@dataclass
class EngineDiagnostic:
    kind: str            # out_of_sequence
    subject_id: str
    resource_id: str
    detail: str
    timestamp_epoch_ms: int


class HyroxCourseEngine:
    def __init__(
        self,
        profile: HyroxCourseProfile,
        store: HyroxAssignmentStore,
        tracker: HyroxProgressTracker,
        on_diagnostic: Optional[Callable[[EngineDiagnostic], None]] = None,
    ):
        # Stage order and targets are config-driven, derived from the profile.
        self._order: list[HyroxStage] = [s.stage for s in profile.stages]
        self._order.append(HyroxStage.FINISHED)
        self._def: dict[HyroxStage, HyroxStageDefinition] = {
            s.stage: s for s in profile.stages
        }
        self._store = store
        self._tracker = tracker
        self._subjects: dict[str, SubjectState] = {}
        self.diagnostics: list[EngineDiagnostic] = []
        # Phase 7: durable audit sink (HyroxService wires this to the SQLite
        # results store). Optional -- unit tests construct a bare engine.
        self._on_diagnostic = on_diagnostic

    # --- Setup ---

    def register_subject(self, subject_id: str) -> SubjectState:
        state = SubjectState(subject_id=subject_id, current_stage=self._order[0])
        self._subjects[subject_id] = state
        return state

    def start(self, now_ms: int):
        # No global gun: each athlete's clock starts on their own first activity
        # (see _ensure_started). With limited resources some athletes queue, so a
        # shared start time would make every individual total identical and count
        # queue waiting against athletes who have not begun.
        pass

    @staticmethod
    def _ensure_started(state: SubjectState, now_ms: int):
        if HyroxStage.RUN_1 not in state.stage_start_ms:
            state.stage_start_ms[HyroxStage.RUN_1] = now_ms

    def state_of(self, subject_id: str) -> Optional[SubjectState]:
        return self._subjects.get(subject_id)

    def current_stage_of(self, subject_id: str) -> Optional[HyroxStage]:
        state = self._subjects.get(subject_id)
        return state.current_stage if state else None

    def stage_definition(self, stage: HyroxStage) -> Optional[HyroxStageDefinition]:
        return self._def.get(stage)

    def allows(self, subject_id: str, resource_group_id: str) -> bool:
        """Whether the subject's current stage accepts events from this group.
        Used by dynamic claim to bind only in-sequence reads."""
        state = self._subjects.get(subject_id)
        if state is None or state.status != "racing":
            return False
        stage_def = self._def.get(state.current_stage)
        return stage_def is not None and resource_group_id in stage_def.allowed_resource_groups

    # --- Event processing ---

    def process(self, event: HyroxTelemetryEvent, now_ms: int):
        attribution = self._store.attribute(event)
        if not attribution.ok:
            return  # store logged the conflict / unbound diagnostic
        state = self._subjects.get(attribution.subject_id)
        if state is None or state.status != "racing":
            return
        self._ensure_started(state, now_ms)

        stage_def = self._def.get(state.current_stage)
        if stage_def is None:
            return

        # Ordered-transition guard: only events from a resource group allowed
        # for the CURRENT stage may advance it. This is what stops sensor noise
        # from a later station (e.g. a rower while the athlete is still running)
        # from skipping the course, and it is how a shared lane's reads are
        # interpreted as the athlete's current stage rather than by station id.
        if event.resource_group_id not in stage_def.allowed_resource_groups:
            self._diag(
                "out_of_sequence", state.subject_id, event.resource_id,
                f"{state.subject_id} on {state.current_stage.value} received event for "
                f"group {event.resource_group_id}",
                now_ms,
            )
            return

        # First valid activity at this station: record arrival (for the roxzone
        # split) and which resource is being used.
        stage = state.current_stage
        if stage not in state.stage_arrived_ms:
            state.stage_arrived_ms[stage] = now_ms
        state.stage_resource[stage] = event.resource_id

        update = self._tracker.apply(
            event, state.subject_id, state.current_stage,
            stage_def.target_type, stage_def.target_value,
        )
        if update.complete:
            self._advance(state, now_ms)

    # --- Terminal / override transitions ---

    def abandon(self, subject_id: str, now_ms: int):
        """One-way DNF: freeze the current stage and release the resource."""
        state = self._subjects.get(subject_id)
        if state is None or state.status != "racing":
            return
        # Terminal states always carry a start timestamp. For an athlete who
        # abandons before their first activity, the DNF time is their start.
        self._ensure_started(state, now_ms)
        state.status = "abandoned"
        state.terminal_at_ms = now_ms
        self._release(subject_id, AssignmentCloseReason.ABANDONED, now_ms)

    def disqualify(self, subject_id: str, reason: str, now_ms: int):
        """One-way DQ: a judge-called rules violation. Freezes the current
        stage and releases the resource exactly like abandon, but requires a
        reason and is not the athlete's own decision (see spec section 2)."""
        state = self._subjects.get(subject_id)
        if state is None or state.status != "racing":
            return
        self._ensure_started(state, now_ms)
        state.status = "disqualified"
        state.dq_reason = reason
        state.terminal_at_ms = now_ms
        self._release(subject_id, AssignmentCloseReason.DISQUALIFIED, now_ms)

    def add_penalty(self, subject_id: str, penalty_ms: int, reason: str, now_ms: int) -> bool:
        """Non-terminal time sanction. Returns False if the subject is
        unknown; otherwise the penalty is appended regardless of status so an
        operator can record a breach spotted right at the finish line."""
        state = self._subjects.get(subject_id)
        if state is None:
            return False
        state.penalties.append(
            Penalty(penalty_ms=penalty_ms, reason=reason, issued_at_epoch_ms=now_ms)
        )
        return True

    def reinstate(self, subject_id: str, now_ms: int) -> bool:
        """Operator correction for a mistaken DNF/DQ (button misfire,
        overturned call). Status returns to racing; current_stage, progress,
        and stage timestamps stay exactly as frozen -- they are not cleared.
        Resources are NOT auto-restored (see spec section 2). Returns False
        if the subject is unknown or not in a reinstatable state."""
        state = self._subjects.get(subject_id)
        if state is None or state.status not in ("abandoned", "disqualified"):
            return False
        prior_status = state.status
        prior_terminal_ms = state.terminal_at_ms
        state.status = "racing"
        # A reinstated athlete is no longer DQ'd; a later finish must not
        # carry the overturned reason into the result.
        state.dq_reason = None
        state.terminal_at_ms = None
        detail = f"{subject_id} reinstated from {prior_status}"
        if prior_terminal_ms is not None:
            detail += f" (terminal at {prior_terminal_ms}ms)"
        self._diag("reinstate", subject_id, "", detail, now_ms)
        return True

    def force_complete_stage(self, subject_id: str, now_ms: int):
        """Operator override: complete the current stage regardless of sensors."""
        state = self._subjects.get(subject_id)
        if state is None or state.status != "racing":
            return
        self._ensure_started(state, now_ms)
        self._tracker.force_complete(subject_id, state.current_stage)
        self._advance(state, now_ms)

    # --- Internals ---

    def _advance(self, state: SubjectState, now_ms: int):
        completed = state.current_stage
        state.stage_end_ms[completed] = now_ms
        # Stage completion reliably releases the occupied resource.
        self._release(state.subject_id, AssignmentCloseReason.COMPLETED, now_ms)

        nxt = self._next_stage(completed)
        if nxt is None or nxt == HyroxStage.FINISHED:
            state.current_stage = HyroxStage.FINISHED
            state.status = "finished"
        else:
            state.current_stage = nxt
            state.stage_start_ms[nxt] = now_ms

    def _next_stage(self, stage: HyroxStage) -> Optional[HyroxStage]:
        idx = self._order.index(stage)
        return self._order[idx + 1] if idx + 1 < len(self._order) else None

    def _release(self, subject_id: str, reason: AssignmentCloseReason, now_ms: int):
        assignment = self._store.active_for_subject(subject_id)
        if assignment is not None:
            self._store.close(assignment.resource_id, reason, now_ms)

    def _diag(self, kind, subject_id, resource_id, detail, ts):
        d = EngineDiagnostic(kind=kind, subject_id=subject_id, resource_id=resource_id,
                             detail=detail, timestamp_epoch_ms=ts)
        self.diagnostics.append(d)
        if self._on_diagnostic is not None:
            self._on_diagnostic(d)

    # --- Persistence (Phase 7) ---

    _SNAPSHOT_DIAGNOSTICS_LIMIT = 200  # see HyroxAssignmentStore for rationale

    def to_dict(self) -> dict:
        return {
            "subjects": {
                sid: {
                    "current_stage": s.current_stage.value,
                    "status": s.status,
                    "stage_start_ms": {k.value: v for k, v in s.stage_start_ms.items()},
                    "stage_arrived_ms": {k.value: v for k, v in s.stage_arrived_ms.items()},
                    "stage_end_ms": {k.value: v for k, v in s.stage_end_ms.items()},
                    "stage_resource": {k.value: v for k, v in s.stage_resource.items()},
                    "dq_reason": s.dq_reason,
                    "penalties": [
                        {
                            "penalty_ms": p.penalty_ms,
                            "reason": p.reason,
                            "issued_at_epoch_ms": p.issued_at_epoch_ms,
                        }
                        for p in s.penalties
                    ],
                    "terminal_at_ms": s.terminal_at_ms,
                }
                for sid, s in self._subjects.items()
            },
            "diagnostics": [
                {
                    "kind": d.kind, "subject_id": d.subject_id, "resource_id": d.resource_id,
                    "detail": d.detail, "timestamp_epoch_ms": d.timestamp_epoch_ms,
                }
                for d in self.diagnostics[-self._SNAPSHOT_DIAGNOSTICS_LIMIT:]
            ],
        }

    def restore(self, data: dict) -> None:
        """Rebuild subject states and diagnostics in place. The engine itself
        must already be constructed (it is wired to the store/tracker it will
        process against), so this mutates rather than replaces the instance."""
        subjects: dict[str, SubjectState] = {}
        for sid, s in data.get("subjects", {}).items():
            subjects[sid] = SubjectState(
                subject_id=sid,
                current_stage=HyroxStage(s["current_stage"]),
                status=s.get("status", "racing"),
                stage_start_ms={
                    HyroxStage(k): v for k, v in s.get("stage_start_ms", {}).items()
                },
                stage_arrived_ms={
                    HyroxStage(k): v for k, v in s.get("stage_arrived_ms", {}).items()
                },
                stage_end_ms={
                    HyroxStage(k): v for k, v in s.get("stage_end_ms", {}).items()
                },
                stage_resource={
                    HyroxStage(k): v for k, v in s.get("stage_resource", {}).items()
                },
                dq_reason=s.get("dq_reason"),
                penalties=[
                    Penalty(
                        penalty_ms=p["penalty_ms"],
                        reason=p["reason"],
                        issued_at_epoch_ms=p["issued_at_epoch_ms"],
                    )
                    for p in s.get("penalties", [])
                ],
                terminal_at_ms=s.get("terminal_at_ms"),
            )
        self._subjects = subjects
        self.diagnostics = [
            EngineDiagnostic(
                kind=d["kind"], subject_id=d["subject_id"], resource_id=d["resource_id"],
                detail=d["detail"], timestamp_epoch_ms=d["timestamp_epoch_ms"],
            )
            for d in data.get("diagnostics", [])
        ]
