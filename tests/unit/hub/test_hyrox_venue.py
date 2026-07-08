"""Phase 1 venue-config validation tests (architecture plan section 15)."""

import pytest

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_venue import (
    HyroxCourseProfile,
    HyroxEndpointSensor,
    HyroxResourceGroup,
    HyroxResourceUnit,
    HyroxSensorClass,
    HyroxStageDefinition,
    HyroxTargetType,
    HyroxVenueConfig,
    validate_venue_config,
    venue_readiness,
)


def _lane(resource_id, node, start_ant, finish_ant):
    return HyroxResourceUnit(
        resource_id=resource_id,
        display_name=resource_id,
        sensor_class=HyroxSensorClass.RFID_ENDPOINT_PAIR,
        lane_length_m=12.5,
        start_endpoint=HyroxEndpointSensor(node_id=node, antenna_id=start_ant),
        finish_endpoint=HyroxEndpointSensor(node_id=node, antenna_id=finish_ant),
    )


def _treadmill(resource_id, node):
    return HyroxResourceUnit(
        resource_id=resource_id,
        display_name=resource_id,
        sensor_class=HyroxSensorClass.FTMS_MACHINE,
        equipment_type="treadmill",
        node_id=node,
    )


def _valid_venue():
    """A small but valid venue: treadmill pool, turf lanes, row, wall balls."""
    return HyroxVenueConfig(
        venue_id="fitrace-hq",
        course_profile_id="hyrox_standard_2026",
        resource_groups=[
            HyroxResourceGroup(
                group_id="run_treadmills",
                resource_type="ftms_machine_pool",
                stage_candidates=[HyroxStage.RUN_1, HyroxStage.RUN_2],
                units=[_treadmill("treadmill-01", "edge-tm-01"),
                       _treadmill("treadmill-02", "edge-tm-02")],
            ),
            HyroxResourceGroup(
                group_id="shared_turf_lanes",
                resource_type="rfid_lane_pool",
                # One shared lane pool serves multiple stages
                stage_candidates=[HyroxStage.SLED_PUSH, HyroxStage.SLED_PULL,
                                  HyroxStage.FARMERS_CARRY],
                units=[_lane("turf-lane-1", "rfid-01", "L1_START", "L1_FINISH"),
                       _lane("turf-lane-2", "rfid-01", "L2_START", "L2_FINISH")],
            ),
            HyroxResourceGroup(
                group_id="row_pool",
                resource_type="ftms_machine_pool",
                stage_candidates=[HyroxStage.ROW],
                units=[_treadmill("rower-01", "edge-row-01")],
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


def _valid_profile():
    return HyroxCourseProfile(
        course_profile_id="hyrox_standard_2026",
        stages=[
            HyroxStageDefinition(stage=HyroxStage.RUN_1, target_type=HyroxTargetType.DISTANCE_M,
                                 target_value=1000, allowed_resource_groups=["run_treadmills"]),
            HyroxStageDefinition(stage=HyroxStage.SLED_PUSH, target_type=HyroxTargetType.LENGTHS,
                                 target_value=4, allowed_resource_groups=["shared_turf_lanes"]),
            HyroxStageDefinition(stage=HyroxStage.ROW, target_type=HyroxTargetType.DISTANCE_M,
                                 target_value=1000, allowed_resource_groups=["row_pool"]),
            HyroxStageDefinition(stage=HyroxStage.WALL_BALLS, target_type=HyroxTargetType.REPS,
                                 target_value=75, allowed_resource_groups=["wall_ball_targets"]),
        ],
    )


# --- Structural validation (venue only) ---

def test_valid_config_has_no_errors():
    assert validate_venue_config(_valid_venue()) == []


def test_shared_turf_lane_may_declare_multiple_stage_candidates():
    # Acceptance: shared turf lanes can declare multiple stage candidates.
    venue = _valid_venue()
    turf = next(g for g in venue.resource_groups if g.group_id == "shared_turf_lanes")
    assert len(turf.stage_candidates) > 1
    assert validate_venue_config(venue) == []


def test_duplicate_antenna_is_rejected():
    venue = _valid_venue()
    # Reuse turf-lane-1's start antenna on turf-lane-2's finish
    lane2 = _lane("turf-lane-2", "rfid-01", "L2_START", "L1_START")
    venue.resource_groups[1].units[1] = lane2
    errors = validate_venue_config(venue)
    assert any("Duplicate RFID read zone" in e for e in errors)


def test_rfid_pair_missing_finish_is_rejected():
    venue = _valid_venue()
    lane = venue.resource_groups[1].units[0]
    lane.finish_endpoint = None
    errors = validate_venue_config(venue)
    assert any("requires both" in e for e in errors)


def test_rfid_pair_same_start_and_finish_is_rejected():
    venue = _valid_venue()
    lane = venue.resource_groups[1].units[0]
    lane.finish_endpoint = HyroxEndpointSensor(
        node_id=lane.start_endpoint.node_id, antenna_id=lane.start_endpoint.antenna_id
    )
    errors = validate_venue_config(venue)
    assert any("different" in e for e in errors)


def test_duplicate_resource_id_is_rejected():
    venue = _valid_venue()
    venue.resource_groups[0].units[1].resource_id = "treadmill-01"
    errors = validate_venue_config(venue)
    assert any("Duplicate resource id" in e for e in errors)


def test_finished_is_not_a_valid_stage_candidate():
    venue = _valid_venue()
    venue.resource_groups[0].stage_candidates.append(HyroxStage.FINISHED)
    errors = validate_venue_config(venue)
    assert any("finished" in e for e in errors)


# --- Readiness (venue + course profile) ---

def test_ready_venue_has_no_readiness_errors():
    assert venue_readiness(_valid_venue(), _valid_profile()) == []


def test_ftms_maps_to_run_row_ski_stages():
    # Acceptance: FTMS resources can be mapped to run, row, and ski stages.
    venue = _valid_venue()
    profile = _valid_profile()
    profile.stages.append(
        HyroxStageDefinition(stage=HyroxStage.SKI_ERG, target_type=HyroxTargetType.DISTANCE_M,
                             target_value=1000, allowed_resource_groups=["run_treadmills"])
    )
    assert venue_readiness(venue, profile) == []


def test_multi_option_run_is_ready_when_only_one_option_exists():
    # A run allowing treadmills OR track is servable when only treadmills exist.
    venue = _valid_venue()
    profile = _valid_profile()
    profile.stages[0].allowed_resource_groups = ["run_treadmills", "run_track"]
    assert venue_readiness(venue, profile) == []


def test_target_resource_mismatch_is_a_readiness_error():
    # A lengths stage whose only group is an FTMS pool cannot be produced.
    venue = _valid_venue()
    profile = _valid_profile()
    sled = next(s for s in profile.stages if s.stage == HyroxStage.SLED_PUSH)
    sled.allowed_resource_groups = ["run_treadmills"]
    errors = venue_readiness(venue, profile)
    assert any("can produce" in e for e in errors)


def test_stage_with_no_configured_group_is_a_readiness_error():
    venue = _valid_venue()
    profile = _valid_profile()
    profile.stages[0].allowed_resource_groups = ["nonexistent_group"]
    errors = venue_readiness(venue, profile)
    assert any("no configured resource group" in e for e in errors)
