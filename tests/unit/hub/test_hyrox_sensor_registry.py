"""Phase 2 sensor-registry resolution tests (architecture plan section 15)."""

import pytest

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_venue import (
    HyroxEndpointSensor,
    HyroxResourceGroup,
    HyroxResourceUnit,
    HyroxSensorClass,
    HyroxVenueConfig,
)
from hub_server.usecases.hyrox_sensor_registry import (
    ENTRY_GATE,
    FINISH_LINE,
    START_LINE,
    HyroxSensorRegistry,
)


def _venue():
    """Treadmill (ftms + entry gate), turf lane (start/finish), wall ball (rep)."""
    return HyroxVenueConfig(
        venue_id="hq",
        course_profile_id="p",
        resource_groups=[
            HyroxResourceGroup(
                group_id="run_treadmills",
                resource_type="ftms_machine_pool",
                stage_candidates=[HyroxStage.RUN_1, HyroxStage.RUN_2],
                units=[HyroxResourceUnit(
                    resource_id="treadmill-01",
                    display_name="Treadmill 1",
                    sensor_class=HyroxSensorClass.FTMS_MACHINE,
                    node_id="edge-tm-01",
                    entry_gate=HyroxEndpointSensor(node_id="rfid-tm-01", antenna_id="T1_GATE"),
                )],
            ),
            HyroxResourceGroup(
                group_id="shared_turf_lanes",
                resource_type="rfid_lane_pool",
                stage_candidates=[HyroxStage.SLED_PUSH, HyroxStage.FARMERS_CARRY],
                units=[HyroxResourceUnit(
                    resource_id="turf-lane-1",
                    display_name="Turf Lane 1",
                    sensor_class=HyroxSensorClass.RFID_ENDPOINT_PAIR,
                    start_endpoint=HyroxEndpointSensor(node_id="rfid-01", antenna_id="L1_START"),
                    finish_endpoint=HyroxEndpointSensor(node_id="rfid-01", antenna_id="L1_FINISH"),
                )],
            ),
            HyroxResourceGroup(
                group_id="wall_ball_targets",
                resource_type="rep_counter_pool",
                stage_candidates=[HyroxStage.WALL_BALLS],
                units=[HyroxResourceUnit(
                    resource_id="wallball-1",
                    display_name="Wall Ball 1",
                    sensor_class=HyroxSensorClass.REP_COUNTER,
                    node_id="edge-wb-01",
                )],
            ),
        ],
    )


def test_rfid_address_resolves_to_one_lane_endpoint():
    reg = HyroxSensorRegistry(_venue())

    start = reg.resolve_rfid("rfid-01", "L1_START")
    assert start.resource_id == "turf-lane-1"
    assert start.role == START_LINE
    assert HyroxStage.SLED_PUSH in start.stage_candidates

    finish = reg.resolve_rfid("rfid-01", "L1_FINISH")
    assert finish.resource_id == "turf-lane-1"
    assert finish.role == FINISH_LINE


def test_entry_gate_resolves_to_treadmill():
    reg = HyroxSensorRegistry(_venue())
    res = reg.resolve_rfid("rfid-tm-01", "T1_GATE")
    assert res.resource_id == "treadmill-01"
    assert res.role == ENTRY_GATE


def test_node_resolves_to_one_ftms_unit():
    reg = HyroxSensorRegistry(_venue())
    res = reg.resolve_node("edge-tm-01")
    assert res.resource_id == "treadmill-01"
    assert res.sensor_class == HyroxSensorClass.FTMS_MACHINE


def test_rep_counter_node_resolves():
    reg = HyroxSensorRegistry(_venue())
    res = reg.resolve_node("edge-wb-01")
    assert res.resource_id == "wallball-1"
    assert res.sensor_class == HyroxSensorClass.REP_COUNTER


def test_unknown_sensor_resolves_to_none():
    reg = HyroxSensorRegistry(_venue())
    assert reg.resolve_rfid("rfid-99", "NOPE") is None
    assert reg.resolve_node("edge-unknown") is None


def test_normalize_rfid_produces_resource_aware_event():
    reg = HyroxSensorRegistry(_venue())
    ev = reg.normalize_rfid("rfid-01", "L1_FINISH", tag_id="TAG_A", timestamp_epoch_ms=1000)
    assert ev is not None
    assert ev.resource_id == "turf-lane-1"
    assert ev.resource_group_id == "shared_turf_lanes"
    assert ev.endpoint == FINISH_LINE
    assert ev.tag_id == "TAG_A"
    assert ev.sensor_class == HyroxSensorClass.RFID_ENDPOINT_PAIR


def test_normalize_ftms_is_anonymous():
    reg = HyroxSensorRegistry(_venue())
    ev = reg.normalize_node("edge-tm-01", timestamp_epoch_ms=2000,
                            metrics={"distance_m": 250.0})
    assert ev is not None
    assert ev.resource_id == "treadmill-01"
    assert ev.tag_id is None  # FTMS carries no identity
    assert ev.metrics["distance_m"] == 250.0


def test_normalize_unknown_returns_none():
    reg = HyroxSensorRegistry(_venue())
    assert reg.normalize_rfid("x", "y", tag_id="T", timestamp_epoch_ms=1) is None
    assert reg.normalize_node("x", timestamp_epoch_ms=1) is None


def test_duplicate_sensor_address_fails_registry_build():
    venue = _venue()
    # Reuse the lane's start antenna as the treadmill entry gate
    venue.resource_groups[0].units[0].entry_gate = HyroxEndpointSensor(
        node_id="rfid-01", antenna_id="L1_START"
    )
    with pytest.raises(ValueError, match="Duplicate RFID read zone"):
        HyroxSensorRegistry(venue)
