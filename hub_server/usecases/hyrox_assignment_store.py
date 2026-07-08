"""In-memory Hyrox resource-assignment store.

Phase 3 of the architecture plan: attribute sensor events to the right
athlete/team through active assignments, maintaining the core invariant that a
resource holds at most one open assignment. Implements the lifecycle from
section 11 (claim / close with four reasons / superseded / conflict diagnostics).

The store is standalone: it does not reach into HyroxManager and is not yet
wired into the live MQTT path. HTTP APIs and ingestion wiring arrive with the
Phase 6 operator UI.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from hub_server.domain.models import HyroxStage
from hub_server.usecases.hyrox_sensor_registry import HyroxTelemetryEvent


class AssignmentCloseReason(str, Enum):
    COMPLETED = "completed"                # stage target reached
    ABANDONED = "abandoned"               # athlete pressed the lane abandon button
    OPERATOR_RELEASE = "operator_release"  # manual release by an operator
    SUPERSEDED = "superseded"             # subject re-claimed elsewhere while open


class ClaimSource(str, Enum):
    OPERATOR = "operator"            # competition: explicit assignment
    DYNAMIC_CLAIM = "dynamic_claim"  # training: first valid read claims a free resource
    AUTO_SCHEDULER = "auto_scheduler"


@dataclass
class ResourceAssignment:
    assignment_id: str
    resource_id: str
    subject_id: str          # team_id; an individual is a team of one
    active_tag_id: str       # member tag currently attributed on this resource
    stage: HyroxStage
    source: ClaimSource
    assigned_at_epoch_ms: int
    status: str = "active"   # active | closed
    close_reason: Optional[AssignmentCloseReason] = None
    closed_at_epoch_ms: Optional[int] = None


@dataclass
class AssignmentDiagnostic:
    kind: str                # conflict | anonymous_no_binding | unassigned_read
    resource_id: str
    detail: str
    timestamp_epoch_ms: int


@dataclass
class Attribution:
    """Result of attributing a sensor event. subject_id is None when the event
    cannot be credited (unknown binding or a conflict)."""
    subject_id: Optional[str]
    assignment_id: Optional[str] = None
    diagnostic: Optional[AssignmentDiagnostic] = None

    @property
    def ok(self) -> bool:
        return self.subject_id is not None


class HyroxAssignmentStore:
    def __init__(self):
        self._by_resource: dict[str, ResourceAssignment] = {}  # invariant lives here
        self._resource_of_subject: dict[str, str] = {}         # subject_id -> resource_id
        self._closed: list[ResourceAssignment] = []            # history / audit
        self.diagnostics: list[AssignmentDiagnostic] = []
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"asg-{self._counter}"

    def _diag(self, kind: str, resource_id: str, detail: str, ts: int) -> AssignmentDiagnostic:
        d = AssignmentDiagnostic(kind=kind, resource_id=resource_id, detail=detail,
                                 timestamp_epoch_ms=ts)
        self.diagnostics.append(d)
        return d

    # --- Queries ---

    def active_on(self, resource_id: str) -> Optional[ResourceAssignment]:
        return self._by_resource.get(resource_id)

    def active_for_subject(self, subject_id: str) -> Optional[ResourceAssignment]:
        rid = self._resource_of_subject.get(subject_id)
        return self._by_resource.get(rid) if rid else None

    def availability(self, resource_ids) -> dict[str, str]:
        """Projection of assignments: free | in_use per resource."""
        return {
            rid: ("in_use" if rid in self._by_resource else "free")
            for rid in resource_ids
        }

    # --- Lifecycle ---

    def claim(
        self,
        resource_id: str,
        subject_id: str,
        active_tag_id: str,
        stage: HyroxStage,
        source: ClaimSource,
        timestamp_epoch_ms: int,
    ) -> Optional[ResourceAssignment]:
        """Claim a resource for a subject. Returns the assignment, or None on a
        rejected claim (resource already held by someone else -> diagnostic)."""
        existing = self._by_resource.get(resource_id)
        if existing is not None:
            if existing.subject_id == subject_id:
                # Idempotent re-claim; refresh the active member tag (relay handoff).
                existing.active_tag_id = active_tag_id
                return existing
            self._diag(
                "conflict", resource_id,
                f"claim by {subject_id} rejected; resource held by {existing.subject_id}",
                timestamp_epoch_ms,
            )
            return None

        # A subject can hold only one active assignment: supersede any prior one.
        prior_rid = self._resource_of_subject.get(subject_id)
        if prior_rid and prior_rid != resource_id:
            self.close(prior_rid, AssignmentCloseReason.SUPERSEDED, timestamp_epoch_ms)

        assignment = ResourceAssignment(
            assignment_id=self._next_id(),
            resource_id=resource_id,
            subject_id=subject_id,
            active_tag_id=active_tag_id,
            stage=stage,
            source=source,
            assigned_at_epoch_ms=timestamp_epoch_ms,
        )
        self._by_resource[resource_id] = assignment
        self._resource_of_subject[subject_id] = resource_id
        return assignment

    def close(
        self,
        resource_id: str,
        reason: AssignmentCloseReason,
        timestamp_epoch_ms: int,
    ) -> Optional[ResourceAssignment]:
        """Close the open assignment on a resource. Idempotent no-op if none."""
        assignment = self._by_resource.pop(resource_id, None)
        if assignment is None:
            return None
        assignment.status = "closed"
        assignment.close_reason = reason
        assignment.closed_at_epoch_ms = timestamp_epoch_ms
        if self._resource_of_subject.get(assignment.subject_id) == resource_id:
            del self._resource_of_subject[assignment.subject_id]
        self._closed.append(assignment)
        return assignment

    # --- Attribution (a normalized event -> which subject to credit) ---

    def attribute(self, event: HyroxTelemetryEvent) -> Attribution:
        """Dispatch a normalized event to its assigned subject.

        RFID events (carry a tag) verify the read tag against the assigned one;
        FTMS/anonymous events are pure resource->subject lookups.
        """
        if event.tag_id is not None:
            return self._attribute_rfid(event.resource_id, event.tag_id,
                                        event.timestamp_epoch_ms)
        return self._attribute_anonymous(event.resource_id, event.timestamp_epoch_ms)

    def _attribute_rfid(self, resource_id: str, tag_id: str, ts: int) -> Attribution:
        assignment = self._by_resource.get(resource_id)
        if assignment is None:
            # No binding yet -> caller decides (e.g. dynamic claim in training mode).
            return Attribution(subject_id=None)
        if assignment.active_tag_id != tag_id:
            d = self._diag(
                "conflict", resource_id,
                f"read tag {tag_id} does not match assigned tag {assignment.active_tag_id}",
                ts,
            )
            return Attribution(subject_id=None, diagnostic=d)
        return Attribution(subject_id=assignment.subject_id,
                           assignment_id=assignment.assignment_id)

    def _attribute_anonymous(self, resource_id: str, ts: int) -> Attribution:
        assignment = self._by_resource.get(resource_id)
        if assignment is None:
            d = self._diag(
                "anonymous_no_binding", resource_id,
                "anonymous telemetry with no active assignment",
                ts,
            )
            return Attribution(subject_id=None, diagnostic=d)
        return Attribution(subject_id=assignment.subject_id,
                           assignment_id=assignment.assignment_id)
