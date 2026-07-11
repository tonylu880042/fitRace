"""Phase 7 persistence and recovery tests (architecture plan section 15,
Phase 7): snapshot/restore round trips for each layer, and HyroxService's
disk persistence (throttling, atomic write, load-on-startup recovery)."""

import json

import pytest

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_venue import (
    HyroxEndpointSensor,
    HyroxResourceGroup,
    HyroxResourceUnit,
    HyroxSensorClass,
    HyroxTargetType,
    HyroxVenueConfig,
    default_hyrox_course_profile,
)
from hub_server.usecases.hyrox_assignment_store import (
    AssignmentCloseReason,
    ClaimSource,
    HyroxAssignmentStore,
)
from hub_server.usecases.hyrox_course_engine import HyroxCourseEngine
from hub_server.usecases.hyrox_progress import HyroxProgressTracker
from hub_server.usecases.hyrox_results_store import HyroxResultsStore
from hub_server.usecases.hyrox_roster import HyroxRoster
from hub_server.usecases.hyrox_sensor_registry import HyroxTelemetryEvent
from hub_server.usecases.hyrox_service import HyroxService


def _venue():
    return HyroxVenueConfig(
        venue_id="hq",
        course_profile_id="hyrox_standard_2026",
        resource_groups=[
            HyroxResourceGroup(
                group_id="run_treadmills", resource_type="ftms_machine_pool",
                stage_candidates=[],
                units=[HyroxResourceUnit(
                    resource_id="treadmill-01", display_name="TM1",
                    sensor_class=HyroxSensorClass.FTMS_MACHINE, node_id="edge-tm-01",
                    entry_gate=HyroxEndpointSensor(node_id="rfid-tm-01", antenna_id="T1_GATE"),
                )],
            ),
            HyroxResourceGroup(
                group_id="shared_turf_lanes", resource_type="rfid_lane_pool",
                stage_candidates=[],
                units=[HyroxResourceUnit(
                    resource_id="turf-lane-1", display_name="Lane 1",
                    sensor_class=HyroxSensorClass.RFID_ENDPOINT_PAIR,
                    start_endpoint=HyroxEndpointSensor(node_id="rfid-01", antenna_id="L1_START"),
                    finish_endpoint=HyroxEndpointSensor(node_id="rfid-01", antenna_id="L1_FINISH"),
                )],
            ),
        ],
    )


def _ftms_event(resource_id, distance, ts=0):
    return HyroxTelemetryEvent(
        sensor_class=HyroxSensorClass.FTMS_MACHINE,
        resource_group_id="run_treadmills",
        resource_id=resource_id,
        tag_id=None,
        timestamp_epoch_ms=ts,
        metrics={"distance_m": distance},
    )


# --- Layer-level round trips ---

def test_roster_to_dict_from_dict_round_trip():
    roster = HyroxRoster()
    roster.add_member("duo", "doubles", "TAG_ONE", "One")
    roster.add_member("duo", "doubles", "TAG_TWO", "Two")
    roster.add_member("TAG_BELLA", "individual", "TAG_BELLA", "Bella")

    restored = HyroxRoster.from_dict(roster.to_dict())

    assert restored.subject_for_tag("TAG_ONE") == "duo"
    assert restored.subject_for_tag("TAG_TWO") == "duo"
    assert restored.get("duo").member_names == ["One", "Two"]
    assert restored.subject_for_tag("TAG_BELLA") == "TAG_BELLA"
    # Duplicate-tag guard still works against restored state.
    with pytest.raises(ValueError):
        restored.add_member("someone-else", "individual", "TAG_ONE", "X")


def test_assignment_store_to_dict_from_dict_round_trip():
    store = HyroxAssignmentStore()
    store.claim("treadmill-01", "alex", "TAG_ALEX", HyroxStage.RUN_1,
                ClaimSource.DYNAMIC_CLAIM, 10)
    store.claim("turf-lane-1", "bella", "TAG_BELLA", HyroxStage.SLED_PUSH,
                ClaimSource.OPERATOR, 20)
    store.close("turf-lane-1", AssignmentCloseReason.COMPLETED, 30)
    store.record_diagnostic("conflict", "treadmill-02", "already occupied", 40)

    restored = HyroxAssignmentStore.from_dict(store.to_dict())

    active = restored.active_on("treadmill-01")
    assert active is not None
    assert active.subject_id == "alex" and active.source == ClaimSource.DYNAMIC_CLAIM
    assert restored.active_on("turf-lane-1") is None
    assert len(restored._closed) == 1
    assert restored._closed[0].close_reason == AssignmentCloseReason.COMPLETED
    assert any(d.kind == "conflict" for d in restored.diagnostics)
    # Counter continuity: the id sequence keeps going, it does not restart and
    # collide with a previously issued assignment_id.
    assert restored._counter == store._counter == 2
    assert restored._next_id() == "asg-3"


