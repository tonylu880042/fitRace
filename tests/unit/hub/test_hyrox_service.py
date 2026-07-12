"""Phase 6a orchestrator tests: roster + registry + store + tracker + engine
driven end to end through HyroxService."""

import pytest

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_venue import (
    HyroxEndpointSensor,
    HyroxResourceGroup,
    HyroxResourceUnit,
    HyroxSensorClass,
    HyroxVenueConfig,
)
from hub_server.usecases.hyrox_roster import HyroxRoster
from hub_server.usecases.hyrox_results_store import HyroxResultsStore
from hub_server.usecases.hyrox_service import HyroxService
from hub_server.usecases.hyrox_sensor_registry import START_LINE, FINISH_LINE


def _venue():
    return HyroxVenueConfig(
        venue_id="hq",
        course_profile_id="hyrox_standard_2026",
        resource_groups=[
            HyroxResourceGroup(
                group_id="run_treadmills", resource_type="ftms_machine_pool",
                stage_candidates=[],  # candidates are advisory; stage comes from athlete state
                units=[HyroxResourceUnit(
                    resource_id="treadmill-01", display_name="TM1",
                    sensor_class=HyroxSensorClass.FTMS_MACHINE, node_id="edge-tm-01",
                    entry_gate=HyroxEndpointSensor(node_id="rfid-tm-01", antenna_id="T1_GATE"),
                    abandon_endpoint=HyroxEndpointSensor(
                        node_id="abandon-tm-01", antenna_id="T1_BUTTON"
                    ),
                    pulse_to_meter=250.0,
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


def _svc(mode="training"):
    svc = HyroxService()
    svc.configure_venue(_venue(), mode=mode)
    return svc


def _venue_with_rep_counter():
    venue = _venue()
    venue.resource_groups.append(
        HyroxResourceGroup(
            group_id="wall_ball_targets",
            resource_type="rep_counter_pool",
            stage_candidates=[],
            units=[
                HyroxResourceUnit(
                    resource_id="wallball-01",
                    display_name="Wall Ball 1",
                    sensor_class=HyroxSensorClass.REP_COUNTER,
                    node_id="edge-wb-01",
                    entry_gate=HyroxEndpointSensor(
                        node_id="rfid-wb-01", antenna_id="WB1_GATE"
                    ),
                )
            ],
        )
    )
    return venue


def test_configure_register_start_and_state():
    svc = _svc()
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    state = svc.get_state()
    assert state["is_active"] is True
    assert state["venue_configured"] is True
    assert state["subjects"][0]["subject_id"] == "alex"
    assert state["subjects"][0]["current_stage"] == "run_1"


def test_bad_venue_config_is_rejected():
    svc = HyroxService()
    bad = HyroxVenueConfig(
        venue_id="x", course_profile_id="p",
        resource_groups=[HyroxResourceGroup(
            group_id="g", resource_type="rfid_lane_pool", stage_candidates=[],
            units=[HyroxResourceUnit(
                resource_id="lane", display_name="lane",
                sensor_class=HyroxSensorClass.RFID_ENDPOINT_PAIR,  # missing endpoints
            )],
        )],
    )
    with pytest.raises(ValueError):
        svc.configure_venue(bad)


def test_training_dynamic_claim_then_ftms_progresses_run():
    svc = _svc(mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()

    # Athlete taps the treadmill entry gate -> dynamic claim binds treadmill-01.
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX")
    st = svc.get_state()
    assert st["resources"]["treadmill-01"] == "in_use"

    # FTMS distance (anonymous) is attributed to the bound athlete.
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 0})      # baseline
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 1000})   # reach target
    st = svc.get_state()
    assert st["subjects"][0]["current_stage"] == "ski_erg"        # advanced
    assert st["resources"]["treadmill-01"] == "free"             # released


def test_ftms_cached_before_start_seeds_dynamic_claim_baseline():
    svc = _svc(mode="training")
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 5000}, timestamp_ms=1)
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()

    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX", timestamp_ms=2)
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 5100}, timestamp_ms=3)

    assert svc.get_state()["subjects"][0]["progress_value"] == 100


def test_operator_assignment_seeds_baseline_and_duplicate_does_not_reset_progress():
    svc = _svc(mode="competition")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 5000}, timestamp_ms=1)

    assert svc.assign("alex", "treadmill-01", timestamp_ms=2) is True
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 5100}, timestamp_ms=3)
    assert svc.assign("alex", "treadmill-01", timestamp_ms=4) is True
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 5200}, timestamp_ms=5)

    assert svc.get_state()["subjects"][0]["progress_value"] == 200


