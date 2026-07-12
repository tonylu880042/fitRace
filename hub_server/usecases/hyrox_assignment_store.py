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
from typing import Callable, Optional

from hub_server.domain.models import HyroxStage
from hub_server.usecases.hyrox_sensor_registry import HyroxTelemetryEvent


class AssignmentCloseReason(str, Enum):
    COMPLETED = "completed"                # stage target reached
    ABANDONED = "abandoned"               # athlete pressed the lane abandon button
    DISQUALIFIED = "disqualified"         # judge-called DQ (Phase 8)
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
    def __init__(self, on_diagnostic: Optional[Callable[[AssignmentDiagnostic], None]] = None):
        self._by_resource: dict[str, ResourceAssignment] = {}  # invariant lives here
        # subject_id -> {active_tag_id: resource_id}. Individuals and relay
        # subjects have at most one entry; doubles subjects may hold two
        # concurrent run-stage entries, one per member tag (Phase 11, spec
        # section 5).
        self._resource_of_subject: dict[str, dict[str, str]] = {}
        self._closed: list[ResourceAssignment] = []            # history / audit
        self.diagnostics: list[AssignmentDiagnostic] = []
        self._counter = 0
        self._recently_closed: dict[str, int] = {}
        # Phase 7: durable audit sink (HyroxService wires this to the SQLite
        # results store). Optional -- unit tests construct a bare store.
        self._on_diagnostic = on_diagnostic

    def _next_id(self) -> str:
        self._counter += 1
        return f"asg-{self._counter}"

    def _diag(self, kind: str, resource_id: str, detail: str, ts: int) -> AssignmentDiagnostic:
        d = AssignmentDiagnostic(kind=kind, resource_id=resource_id, detail=detail,
                                 timestamp_epoch_ms=ts)
        self.diagnostics.append(d)
        if self._on_diagnostic is not None:
            self._on_diagnostic(d)
        return d

    def record_diagnostic(
        self, kind: str, resource_id: str, detail: str, timestamp_epoch_ms: int
    ) -> AssignmentDiagnostic:
        """Record a rejected external event without exposing private internals."""
        return self._diag(kind, resource_id, detail, timestamp_epoch_ms)

    # --- Queries ---

    def active_on(self, resource_id: str) -> Optional[ResourceAssignment]:
        return self._by_resource.get(resource_id)

    def active_for_subject(self, subject_id: str) -> Optional[ResourceAssignment]:
        """One of the subject's open assignments (arbitrary but stable pick
        via dict insertion order). For the common single-assignment case this
        is unambiguous; doubles run stages should prefer
        `active_for_subject_tag` or `all_active_for_subject`."""
        tag_resources = self._resource_of_subject.get(subject_id)
        if not tag_resources:
            return None
        rid = next(iter(tag_resources.values()))
        return self._by_resource.get(rid)

    def active_for_subject_tag(
        self, subject_id: str, tag_id: str
    ) -> Optional[ResourceAssignment]:
        """The open assignment held specifically by this member tag (doubles
        run stages, where a subject may hold two concurrent assignments)."""
        tag_resources = self._resource_of_subject.get(subject_id)
        if not tag_resources:
            return None
        rid = tag_resources.get(tag_id)
        return self._by_resource.get(rid) if rid else None

    def all_active_for_subject(self, subject_id: str) -> list[ResourceAssignment]:
        """Every open assignment currently held by the subject (0, 1, or --
        for a doubles team mid-run -- 2)."""
        tag_resources = self._resource_of_subject.get(subject_id, {})
        return [
            self._by_resource[rid] for rid in tag_resources.values()
            if rid in self._by_resource
        ]

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
        allow_concurrent: bool = False,
    ) -> Optional[ResourceAssignment]:
        """Claim a resource for a subject. Returns the assignment, or None on a
        rejected claim (resource already held by someone else -> diagnostic).

        `allow_concurrent` relaxes the per-subject invariant for a doubles
        team on a run stage (spec section 5): the subject may hold up to two
        open assignments, one per member tag, each on its own resource. The
        per-resource invariant (one open assignment per resource) is
        unaffected either way -- it is enforced above by the `existing`
        check."""
        existing = self._by_resource.get(resource_id)
        if existing is not None:
            if existing.subject_id == subject_id:
                # Idempotent re-claim; refresh the active member tag (relay
                # handoff) and drop any stale tag entry that pointed here.
                tag_resources = self._resource_of_subject.setdefault(subject_id, {})
                for tag, rid in list(tag_resources.items()):
                    if rid == resource_id and tag != active_tag_id:
                        del tag_resources[tag]
                tag_resources[active_tag_id] = resource_id
                existing.active_tag_id = active_tag_id
                return existing
            self._diag(
                "conflict", resource_id,
                f"claim by {subject_id} rejected; resource held by {existing.subject_id}",
                timestamp_epoch_ms,
            )
            return None

        tag_resources = self._resource_of_subject.get(subject_id, {})
        if allow_concurrent:
            # Only supersede this same tag's own prior resource; the other
            # member's concurrent assignment is left untouched.
            prior_rid = tag_resources.get(active_tag_id)
            if prior_rid and prior_rid != resource_id:
                self.close(prior_rid, AssignmentCloseReason.SUPERSEDED, timestamp_epoch_ms)
        else:
            # A subject can hold only one active assignment: supersede any prior one(s).
            for other_rid in list(tag_resources.values()):
                if other_rid != resource_id:
                    self.close(other_rid, AssignmentCloseReason.SUPERSEDED, timestamp_epoch_ms)

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
        self._resource_of_subject.setdefault(subject_id, {})[active_tag_id] = resource_id
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
        self._recently_closed[resource_id] = timestamp_epoch_ms
        assignment.status = "closed"
        assignment.close_reason = reason
        assignment.closed_at_epoch_ms = timestamp_epoch_ms
        tag_resources = self._resource_of_subject.get(assignment.subject_id)
        if tag_resources:
            for tag, rid in list(tag_resources.items()):
                if rid == resource_id:
                    del tag_resources[tag]
            if not tag_resources:
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
            # Trailing telemetry within 3 seconds of resource release is normal
            # (due to physical inertia of machines or network polling lag). Ignore silently.
            closed_at = self._recently_closed.get(resource_id)
            if closed_at is not None and (ts - closed_at < 3000):
                return Attribution(subject_id=None)

            d = self._diag(
                "anonymous_no_binding", resource_id,
                "anonymous telemetry with no active assignment",
                ts,
            )
            return Attribution(subject_id=None, diagnostic=d)
        return Attribution(subject_id=assignment.subject_id,
                           assignment_id=assignment.assignment_id)

    # --- Persistence (Phase 7) ---

    # Serialized diagnostics are capped -- the SQLite diagnostics table (see
    # hyrox_results_store.py) is the durable, unbounded audit log; the JSON
    # state snapshot only needs enough tail history to reconstruct the
    # in-memory last-20 API view after a restart.
    _SNAPSHOT_DIAGNOSTICS_LIMIT = 200

    @staticmethod
    def _assignment_to_dict(a: ResourceAssignment) -> dict:
        return {
            "assignment_id": a.assignment_id,
            "resource_id": a.resource_id,
            "subject_id": a.subject_id,
            "active_tag_id": a.active_tag_id,
            "stage": a.stage.value,
            "source": a.source.value,
            "assigned_at_epoch_ms": a.assigned_at_epoch_ms,
            "status": a.status,
            "close_reason": a.close_reason.value if a.close_reason else None,
            "closed_at_epoch_ms": a.closed_at_epoch_ms,
        }

    @staticmethod
    def _assignment_from_dict(a: dict) -> ResourceAssignment:
        return ResourceAssignment(
            assignment_id=a["assignment_id"],
            resource_id=a["resource_id"],
            subject_id=a["subject_id"],
            active_tag_id=a["active_tag_id"],
            stage=HyroxStage(a["stage"]),
            source=ClaimSource(a["source"]),
            assigned_at_epoch_ms=a["assigned_at_epoch_ms"],
            status=a["status"],
            close_reason=AssignmentCloseReason(a["close_reason"]) if a["close_reason"] else None,
            closed_at_epoch_ms=a["closed_at_epoch_ms"],
        )

    def to_dict(self) -> dict:
        return {
            "active": [self._assignment_to_dict(a) for a in self._by_resource.values()],
            "closed": [self._assignment_to_dict(a) for a in self._closed],
            "diagnostics": [
                {
                    "kind": d.kind, "resource_id": d.resource_id, "detail": d.detail,
                    "timestamp_epoch_ms": d.timestamp_epoch_ms,
                }
                for d in self.diagnostics[-self._SNAPSHOT_DIAGNOSTICS_LIMIT:]
            ],
            "counter": self._counter,
            "recently_closed": dict(self._recently_closed),
        }

    @classmethod
    def from_dict(
        cls, data: dict, on_diagnostic: Optional[Callable[[AssignmentDiagnostic], None]] = None
    ) -> "HyroxAssignmentStore":
        store = cls(on_diagnostic=on_diagnostic)
        for a in data.get("active", []):
            assignment = cls._assignment_from_dict(a)
            store._by_resource[assignment.resource_id] = assignment
            store._resource_of_subject.setdefault(assignment.subject_id, {})[
                assignment.active_tag_id
            ] = assignment.resource_id
        for a in data.get("closed", []):
            store._closed.append(cls._assignment_from_dict(a))
        store.diagnostics = [
            AssignmentDiagnostic(
                kind=d["kind"], resource_id=d["resource_id"], detail=d["detail"],
                timestamp_epoch_ms=d["timestamp_epoch_ms"],
            )
            for d in data.get("diagnostics", [])
        ]
        store._counter = data.get("counter", 0)
        store._recently_closed = dict(data.get("recently_closed", {}))
        return store
