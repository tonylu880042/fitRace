"""Hyrox service: the single orchestration entry point for the resource-aware
Hyrox backend.

Phase 6a. Replaces the station-based HyroxManager. Wires roster + sensor
registry + assignment store + progress tracker + course engine, and exposes the
operations the API and MQTT ingestion need: configure a venue, register
subjects, start, ingest telemetry (with training-mode dynamic claim and
competition-mode operator assignment), abandon, force-complete, and project a
clean state.

Phase 7 (docs/hyrox_system_architecture_plan.md section 15, Phase 7): the whole
race state -- venue, roster, assignments, subject states, and progress -- can
be snapshotted to a dict and restored from one, and that snapshot is written to
a JSON file on disk after state-changing operations so the Hub survives a
restart mid-race. Finalized results are already durable in HyroxResultsStore;
this covers the in-flight state that lived only in memory before.
"""

import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_results import HyroxAthleteResult, HyroxRaceResults
from hub_server.usecases.hyrox_results_store import HyroxResultsStore, build_athlete_result
from hub_server.domain.hyrox_venue import (
    HyroxCourseProfile,
    HyroxSensorClass,
    HyroxVenueConfig,
    default_hyrox_course_profile,
    validate_venue_config,
)
from hub_server.usecases.hyrox_roster import HyroxRoster
from hub_server.usecases.hyrox_sensor_registry import HyroxSensorRegistry
from hub_server.usecases.hyrox_assignment_store import ClaimSource, HyroxAssignmentStore
from hub_server.usecases.hyrox_progress import HyroxProgressTracker
from hub_server.usecases.hyrox_course_engine import HyroxCourseEngine

DEFAULT_STATE_PATH = "data/hyrox_state.json"
# High-frequency telemetry (FTMS/RFID) is throttled to at most one snapshot
# write per second; every other state-changing operation writes immediately.
TELEMETRY_PERSIST_INTERVAL_MS = 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