def test_ftms_without_pre_bind_read_uses_first_reading_as_baseline():
    svc = _svc(mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX", timestamp_ms=1)

    svc.ingest_node("edge-tm-01", metrics={"distance_m": 5100}, timestamp_ms=2)
    assert svc.get_state()["subjects"][0]["progress_value"] == 0
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 5200}, timestamp_ms=3)

    assert svc.get_state()["subjects"][0]["progress_value"] == 100


def test_seeded_ftms_progress_stays_monotonic_across_counter_reset():
    svc = _svc(mode="training")
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 5000}, timestamp_ms=1)
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX", timestamp_ms=2)

    svc.ingest_node("edge-tm-01", metrics={"distance_m": 5100}, timestamp_ms=3)
    svc.ingest_node("edge-tm-01", metrics={"distance_m": 50}, timestamp_ms=4)

    assert svc.get_state()["subjects"][0]["progress_value"] == 150


def test_generic_distance_from_rep_counter_does_not_count_as_rep():
    svc = HyroxService()
    svc.configure_venue(_venue_with_rep_counter(), mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc._engine.state_of("alex").current_stage = HyroxStage.WALL_BALLS
    svc.ingest_rfid("rfid-wb-01", "WB1_GATE", "TAG_ALEX", timestamp_ms=1)

    svc.ingest_node("edge-wb-01", metrics={"distance_m": 42}, timestamp_ms=2)
    assert svc.get_state()["subjects"][0]["progress_value"] == 0

    svc.ingest_node("edge-wb-01", metrics=None, timestamp_ms=3)
    assert svc.get_state()["subjects"][0]["progress_value"] == 1


def test_unregistered_tag_does_not_claim():
    svc = _svc(mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_STRANGER")
    assert svc.get_state()["resources"]["treadmill-01"] == "free"


def test_competition_mode_requires_operator_assignment():
    svc = _svc(mode="competition")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    # No dynamic claim in competition mode: an RFID read alone binds nothing.
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX")
    assert svc.get_state()["resources"]["treadmill-01"] == "free"
    # Operator assigns explicitly.
    assert svc.assign("alex", "treadmill-01") is True
    assert svc.get_state()["resources"]["treadmill-01"] == "in_use"


def test_operator_assignment_is_rejected_in_training_mode():
    svc = _svc(mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")

    with pytest.raises(ValueError, match="competition mode"):
        svc.assign("alex", "treadmill-01")

    assert svc.get_state()["resources"]["treadmill-01"] == "free"


def test_operator_assignment_rejects_unknown_subject_and_resource():
    svc = _svc(mode="competition")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")

    with pytest.raises(ValueError, match="Subject .* not found"):
        svc.assign("missing", "treadmill-01")
    with pytest.raises(ValueError, match="Resource .* not found"):
        svc.assign("alex", "does-not-exist")

    assert svc.get_state()["subjects"][0]["assigned_resource"] is None


def test_operator_assignment_rejects_resource_for_wrong_stage():
    svc = _svc(mode="competition")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")

    with pytest.raises(ValueError, match="not allowed for stage run_1"):
        svc.assign("alex", "turf-lane-1")

    assert svc.get_state()["resources"]["turf-lane-1"] == "free"


@pytest.mark.parametrize("terminal_status", ["abandoned", "finished"])
def test_operator_assignment_rejects_terminal_subject(terminal_status):
    svc = _svc(mode="competition")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc._engine.state_of("alex").status = terminal_status

    with pytest.raises(ValueError, match="is not racing"):
        svc.assign("alex", "treadmill-01")

    assert svc.get_state()["resources"]["treadmill-01"] == "free"


def test_team_assignment_requires_a_registered_active_tag():
    svc = _svc(mode="competition")
    svc.register("duo", "doubles", "TAG_ONE", "One")
    svc.register("duo", "doubles", "TAG_TWO", "Two")

    with pytest.raises(ValueError, match="active_tag_id is required"):
        svc.assign("duo", "treadmill-01")
    with pytest.raises(ValueError, match="does not belong to subject duo"):
        svc.assign("duo", "treadmill-01", active_tag_id="TAG_STRANGER")

    assert svc.get_state()["resources"]["treadmill-01"] == "free"
    assert svc.assign("duo", "treadmill-01", active_tag_id="TAG_TWO") is True
    assert svc._store.active_on("treadmill-01").active_tag_id == "TAG_TWO"


def test_pre_start_assignment_is_allowed_and_occupied_resource_conflicts():
    svc = _svc(mode="competition")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.register("bella", "individual", "TAG_BELLA", "Bella")

    assert svc.assign("alex", "treadmill-01") is True
    with pytest.raises(ValueError, match="occupied"):
        svc.assign("bella", "treadmill-01")

    state = svc.get_state()
    assert state["subjects"][0]["assigned_resource"] == "treadmill-01"
    assert state["subjects"][1]["assigned_resource"] is None


def test_lane_lengths_progress_and_complete():
    svc = _svc(mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    # Move Alex to a lane stage.
    svc._engine.state_of("alex").current_stage = __import__(
        "hub_server.domain.models", fromlist=["HyroxStage"]
    ).HyroxStage.SLED_PUSH

    for endpoint, ts in [(START_LINE, 1), (FINISH_LINE, 2), (START_LINE, 3),
                         (FINISH_LINE, 4), (START_LINE, 5)]:
        antenna = "L1_START" if endpoint == START_LINE else "L1_FINISH"
        svc.ingest_rfid("rfid-01", antenna, "TAG_ALEX", timestamp_ms=ts)

    st = svc.get_state()["subjects"][0]
    assert st["current_stage"] == "run_3"  # sled_push completed (4 lengths)


def test_abandon_and_complete_stage():
    svc = _svc()
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.complete_stage("alex")
    assert svc.get_state()["subjects"][0]["current_stage"] == "ski_erg"
    svc.abandon("alex")
    assert svc.get_state()["subjects"][0]["status"] == "abandoned"


def test_sensor_abandon_requires_matching_active_assignment_and_finalizes(tmp_path):
    store = HyroxResultsStore(str(tmp_path / "sensor-abandon.db"))
    svc = HyroxService(results_store=store)
    svc.configure_venue(_venue(), mode="training", race_id="sensor-abandon")
    token = svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX", timestamp_ms=1)

    svc.ingest_abandon(
        "abandon-tm-01", "T1_BUTTON", "TAG_ALEX", timestamp_ms=0
    )
    svc.ingest_abandon(
        "abandon-tm-01", "T1_BUTTON", "TAG_ALEX", timestamp_ms=0
    )

    state = svc.get_state()
    assert state["subjects"][0]["status"] == "abandoned"
    assert state["resources"]["treadmill-01"] == "free"
    assert svc.result_by_token(token).status == "dnf"
    assert svc._finalized == {"alex"}
    store.close()


def test_sensor_abandon_rejects_wrong_tag_without_releasing_assignment():
    svc = _svc(mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX", timestamp_ms=1)

    svc.ingest_abandon(
        "abandon-tm-01", "T1_BUTTON", "TAG_WRONG", timestamp_ms=2
    )

    state = svc.get_state()
    assert state["subjects"][0]["status"] == "racing"
    assert state["resources"]["treadmill-01"] == "in_use"
    assert state["diagnostics"][-1]["kind"] == "abandon_tag_mismatch"


def test_sensor_abandon_rejects_unassigned_resource_with_diagnostic():
    svc = _svc(mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()

    svc.ingest_abandon(
        "abandon-tm-01", "T1_BUTTON", "TAG_ALEX", timestamp_ms=3
    )

    state = svc.get_state()
    assert state["subjects"][0]["status"] == "racing"
    assert state["diagnostics"][-1]["kind"] == "abandon_unassigned"


def test_sensor_abandon_rejects_unknown_sensor_with_diagnostic():
    svc = _svc(mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()

    svc.ingest_abandon("unknown", "UNKNOWN", "TAG_ALEX", timestamp_ms=4)

    state = svc.get_state()
    assert state["subjects"][0]["status"] == "racing"
    assert state["diagnostics"][-1]["kind"] == "abandon_unknown_sensor"


def test_pre_activity_abandon_finalizes_dnf_at_supplied_zero_timestamp(tmp_path):
    store = HyroxResultsStore(str(tmp_path / "dnf.db"))
    svc = HyroxService(results_store=store)
    svc.configure_venue(_venue(), race_id="race-dnf")
    token = svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()

    svc.abandon("alex", timestamp_ms=0)

    result = svc.result_by_token(token)
    assert result is not None
    assert result.status == "dnf"
    assert result.started_at_ms == 0
    assert result.finished_at_ms is None
    assert result.total_time_ms is None
    assert result.splits == []
    store.close()


def test_force_complete_stage_preserves_supplied_zero_timestamp():
    svc = _svc()
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()

    svc.complete_stage("alex", timestamp_ms=0)

    state = svc._engine.state_of("alex")
    assert state.stage_start_ms[HyroxStage.RUN_1] == 0
    assert state.stage_end_ms[HyroxStage.RUN_1] == 0


def test_abandon_zone_rfid_read_routes_to_abandon_flow(tmp_path):
    store = HyroxResultsStore(str(tmp_path / "abandon-rfid.db"))
    svc = HyroxService(results_store=store)
    svc.configure_venue(_venue(), mode="training", race_id="abandon-rfid")
    token = svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX", timestamp_ms=1)

    # Athlete taps the abandon button via the regular RFID topic.
    svc.ingest_rfid("abandon-tm-01", "T1_BUTTON", "TAG_ALEX", timestamp_ms=2)

    state = svc.get_state()
    assert state["subjects"][0]["status"] == "abandoned"
    assert state["resources"]["treadmill-01"] == "free"
    assert svc.result_by_token(token).status == "dnf"
    store.close()


def test_abandon_zone_rfid_read_rejects_wrong_tag_via_rfid():
    svc = _svc(mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX", timestamp_ms=1)

    # Different tag taps the abandon button via the regular RFID topic.
    svc.ingest_rfid("abandon-tm-01", "T1_BUTTON", "TAG_WRONG", timestamp_ms=2)

    state = svc.get_state()
    assert state["subjects"][0]["status"] == "racing"
    assert state["resources"]["treadmill-01"] == "in_use"
    assert state["diagnostics"][-1]["kind"] == "abandon_tag_mismatch"


def test_one_pre_activity_dnf_does_not_block_another_finalization(tmp_path):
    store = HyroxResultsStore(str(tmp_path / "multiple-dnfs.db"))
    svc = HyroxService(results_store=store)
    svc.configure_venue(_venue(), race_id="race-multiple-dnfs")
    alex_token = svc.register("alex", "individual", "TAG_ALEX", "Alex")
    bella_token = svc.register("bella", "individual", "TAG_BELLA", "Bella")
    svc.start()

    svc.abandon("alex", timestamp_ms=10)
    svc.abandon("bella", timestamp_ms=20)

    alex = svc.result_by_token(alex_token)
    bella = svc.result_by_token(bella_token)
    assert alex is not None and alex.status == "dnf"
    assert bella is not None and bella.status == "dnf"
    assert alex.started_at_ms == 10
    assert bella.started_at_ms == 20
    store.close()


# --- Phase 8: DQ, penalties, reinstate ---


def test_disqualify_finalizes_as_dq_with_reason(tmp_path):
    from hub_server.usecases.hyrox_service import HyroxAssignmentError

    store = HyroxResultsStore(str(tmp_path / "dq.db"))
    svc = HyroxService(results_store=store)
    svc.configure_venue(_venue(), race_id="race-dq")
    token = svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()

    svc.disqualify("alex", "left station before work complete", timestamp_ms=50)

    state = svc.get_state()["subjects"][0]
    assert state["status"] == "disqualified"
    assert state["dq_reason"] == "left station before work complete"
    result = svc.result_by_token(token)
    assert result.status == "dq"
    assert result.dq_reason == "left station before work complete"
    assert svc._finalized == {"alex"}

    with pytest.raises(HyroxAssignmentError) as exc:
        svc.disqualify("ghost", "no such subject")
    assert exc.value.code == "unknown_subject"

    with pytest.raises(HyroxAssignmentError) as exc:
        svc.disqualify("alex", "already terminal")  # no longer racing
    assert exc.value.code == "not_racing"
    store.close()


def test_add_penalty_sums_into_total_ms_and_affects_rank(tmp_path):
    store = HyroxResultsStore(str(tmp_path / "penalty.db"))
    svc = HyroxService(results_store=store)
    svc.configure_venue(_venue(), race_id="race-penalty")
    alex_token = svc.register("alex", "individual", "TAG_ALEX", "Alex")
    bella_token = svc.register("bella", "individual", "TAG_BELLA", "Bella")
    svc.start()

    svc.add_penalty("alex", 5_000, "movement standard breach", timestamp_ms=1)

    for i in range(16):
        svc.complete_stage("alex", timestamp_ms=10 + i)   # race_start=10, finish=25, raw=15
    for i in range(16):
        svc.complete_stage("bella", timestamp_ms=5 + i)   # race_start=5, finish=20, raw=15

    alex = svc.result_by_token(alex_token)
    bella = svc.result_by_token(bella_token)
    assert alex.status == "finished"
    assert alex.total_time_ms == 15 + 5_000
    assert [p.penalty_ms for p in alex.penalties] == [5_000]
    assert bella.total_time_ms == 15
    # Bella's unpenalized raw time beats Alex's penalized total.
    assert bella.rank == 1
    assert alex.rank == 2
    store.close()


def test_add_penalty_unknown_subject_raises():
    from hub_server.usecases.hyrox_service import HyroxAssignmentError

    svc = _svc()
    with pytest.raises(HyroxAssignmentError) as exc:
        svc.add_penalty("ghost", 1_000, "no such subject")
    assert exc.value.code == "unknown_subject"


def test_reinstate_unfinalizes_and_recomputes_ranks(tmp_path):
    store = HyroxResultsStore(str(tmp_path / "reinstate.db"))
    svc = HyroxService(results_store=store)
    svc.configure_venue(_venue(), race_id="race-reinstate")
    alex_token = svc.register("alex", "individual", "TAG_ALEX", "Alex")
    bella_token = svc.register("bella", "individual", "TAG_BELLA", "Bella")
    svc.start()

    for _ in range(16):
        svc.complete_stage("alex", timestamp_ms=100)  # finishes first, rank 1
    svc.abandon("bella", timestamp_ms=50)              # DNF, not ranked

    assert svc.result_by_token(alex_token).rank == 1
    assert svc._finalized == {"alex", "bella"}

    # Operator overturns bella's DNF -- a button misfire.
    svc.reinstate("bella", timestamp_ms=200)

    state = svc.get_state()
    bella_state = next(s for s in state["subjects"] if s["subject_id"] == "bella")
    assert bella_state["status"] == "racing"
    assert svc.result_by_token(bella_token) is None   # un-finalized
    assert "bella" not in svc._finalized
    assert svc.result_by_token(alex_token).rank == 1   # alex's rank recomputed, unaffected

    # Reinstated athlete can resume: gate claim binds a fresh resource.
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_BELLA", timestamp_ms=210)
    assert svc.get_state()["resources"]["treadmill-01"] == "in_use"


def test_reinstate_from_disqualified_and_idempotent_guard(tmp_path):
    from hub_server.usecases.hyrox_service import HyroxAssignmentError

    store = HyroxResultsStore(str(tmp_path / "reinstate-dq.db"))
    svc = HyroxService(results_store=store)
    svc.configure_venue(_venue(), race_id="race-reinstate-dq")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()
    svc.disqualify("alex", "equipment misuse", timestamp_ms=30)

    svc.reinstate("alex", timestamp_ms=40)
    assert svc.get_state()["subjects"][0]["status"] == "racing"

    # Idempotent guard: the subject is racing now, not abandoned/disqualified.
    with pytest.raises(HyroxAssignmentError) as exc:
        svc.reinstate("alex", timestamp_ms=50)
    assert exc.value.code == "invalid_state"

    with pytest.raises(HyroxAssignmentError) as exc:
        svc.reinstate("ghost", timestamp_ms=50)
    assert exc.value.code == "unknown_subject"
    store.close()


def test_ingest_ignored_before_start_or_config():
    svc = HyroxService()
    # Not configured: ingestion is a no-op, no crash.
    svc.ingest_rfid("n", "a", "t")
    svc.ingest_node("n", metrics={"distance_m": 10})
    assert svc.get_state()["venue_configured"] is False


def test_roster_rejects_duplicate_tag_across_subjects():
    roster = HyroxRoster()
    roster.add_member("team-a", "doubles", "TAG_1", "Tom")
    with pytest.raises(ValueError):
        roster.add_member("team-b", "doubles", "TAG_1", "Jerry")


def test_roster_flags_overfilled_team():
    roster = HyroxRoster()
    roster.add_member("duo", "doubles", "T1", "A")
    roster.add_member("duo", "doubles", "T2", "B")
    roster.add_member("duo", "doubles", "T3", "C")  # one too many
    assert "duo" in roster.overfilled()


def test_pulse_to_meter_progresses_run():
    svc = _svc(mode="training")
    svc.register("alex", "individual", "TAG_ALEX", "Alex")
    svc.start()

    # Athlete taps the treadmill entry gate -> dynamic claim binds treadmill-01.
    svc.ingest_rfid("rfid-tm-01", "T1_GATE", "TAG_ALEX")

    # Send 4 pulses of 250m each (no distance_m in metrics)
    svc.ingest_node("edge-tm-01", metrics=None)  # 250m
    svc.ingest_node("edge-tm-01", metrics=None)  # 500m
    svc.ingest_node("edge-tm-01", metrics=None)  # 750m
    svc.ingest_node("edge-tm-01", metrics=None)  # 1000m -> completed!

    st = svc.get_state()
    assert st["subjects"][0]["current_stage"] == "ski_erg"