def test_progress_tracker_to_dict_restore_round_trip():
    tracker = HyroxProgressTracker()
    tracker.seed_distance_baseline("alex", HyroxStage.RUN_1, 100.0)
    tracker.apply(
        _ftms_event("treadmill-01", 400.0), "alex", HyroxStage.RUN_1,
        HyroxTargetType.DISTANCE_M, 1000.0,
    )
    tracker.force_complete("bella", HyroxStage.SLED_PUSH, 4.0)

    restored = HyroxProgressTracker()
    restored.restore(tracker.to_dict())

    assert restored.value_of("alex", HyroxStage.RUN_1, HyroxTargetType.DISTANCE_M) == 300.0
    assert restored._distance[("alex", HyroxStage.RUN_1)].last_raw == 400.0
    assert restored._is_forced("bella", HyroxStage.SLED_PUSH)


def test_course_engine_to_dict_restore_round_trip():
    profile = default_hyrox_course_profile()
    store = HyroxAssignmentStore()
    tracker = HyroxProgressTracker()
    engine = HyroxCourseEngine(profile, store, tracker)
    engine.register_subject("alex")
    engine._ensure_started(engine.state_of("alex"), 100)
    engine.state_of("alex").stage_arrived_ms[HyroxStage.RUN_1] = 105
    engine.state_of("alex").stage_resource[HyroxStage.RUN_1] = "treadmill-01"
    engine._diag("out_of_sequence", "alex", "row-1", "too early", 200)

    fresh_engine = HyroxCourseEngine(profile, store, tracker)
    fresh_engine.restore(engine.to_dict())

    state = fresh_engine.state_of("alex")
    assert state.current_stage == HyroxStage.RUN_1
    assert state.stage_start_ms[HyroxStage.RUN_1] == 100
    assert state.stage_arrived_ms[HyroxStage.RUN_1] == 105
    assert state.stage_resource[HyroxStage.RUN_1] == "treadmill-01"
    assert fresh_engine.diagnostics[0].kind == "out_of_sequence"


# --- Service-level snapshot / restore ---

def test_snapshot_restore_preserves_mid_race_state_and_ftms_continuity():
    svc = HyroxService()
    svc.configure_venue(_venue(), mode="training", race_id="race-snap")
    token = svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()

    # Dynamic claim binds the treadmill, then partial FTMS progress.
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX", timestamp_ms=1)
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 0}, timestamp_ms=2)    # baseline
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 400}, timestamp_ms=3)  # partial

    original_elapsed = svc.get_state()["subjects"][0]["elapsed_ms"]
    snap = svc.snapshot()

    fresh = HyroxService()
    fresh.restore(snap)

    state = fresh.get_state()
    subject = state["subjects"][0]
    assert subject["subject_id"] == "alex"
    assert subject["current_stage"] == "run_1"
    assert subject["status"] == "racing"
    assert subject["progress_value"] == 400
    assert subject["assigned_resource"] == "treadmill-01"
    assert fresh.token_for("alex") == token
    assert fresh._store.active_on("treadmill-01").subject_id == "alex"
    # Same stage_start_ms carried over -- the clock is not reset by restore.
    assert subject["elapsed_ms"] >= original_elapsed

    # FTMS distance continues from the restored baseline: no double count, no
    # reset-to-zero.
    fresh.ingest_node("edge-tm-01", metrics={"distance_m": 500}, timestamp_ms=4)
    assert fresh.get_state()["subjects"][0]["progress_value"] == 500


