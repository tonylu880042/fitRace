"""Hyrox service: the single orchestration entry point for the resource-aware
Hyrox backend.

Phase 6a. Replaces the station-based HyroxManager. Wires roster + sensor
registry + assignment store + progress tracker + course engine, and exposes the
operations the API and MQTT ingestion need: configure a venue, register
subjects, start, ingest telemetry (with training-mode dynamic claim and
competition-mode operator assignment), abandon, force-complete, and project a
clean state.

Everything is in-memory; persistence is Phase 7.
"""

import time
from typing import Optional

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_venue import (
    HyroxCourseProfile,
    HyroxVenueConfig,
    default_hyrox_course_profile,
    validate_venue_config,
)
from hub_server.usecases.hyrox_roster import HyroxRoster
from hub_server.usecases.hyrox_sensor_registry import HyroxSensorRegistry
from hub_server.usecases.hyrox_assignment_store import ClaimSource, HyroxAssignmentStore
from hub_server.usecases.hyrox_progress import HyroxProgressTracker
from hub_server.usecases.hyrox_course_engine import HyroxCourseEngine


def _now_ms() -> int:
    return int(time.time() * 1000)


class HyroxService:
    def __init__(self, profile: Optional[HyroxCourseProfile] = None):
        self._profile = profile or default_hyrox_course_profile()
        self._stage_def = {s.stage: s for s in self._profile.stages}
        self._mode = "training"  # training (dynamic claim) | competition (operator assign)
        self._venue: Optional[HyroxVenueConfig] = None
        self._registry: Optional[HyroxSensorRegistry] = None
        self._roster = HyroxRoster()
        self._store = HyroxAssignmentStore()
        self._tracker = HyroxProgressTracker()
        self._engine: Optional[HyroxCourseEngine] = None
        self._is_active = False
        self._resource_heartbeats: dict[str, int] = {}
        self._queues: dict[str, tuple[str, int]] = {}

    # --- Configuration ---

    def configure_venue(self, venue: HyroxVenueConfig, mode: str = "training"):
        # Structural validation is a hard gate; full course readiness (every
        # stage has a resource) is a separate start-time / UI concern.
        errors = validate_venue_config(venue)
        if errors:
            raise ValueError("; ".join(errors))
        self._venue = venue
        self._mode = mode
        self._registry = HyroxSensorRegistry(venue)
        # A new venue resets the race.
        self._roster = HyroxRoster()
        self._store = HyroxAssignmentStore()
        self._tracker = HyroxProgressTracker()
        self._engine = HyroxCourseEngine(self._profile, self._store, self._tracker)
        self._is_active = False
        self._resource_heartbeats = {}
        self._queues = {}

    @property
    def is_configured(self) -> bool:
        return self._registry is not None and self._engine is not None

    # --- Roster and race control ---

    def register(self, subject_id: str, division: str, member_tag: str, member_name: str):
        if self._engine is None:
            raise RuntimeError("Configure a venue before registering athletes")
        self._roster.add_member(subject_id, division, member_tag, member_name)
        if self._engine.state_of(subject_id) is None:
            state = self._engine.register_subject(subject_id)
            if self._is_active:
                state.stage_start_ms[state.current_stage] = _now_ms()

    def start(self):
        if self._engine is None:
            raise RuntimeError("Configure a venue before starting")
        self._is_active = True
        self._engine.start(_now_ms())

    # --- Telemetry ingestion ---

    def ingest_rfid(self, node_id: str, antenna_id: str, tag_id: str,
                    timestamp_ms: Optional[int] = None):
        if not (self._is_active and self._registry and self._engine):
            return
        ts = timestamp_ms if timestamp_ms is not None else _now_ms()
        event = self._registry.normalize_rfid(node_id, antenna_id, tag_id, ts)
        if event is None:
            return  # unknown sensor
        self._maybe_dynamic_claim(event, tag_id, ts)
        self._resource_heartbeats[event.resource_id] = ts
        self._engine.process(event, ts)

    def ingest_node(self, node_id: str, metrics: Optional[dict] = None,
                    timestamp_ms: Optional[int] = None):
        """FTMS distance and rep-counter events -- anonymous, attributed via the
        active assignment on the resource (bound earlier by an entry-gate read
        or an operator assignment)."""
        if not (self._is_active and self._registry and self._engine):
            return
        ts = timestamp_ms if timestamp_ms is not None else _now_ms()
        event = self._registry.normalize_node(node_id, ts, metrics=metrics)
        if event is None:
            return
        self._resource_heartbeats[event.resource_id] = ts
        self._engine.process(event, ts)

    def _maybe_dynamic_claim(self, event, tag_id: str, ts: int):
        # Training mode only: the first in-sequence read on a free resource
        # claims it. Competition mode requires an explicit operator assignment.
        if self._mode != "training":
            return
        subject_id = self._roster.subject_for_tag(tag_id)
        if subject_id is None:
            return  # unregistered tag
        if self._store.active_on(event.resource_id) is not None:
            return  # occupied; claim() would reject/idempotent-noop anyway
        if self._engine.allows(subject_id, event.resource_group_id):
            self._store.claim(
                event.resource_id, subject_id, tag_id,
                self._engine.current_stage_of(subject_id),
                ClaimSource.DYNAMIC_CLAIM, ts,
            )

    # --- Operator actions ---

    def assign(self, subject_id: str, resource_id: str,
               timestamp_ms: Optional[int] = None) -> bool:
        """Competition-mode explicit assignment of a resource to a subject."""
        entry = self._roster.get(subject_id)
        if entry is None or not entry.member_tags or self._engine is None:
            return False
        ts = timestamp_ms if timestamp_ms is not None else _now_ms()
        active_tag = entry.member_tags[0]  # relay handoff refinement: per-member later
        stage = self._engine.current_stage_of(subject_id)
        assignment = self._store.claim(
            resource_id, subject_id, active_tag, stage, ClaimSource.OPERATOR, ts
        )
        return assignment is not None

    def abandon(self, subject_id: str, timestamp_ms: Optional[int] = None):
        if self._engine is None:
            return
        self._engine.abandon(subject_id, timestamp_ms or _now_ms())

    def abandon_by_tag(self, tag_id: str, timestamp_ms: Optional[int] = None):
        """Abandon button read: resolve the member tag to its subject."""
        subject_id = self._roster.subject_for_tag(tag_id)
        if subject_id is not None:
            self.abandon(subject_id, timestamp_ms)

    def complete_stage(self, subject_id: str, timestamp_ms: Optional[int] = None):
        if self._engine is None:
            return
        self._engine.force_complete_stage(subject_id, timestamp_ms or _now_ms())

    # --- State projection (clean, resource-aware shape) ---

    def _elapsed_ms(self, state) -> int:
        if state is None or HyroxStage.RUN_1 not in state.stage_start_ms:
            return 0
        start = state.stage_start_ms[HyroxStage.RUN_1]
        if state.status == "racing":
            return max(0, _now_ms() - start)
        # finished / abandoned: freeze at the last recorded stage-end.
        if state.stage_end_ms:
            return max(0, max(state.stage_end_ms.values()) - start)
        return 0

    def get_state(self) -> dict:
        subjects = []
        for entry in self._roster.all():
            state = self._engine.state_of(entry.subject_id) if self._engine else None
            stage = state.current_stage if state else HyroxStage.RUN_1
            stage_def = self._stage_def.get(stage)
            value = 0.0
            target = 0.0
            if stage_def is not None:
                value = self._tracker.value_of(entry.subject_id, stage, stage_def.target_type)
                target = stage_def.target_value
            assignment = self._store.active_for_subject(entry.subject_id)
            subjects.append({
                "subject_id": entry.subject_id,
                "division": entry.division,
                "members": entry.member_names,
                "current_stage": stage.value,
                "status": state.status if state else "racing",
                "assigned_resource": assignment.resource_id if assignment else None,
                "progress_value": value,
                "progress_target": target,
                "progress_type": stage_def.target_type.value if stage_def else None,
                "elapsed_ms": self._elapsed_ms(state),
            })
        resource_ids = [
            u.resource_id
            for g in (self._venue.resource_groups if self._venue else [])
            for u in g.units
        ]
        return {
            "is_active": self._is_active,
            "mode": self._mode,
            "venue_configured": self.is_configured,
            "subjects": subjects,
            "resources": self._store.availability(resource_ids),
            "diagnostics": [
                {"kind": d.kind, "resource_id": d.resource_id, "detail": d.detail}
                for d in self._store.diagnostics[-20:]
            ],
        }

    def get_god_view_state(self) -> dict:
        if self._venue is None:
            return {
                "is_active": self._is_active,
                "mode": self._mode,
                "venue_configured": False,
                "resource_groups": [],
                "resources": {},
                "diagnostics": [],
            }

        resources_detail = {}
        for g in self._venue.resource_groups:
            for u in g.units:
                assignment = self._store.active_on(u.resource_id)
                status = "free"
                subject_id = None
                subject_name = None
                current_stage = None
                progress_value = 0.0
                progress_target = 0.0
                progress_type = None

                if assignment is not None:
                    status = "in_use"
                    subject_id = assignment.subject_id
                    entry = self._roster.get(subject_id)
                    if entry is not None:
                        if entry.division != "individual":
                            subject_name = subject_id
                        else:
                            subject_name = entry.member_names[0] if entry.member_names else subject_id
                    else:
                        subject_name = subject_id

                    # Get athlete progress details
                    state = self._engine.state_of(subject_id) if self._engine else None
                    if state:
                        current_stage = state.current_stage.value
                        stage_def = self._stage_def.get(state.current_stage)
                        if stage_def:
                            progress_value = self._tracker.value_of(subject_id, state.current_stage, stage_def.target_type)
                            progress_target = stage_def.target_value
                            progress_type = stage_def.target_type.value

                resources_detail[u.resource_id] = {
                    "status": status,
                    "subject_id": subject_id,
                    "subject_name": subject_name,
                    "current_stage": current_stage,
                    "progress_value": progress_value,
                    "progress_target": progress_target,
                    "progress_type": progress_type,
                    "last_heartbeat_epoch_ms": self._resource_heartbeats.get(u.resource_id),
                }

        return {
            "is_active": self._is_active,
            "mode": self._mode,
            "venue_configured": True,
            "venue_id": self._venue.venue_id,
            "resource_groups": [
                {
                    "group_id": g.group_id,
                    "resource_type": g.resource_type,
                    "stage_candidates": [s.value for s in g.stage_candidates],
                    "units": [
                        {
                            "resource_id": u.resource_id,
                            "display_name": u.display_name,
                            "sensor_class": u.sensor_class.value,
                            "node_id": u.node_id,
                        }
                        for u in g.units
                    ]
                }
                for g in self._venue.resource_groups
            ],
            "resources": resources_detail,
            "diagnostics": [
                {
                    "kind": d.kind, "resource_id": d.resource_id, "detail": d.detail,
                    "timestamp": d.timestamp_epoch_ms
                }
                for d in self._store.diagnostics[-20:]
            ],
            "queues": [
                {
                    "subject_id": sid,
                    "subject_name": (
                        self._roster.get(sid).member_names[0]
                        if self._roster.get(sid) and self._roster.get(sid).member_names
                        else sid
                    ),
                    "group_id": gid,
                    "wait_start_epoch_ms": ts,
                }
                for sid, (gid, ts) in self._queues.items()
            ],
        }

    def set_queue(self, subject_id: str, group_id: str, wait_start_epoch_ms: Optional[int]):
        if wait_start_epoch_ms is None:
            self._queues.pop(subject_id, None)
        else:
            self._queues[subject_id] = (group_id, wait_start_epoch_ms)
