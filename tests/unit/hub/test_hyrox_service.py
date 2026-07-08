"""Phase 6a orchestrator tests: roster + registry + store + tracker + engine
driven end to end through HyroxService."""

import pytest

from hub_server.domain.hyrox_venue import (
    HyroxEndpointSensor,
    HyroxResourceGroup,
    HyroxResourceUnit,
    HyroxSensorClass,
    HyroxVenueConfig,
)
from hub_server.usecases.hyrox_roster import HyroxRoster
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