def test_snapshot_restore_preserves_finished_and_abandoned_subjects(tmp_path):
    store = HyroxResultsStore(str(tmp_path / "r.db"))
    svc = HyroxService(results_store=store)
    svc.configure_venue(_venue(), mode="training", race_id="race-terminal")
    alex_token = svc.register("alex", "individual", "TAG_ALEX", "Alex")
    bella_token = svc.register("bella", "individual", "TAG_BELLA", "Bella")
    svc.start()

    svc.complete_stage("alex", timestamp_ms=10)   # not terminal yet, mid-race
    svc.abandon("bella", timestamp_ms=20)          # terminal: DNF

    snap = svc.snapshot()
    fresh = HyroxService()
    fresh.restore(snap)

    subjects = {s["subject_id"]: s for s in fresh.get_state()["subjects"]}
    assert subjects["alex"]["current_stage"] == "ski_erg"
    assert subjects["bella"]["status"] == "abandoned"
    assert fresh._finalized == {"bella"}
    assert fresh.token_for("alex") == alex_token
    assert fresh.token_for("bella") == bella_token
    store.close()


# --- Disk persistence ---

def test_configure_venue_and_register_persist_to_disk(tmp_path):
    state_path = tmp_path / "state.json"
    svc = HyroxService()
    svc.load_snapshot(str(state_path))   # enables persistence; nothing to restore yet
    assert svc.recovered is False

    svc.configure_venue(_venue(), mode="training", race_id="race-disk")
    assert state_path.exists()
    on_disk = json.loads(state_path.read_text())
    assert on_disk["race_id"] == "race-disk"
    assert on_disk["roster"]["subjects"] == []

    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    on_disk = json.loads(state_path.read_text())
    assert len(on_disk["roster"]["subjects"]) == 1


def test_load_snapshot_recovers_state_written_by_a_prior_instance(tmp_path):
    # Competition-mode explicit assignment persists unthrottled (unlike the
    # throttled telemetry path exercised separately below), so this test is
    # deterministic regardless of how fast it runs.
    state_path = tmp_path / "state.json"
    svc = HyroxService()
    svc.load_snapshot(str(state_path))
    svc.configure_venue(_venue(), mode="competition", race_id="race-restart")
    token = svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.assign("alex", "treadmill-01")

    # Simulate a hub restart: a brand-new service loads the same path.
    restarted = HyroxService()
    recovered = restarted.load_snapshot(str(state_path))

    assert recovered is True
    assert restarted.recovered is True
    assert restarted.get_state()["recovered"] is True
    assert restarted.race_id == "race-restart"
    assert restarted.token_for("alex") == token
    assert restarted.get_state()["subjects"][0]["assigned_resource"] == "treadmill-01"


def test_load_snapshot_missing_file_is_not_recovered_but_enables_future_writes(tmp_path):
    state_path = tmp_path / "does-not-exist.json"
    svc = HyroxService()

    assert svc.load_snapshot(str(state_path)) is False
    assert svc.recovered is False
    assert svc.get_state()["recovered"] is False

    # Persistence is now enabled going forward even though there was nothing
    # to recover.
    svc.configure_venue(_venue())
    assert state_path.exists()


def test_configure_venue_overwrites_stale_snapshot_for_a_new_race(tmp_path):
    state_path = tmp_path / "state.json"
    svc = HyroxService()
    svc.load_snapshot(str(state_path))
    svc.configure_venue(_venue(), race_id="race-one")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")

    # Configuring a brand-new race must overwrite the old snapshot, not merge
    # with it -- the old roster should not leak into the new file.
    svc.configure_venue(_venue(), race_id="race-two")

    on_disk = json.loads(state_path.read_text())
    assert on_disk["race_id"] == "race-two"
    assert on_disk["roster"]["subjects"] == []


def test_high_frequency_telemetry_writes_are_throttled(monkeypatch, tmp_path):
    import hub_server.usecases.hyrox_service as hs_module

    fake_now = {"ms": 1_000}
    monkeypatch.setattr(hs_module, "_now_ms", lambda: fake_now["ms"])

    state_path = tmp_path / "state.json"
    svc = HyroxService()
    svc.load_snapshot(str(state_path))
    svc.configure_venue(_venue(), mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX", timestamp_ms=1)

    def _snapshot_distance():
        return json.loads(state_path.read_text())["latest_ftms_distance"].get("treadmill-01")

    # First telemetry write lands at the same instant as the priming writes
    # from configure/register/start/claim, so it is throttled away.
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 10}, timestamp_ms=2)
    assert _snapshot_distance() is None

    fake_now["ms"] = 1_999  # still inside the 1s throttle window
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 20}, timestamp_ms=3)
    assert _snapshot_distance() is None  # still throttled

    fake_now["ms"] = 2_500  # past the 1s window
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 30}, timestamp_ms=4)
    assert _snapshot_distance() == 30.0  # write goes through