class HyroxAssignmentError(ValueError):
    """A rejected operator assignment with a stable API-facing reason code."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class HyroxService:
    def __init__(self, profile: Optional[HyroxCourseProfile] = None,
                 results_store: Optional[HyroxResultsStore] = None):
        self._profile = profile or default_hyrox_course_profile()
        self._stage_def = {s.stage: s for s in self._profile.stages}
        self._stage_order = [s.stage for s in self._profile.stages]
        self._targets = {s.stage: (s.target_type.value, s.target_value)
                         for s in self._profile.stages}
        self._mode = "training"  # training (dynamic claim) | competition (operator assign)
        self._venue: Optional[HyroxVenueConfig] = None
        self._registry: Optional[HyroxSensorRegistry] = None
        self._roster = HyroxRoster()
        self._store = HyroxAssignmentStore(on_diagnostic=self._on_diagnostic)
        self._tracker = HyroxProgressTracker()
        self._engine: Optional[HyroxCourseEngine] = None
        self._is_active = False
        self._resource_heartbeats: dict[str, int] = {}
        self._latest_ftms_distance: dict[str, float] = {}
        self._queues: dict[str, tuple[str, int]] = {}
        # Results persistence (attached at runtime; None in unit tests)
        self._results = results_store
        self._race_id: Optional[str] = None
        self._result_tokens: dict[str, str] = {}   # subject_id -> token
        self._finalized: set[str] = set()
        # Race-state snapshot persistence (Phase 7). None means disabled --
        # unit tests construct a bare HyroxService and never touch disk.
        # Enabled via load_snapshot() at hub startup.
        self._state_path: Optional[str] = None
        self._last_persist_ms = 0
        self.recovered = False

    def attach_results_store(self, store: HyroxResultsStore):
        self._results = store

    def _on_diagnostic(self, d) -> None:
        """Durable audit sink shared by the assignment store and course
        engine. Fires on every diagnostic; a no-op until a results store is
        attached (see HyroxResultsStore.record_diagnostic)."""
        if self._results is None:
            return
        self._results.record_diagnostic(
            self._race_id, d.kind, d.resource_id, d.detail, d.timestamp_epoch_ms,
        )

    # --- Configuration ---

    def configure_venue(self, venue: HyroxVenueConfig, mode: str = "training",
                        race_id: Optional[str] = None):
        # Structural validation is a hard gate; full course readiness (every
        # stage has a resource) is a separate start-time / UI concern.
        errors = validate_venue_config(venue)
        if errors:
            raise ValueError("; ".join(errors))
        self._venue = venue
        self._mode = mode
        self._race_id = race_id or f"race-{_now_ms()}"
        self._registry = HyroxSensorRegistry(venue)
        # A new venue resets the race.
        self._roster = HyroxRoster()
        self._store = HyroxAssignmentStore(on_diagnostic=self._on_diagnostic)
        self._tracker = HyroxProgressTracker()
        self._engine = HyroxCourseEngine(
            self._profile, self._store, self._tracker, on_diagnostic=self._on_diagnostic
        )
        self._is_active = False
        self._resource_heartbeats = {}
        self._latest_ftms_distance = {}
        self._queues = {}
        self._result_tokens = {}
        self._finalized = set()
        self.recovered = False
        # A new race overwrites any stale snapshot on disk rather than leaving
        # the previous race's mid-run state behind.
        self._persist()

    @property
    def is_configured(self) -> bool:
        return self._registry is not None and self._engine is not None

    def venue_snapshot(self) -> dict:
        """Read-only view of the current venue config for the admin editor."""
        return {
            "configured": self.is_configured,
            "mode": self._mode if self.is_configured else None,
            "venue": self._venue.model_dump() if self._venue is not None else None,
        }

    # --- Roster and race control ---

    def register(self, subject_id: str, division: str, member_tag: str,
                 member_name: str) -> str:
        """Register an athlete/team member. Returns the subject's result token
        (issued once per subject, for post-race result retrieval)."""
        if self._engine is None:
            raise RuntimeError("Configure a venue before registering athletes")
        self._roster.add_member(subject_id, division, member_tag, member_name)
        if self._engine.state_of(subject_id) is None:
            # The clock starts on the athlete's first activity, not at
            # registration -- see HyroxCourseEngine._ensure_started.
            self._engine.register_subject(subject_id)
        token = self._result_tokens.setdefault(subject_id, secrets.token_urlsafe(12))
        self._persist()
        return token

    def token_for(self, subject_id: str) -> Optional[str]:
        return self._result_tokens.get(subject_id)

    def start(self):
        if self._engine is None:
            raise RuntimeError("Configure a venue before starting")
        self._is_active = True
        self._engine.start(_now_ms())
        if self._results is not None and self._venue is not None:
            self._results.create_race(
                self._race_id, self._venue.venue_id, self._mode,
                self._profile.course_profile_id, _now_ms(),
            )
        self._persist()

    # --- Telemetry ingestion ---

    def ingest_rfid(self, node_id: str, antenna_id: str, tag_id: str,
                    timestamp_ms: Optional[int] = None):
        if not (self._registry and self._engine):
            return
        ts = timestamp_ms if timestamp_ms is not None else _now_ms()
        event = self._registry.normalize_rfid(node_id, antenna_id, tag_id, ts)
        if event is None:
            # Abandon zones arrive on the regular RFID topic; route to the dedicated handler.
            if self._registry.resolve_abandon(node_id, antenna_id) is not None:
                self.ingest_abandon(node_id, antenna_id, tag_id, timestamp_ms)
            return
        if not self._is_active:
            return
        self._maybe_dynamic_claim(event, tag_id, ts)
        self._resource_heartbeats[event.resource_id] = ts
        self._engine.process(event, ts)
        self._finalize_done()
        self._persist(throttled=True)

    def ingest_node(self, node_id: str, metrics: Optional[dict] = None,
                    timestamp_ms: Optional[int] = None):
        """FTMS distance and rep-counter events -- anonymous, attributed via the
        active assignment on the resource (bound earlier by an entry-gate read
        or an operator assignment)."""
        if not (self._registry and self._engine):
            return
        ts = timestamp_ms if timestamp_ms is not None else _now_ms()
        event = self._registry.normalize_node(node_id, ts, metrics=metrics)
        if event is None:
            return
        distance_m = (metrics or {}).get("distance_m")
        if event.sensor_class == HyroxSensorClass.FTMS_MACHINE:
            if distance_m is not None:
                self._latest_ftms_distance[event.resource_id] = float(distance_m)
                self._persist(throttled=True)
        elif distance_m is not None:
            # Generic Edge telemetry also reaches this method. A distance field
            # from a rep-counter node is not a discrete rep event.
            return
        if not self._is_active:
            return
        self._resource_heartbeats[event.resource_id] = ts
        self._engine.process(event, ts)
        self._finalize_done()
        self._persist(throttled=True)

    def ingest_abandon(
        self,
        node_id: str,
        antenna_id: str,
        tag_id: str,
        timestamp_ms: Optional[int] = None,
    ):
        """Process a resource-addressed abandon button event safely."""
        if self._registry is None or self._engine is None:
            return
        ts = timestamp_ms if timestamp_ms is not None else _now_ms()
        event = self._registry.normalize_abandon(node_id, antenna_id, tag_id, ts)
        if event is None:
            self._store.record_diagnostic(
                "abandon_unknown_sensor",
                f"{node_id}/{antenna_id}",
                "abandon rejected because the sensor is not configured",
                ts,
            )
            return
        if not self._is_active:
            self._store.record_diagnostic(
                "abandon_inactive",
                event.resource_id,
                "abandon rejected because the race is not active",
                ts,
            )
            return
        assignment = self._store.active_on(event.resource_id)
        if assignment is None:
            self._store.record_diagnostic(
                "abandon_unassigned",
                event.resource_id,
                "abandon rejected because the resource is unassigned",
                ts,
            )
            return
        if assignment.active_tag_id != tag_id:
            self._store.record_diagnostic(
                "abandon_tag_mismatch",
                event.resource_id,
                f"abandon tag {tag_id} does not match the active assignment",
                ts,
            )
            return
        self.abandon(assignment.subject_id, ts)

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
            assignment = self._store.claim(
                event.resource_id, subject_id, tag_id,
                self._engine.current_stage_of(subject_id),
                ClaimSource.DYNAMIC_CLAIM, ts,
            )
            if assignment is not None:
                self._seed_ftms_baseline(
                    assignment.subject_id, assignment.stage, assignment.resource_id
                )

    def _seed_ftms_baseline(
        self, subject_id: str, stage: HyroxStage, resource_id: str
    ) -> None:
        raw_distance_m = self._latest_ftms_distance.get(resource_id)
        if raw_distance_m is not None:
            self._tracker.seed_distance_baseline(subject_id, stage, raw_distance_m)

    # --- Operator actions ---

    def assign(
        self,
        subject_id: str,
        resource_id: str,
        timestamp_ms: Optional[int] = None,
        active_tag_id: Optional[str] = None,
    ) -> bool:
        """Competition-mode explicit assignment of a resource to a subject."""
        if self._engine is None or self._venue is None:
            raise HyroxAssignmentError(
                "not_configured", "Load a venue config before assigning resources"
            )
        if self._mode != "competition":
            raise HyroxAssignmentError(
                "wrong_mode", "Operator assignment is only available in competition mode"
            )

        entry = self._roster.get(subject_id)
        if entry is None:
            raise HyroxAssignmentError(
                "unknown_subject", f"Subject {subject_id} not found"
            )
        state = self._engine.state_of(subject_id)
        if state is None:
            raise HyroxAssignmentError(
                "unknown_subject", f"Subject {subject_id} not found"
            )
        if state.status != "racing":
            raise HyroxAssignmentError(
                "not_racing", f"Subject {subject_id} is not racing"
            )

        resource_group_id = next(
            (
                group.group_id
                for group in self._venue.resource_groups
                if any(unit.resource_id == resource_id for unit in group.units)
            ),
            None,
        )
        if resource_group_id is None:
            raise HyroxAssignmentError(
                "unknown_resource", f"Resource {resource_id} not found"
            )
        stage = state.current_stage
        stage_definition = self._engine.stage_definition(stage)
        if (
            stage_definition is None
            or resource_group_id not in stage_definition.allowed_resource_groups
        ):
            raise HyroxAssignmentError(
                "wrong_stage",
                f"Resource {resource_id} is not allowed for stage {stage.value}",
            )

        if entry.division == "individual":
            if len(entry.member_tags) != 1:
                raise HyroxAssignmentError(
                    "invalid_tag",
                    f"Individual subject {subject_id} must have exactly one registered tag",
                )
            selected_tag = (
                active_tag_id if active_tag_id is not None else entry.member_tags[0]
            )
        else:
            if active_tag_id is None:
                raise HyroxAssignmentError(
                    "active_tag_required",
                    "active_tag_id is required for doubles and relay assignments",
                )
            selected_tag = active_tag_id
        if selected_tag not in entry.member_tags:
            raise HyroxAssignmentError(
                "invalid_tag",
                f"Tag {selected_tag} does not belong to subject {subject_id}",
            )

        existing = self._store.active_on(resource_id)
        if existing is not None and existing.subject_id != subject_id:
            raise HyroxAssignmentError(
                "occupied", f"Resource {resource_id} is occupied"
            )

        ts = timestamp_ms if timestamp_ms is not None else _now_ms()
        assignment = self._store.claim(
            resource_id, subject_id, selected_tag, stage, ClaimSource.OPERATOR, ts
        )
        if assignment is None:
            raise HyroxAssignmentError(
                "occupied", f"Resource {resource_id} is occupied"
            )
        self._seed_ftms_baseline(
            assignment.subject_id, assignment.stage, assignment.resource_id
        )
        self._persist()
        return True

    def abandon(self, subject_id: str, timestamp_ms: Optional[int] = None):
        if self._engine is None:
            return
        ts = timestamp_ms if timestamp_ms is not None else _now_ms()
        self._engine.abandon(subject_id, ts)
        self._finalize_done()
        self._persist()

    def complete_stage(self, subject_id: str, timestamp_ms: Optional[int] = None):
        if self._engine is None:
            return
        ts = timestamp_ms if timestamp_ms is not None else _now_ms()
        self._engine.force_complete_stage(subject_id, ts)
        self._finalize_done()
        self._persist()

    # --- Results finalization and retrieval ---

    @property
    def race_id(self) -> Optional[str]:
        return self._race_id

    def _finalize_done(self):
        """Persist the result of any subject that has newly reached a terminal
        state (finished / abandoned). Idempotent -- each subject once."""
        if self._results is None or self._engine is None:
            return
        for entry in self._roster.all():
            sid = entry.subject_id
            if sid in self._finalized:
                continue
            state = self._engine.state_of(sid)
            if state is None or state.status not in ("finished", "abandoned"):
                continue
            token = self._result_tokens.get(sid)
            if token is None:
                continue
            name = (sid if entry.division != "individual"
                    else (entry.member_names[0] if entry.member_names else sid))
            result = build_athlete_result(
                race_id=self._race_id, result_token=token, subject_id=sid,
                display_name=name, division=entry.division, members=entry.member_names,
                state=state, stage_order=self._stage_order, targets=self._targets,
                progress_of=lambda stg: self._tracker.value_of(
                    sid, stg, self._stage_def[stg].target_type),
            )
            self._results.finalize_athlete(result)
            self._finalized.add(sid)

    def result_by_token(self, token: str) -> Optional[HyroxAthleteResult]:
        return self._results.get_by_token(token) if self._results else None

    def race_results(self, race_id: str) -> Optional[HyroxRaceResults]:
        return self._results.get_race(race_id) if self._results else None

    def export_csv(self, race_id: str) -> str:
        return self._results.export_csv(race_id) if self._results else ""

    def list_races(self) -> list[dict]:
        return self._results.list_races() if self._results else []

    def diagnostics_for(self, race_id: str) -> list[dict]:
        return self._results.get_diagnostics(race_id) if self._results else []

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
            "recovered": self.recovered,
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

    # --- Snapshot / restore (Phase 7) ---

    def snapshot(self) -> dict:
        """The full in-flight race state, JSON-safe. Finalized results are
        already durable in HyroxResultsStore and are not duplicated here."""
        return {
            "version": 1,
            "venue": self._venue.model_dump(mode="json") if self._venue else None,
            "mode": self._mode,
            "race_id": self._race_id,
            "is_active": self._is_active,
            "roster": self._roster.to_dict(),
            "result_tokens": dict(self._result_tokens),
            "assignments": self._store.to_dict(),
            "engine": self._engine.to_dict() if self._engine else None,
            "progress": self._tracker.to_dict(),
            "queues": {sid: [gid, ts] for sid, (gid, ts) in self._queues.items()},
            "finalized": sorted(self._finalized),
            "latest_ftms_distance": dict(self._latest_ftms_distance),
        }

    def restore(self, snapshot: dict) -> None:
        """Rebuild in-flight race state from a snapshot() dict. Finalized
        results are not part of the snapshot -- they are read back from
        HyroxResultsStore, unaffected by this call."""
        venue_data = snapshot.get("venue")
        self._venue = HyroxVenueConfig.model_validate(venue_data) if venue_data else None
        self._registry = HyroxSensorRegistry(self._venue) if self._venue else None
        self._mode = snapshot.get("mode", "training")
        self._race_id = snapshot.get("race_id")
        self._is_active = snapshot.get("is_active", False)
        self._roster = HyroxRoster.from_dict(snapshot.get("roster", {}))
        self._result_tokens = dict(snapshot.get("result_tokens", {}))
        self._store = HyroxAssignmentStore.from_dict(
            snapshot.get("assignments", {}), on_diagnostic=self._on_diagnostic
        )
        self._tracker = HyroxProgressTracker()
        self._tracker.restore(snapshot.get("progress", {}))
        if self._venue is not None:
            self._engine = HyroxCourseEngine(
                self._profile, self._store, self._tracker, on_diagnostic=self._on_diagnostic
            )
            engine_data = snapshot.get("engine")
            if engine_data:
                self._engine.restore(engine_data)
        else:
            self._engine = None
        self._queues = {
            sid: (gid_ts[0], gid_ts[1]) for sid, gid_ts in snapshot.get("queues", {}).items()
        }
        self._finalized = set(snapshot.get("finalized", []))
        self._latest_ftms_distance = {
            k: float(v) for k, v in snapshot.get("latest_ftms_distance", {}).items()
        }
        self._resource_heartbeats = {}

    # --- Disk persistence (Phase 7) ---

    def load_snapshot(self, path: Optional[str] = None) -> bool:
        """Enable snapshot persistence to `path` (default DEFAULT_STATE_PATH,
        overridable via FITRACE_HYROX_STATE_PATH) and, if a snapshot already
        exists there, restore it. Call once at hub startup, next to
        attach_results_store. Returns True when state was recovered."""
        self._state_path = path or os.getenv("FITRACE_HYROX_STATE_PATH", DEFAULT_STATE_PATH)
        p = Path(self._state_path)
        if not p.exists():
            self.recovered = False
            return False
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.recovered = False
            return False
        self.restore(data)
        self.recovered = True
        return True

    def _persist(self, throttled: bool = False) -> None:
        """Write the current snapshot to disk. A no-op until load_snapshot()
        has enabled a state path (unit tests never touch disk). When
        throttled, high-frequency telemetry callers skip the write if one
        already happened within TELEMETRY_PERSIST_INTERVAL_MS."""
        if self._state_path is None:
            return
        now = _now_ms()
        if throttled and (now - self._last_persist_ms) < TELEMETRY_PERSIST_INTERVAL_MS:
            return
        path = Path(self._state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(json.dumps(self.snapshot()), encoding="utf-8")
        os.replace(tmp_path, path)
        self._last_persist_ms = now
