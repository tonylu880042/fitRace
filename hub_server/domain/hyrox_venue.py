"""Hyrox venue and course-profile contracts, plus config validation.

Phase 1 of the Hyrox architecture plan (docs/hyrox_system_architecture_plan.md):
domain contracts + validation only. Nothing here touches the live HyroxManager,
the MQTT ingestion path, or the existing /api/hyrox/configure endpoint.
"""

from enum import Enum

from pydantic import BaseModel

from hub_server.domain.models import HyroxStage


class HyroxSensorClass(str, Enum):
    FTMS_MACHINE = "ftms_machine"
    RFID_ENDPOINT_PAIR = "rfid_endpoint_pair"
    RFID_ENTRY_GATE = "rfid_entry_gate"
    REP_COUNTER = "rep_counter"
    MANUAL_OVERRIDE = "manual_override"
    ABANDON_BUTTON = "abandon_button"


class HyroxTargetType(str, Enum):
    DISTANCE_M = "distance_m"
    LENGTHS = "lengths"
    REPS = "reps"
    TIME_MS = "time_ms"
    MANUAL = "manual"


# Which sensor classes can produce progress for a given target type.
# MANUAL is completable by any resource (operator override always allowed).
_TARGET_SENSORS: dict[HyroxTargetType, set[HyroxSensorClass]] = {
    HyroxTargetType.DISTANCE_M: {
        HyroxSensorClass.FTMS_MACHINE,
        HyroxSensorClass.RFID_ENTRY_GATE,  # track laps -> distance
    },
    HyroxTargetType.LENGTHS: {HyroxSensorClass.RFID_ENDPOINT_PAIR},
    HyroxTargetType.REPS: {HyroxSensorClass.REP_COUNTER},
    HyroxTargetType.TIME_MS: {
        HyroxSensorClass.FTMS_MACHINE,
        HyroxSensorClass.RFID_ENTRY_GATE,
        HyroxSensorClass.RFID_ENDPOINT_PAIR,
        HyroxSensorClass.REP_COUNTER,
    },
    HyroxTargetType.MANUAL: set(HyroxSensorClass),
}


class HyroxEndpointSensor(BaseModel):
    node_id: str
    antenna_id: str


class HyroxResourceUnit(BaseModel):
    resource_id: str
    display_name: str
    sensor_class: HyroxSensorClass  # the progress sensor
    equipment_type: str | None = None
    node_id: str | None = None
    lane_length_m: float | None = None
    start_endpoint: HyroxEndpointSensor | None = None
    finish_endpoint: HyroxEndpointSensor | None = None
    # Identity reader that binds an athlete to an otherwise-anonymous unit
    # (e.g. the RFID gate in front of a treadmill).
    entry_gate: HyroxEndpointSensor | None = None
    pulse_to_meter: float | None = None


class HyroxResourceGroup(BaseModel):
    group_id: str
    resource_type: str
    stage_candidates: list[HyroxStage]
    units: list[HyroxResourceUnit]


class HyroxStageDefinition(BaseModel):
    stage: HyroxStage
    target_type: HyroxTargetType
    target_value: float
    allowed_resource_groups: list[str]


class HyroxCourseProfile(BaseModel):
    course_profile_id: str
    stages: list[HyroxStageDefinition]


class HyroxVenueConfig(BaseModel):
    venue_id: str
    course_profile_id: str
    resource_groups: list[HyroxResourceGroup]


# Standard Hyrox target per workout stage. Runs, SkiErg and Row are distance;
# the five turf-lane stations are counted in lengths; wall balls in reps.
_STANDARD_TARGETS: dict[HyroxStage, tuple[HyroxTargetType, float, list[str]]] = {
    HyroxStage.RUN_1: (HyroxTargetType.DISTANCE_M, 1000, ["run_treadmills", "run_track"]),
    HyroxStage.SKI_ERG: (HyroxTargetType.DISTANCE_M, 1000, ["ski_erg_pool"]),
    HyroxStage.RUN_2: (HyroxTargetType.DISTANCE_M, 1000, ["run_treadmills", "run_track"]),
    HyroxStage.SLED_PUSH: (HyroxTargetType.LENGTHS, 4, ["shared_turf_lanes"]),
    HyroxStage.RUN_3: (HyroxTargetType.DISTANCE_M, 1000, ["run_treadmills", "run_track"]),
    HyroxStage.SLED_PULL: (HyroxTargetType.LENGTHS, 4, ["shared_turf_lanes"]),
    HyroxStage.RUN_4: (HyroxTargetType.DISTANCE_M, 1000, ["run_treadmills", "run_track"]),
    HyroxStage.BURPEE_BROAD: (HyroxTargetType.LENGTHS, 4, ["shared_turf_lanes"]),
    HyroxStage.RUN_5: (HyroxTargetType.DISTANCE_M, 1000, ["run_treadmills", "run_track"]),
    HyroxStage.ROW: (HyroxTargetType.DISTANCE_M, 1000, ["row_pool"]),
    HyroxStage.RUN_6: (HyroxTargetType.DISTANCE_M, 1000, ["run_treadmills", "run_track"]),
    HyroxStage.FARMERS_CARRY: (HyroxTargetType.LENGTHS, 4, ["shared_turf_lanes"]),
    HyroxStage.RUN_7: (HyroxTargetType.DISTANCE_M, 1000, ["run_treadmills", "run_track"]),
    HyroxStage.SANDBAG_LUNGES: (HyroxTargetType.LENGTHS, 4, ["shared_turf_lanes"]),
    HyroxStage.RUN_8: (HyroxTargetType.DISTANCE_M, 1000, ["run_treadmills", "run_track"]),
    HyroxStage.WALL_BALLS: (HyroxTargetType.REPS, 75, ["wall_ball_targets"]),
}


def default_hyrox_course_profile(
    course_profile_id: str = "hyrox_standard_2026",
) -> HyroxCourseProfile:
    """The standard Hyrox stage order and targets. The stage sequence follows
    the HyroxStage enum declaration order; allowed_resource_groups use the
    conventional group ids a venue is expected to define (override per venue)."""
    return HyroxCourseProfile(
        course_profile_id=course_profile_id,
        stages=[
            HyroxStageDefinition(
                stage=stage,
                target_type=target_type,
                target_value=target_value,
                allowed_resource_groups=groups,
            )
            for stage, (target_type, target_value, groups) in _STANDARD_TARGETS.items()
        ],
    )


def _endpoint_addr(ep: HyroxEndpointSensor) -> tuple[str, str]:
    return (ep.node_id, ep.antenna_id)


def validate_venue_config(venue: HyroxVenueConfig) -> list[str]:
    """Structural validation of a venue, independent of any course profile.

    Errors here mean the wiring is wrong: duplicate ids, duplicate sensor
    addresses, incomplete RFID endpoint pairs, or an FTMS unit with no node.
    Whether the venue can actually run a full Hyrox (every stage has a resource)
    is a readiness question -- see venue_readiness().
    """
    errors: list[str] = []

    seen_groups: set[str] = set()
    seen_resources: set[str] = set()
    # Every physical sensor address (node_id, antenna_id) must be unique across
    # the whole venue; likewise an FTMS node may back only one resource unit.
    seen_endpoints: dict[tuple[str, str], str] = {}
    seen_ftms_nodes: dict[str, str] = {}

    for group in venue.resource_groups:
        if group.group_id in seen_groups:
            errors.append(f"Duplicate resource group id: {group.group_id}.")
        seen_groups.add(group.group_id)

        # stage_candidates is advisory; the course profile is authoritative for
        # which group serves a stage. Only a stray 'finished' is a data mistake.
        if HyroxStage.FINISHED in group.stage_candidates:
            errors.append(
                f"Resource group {group.group_id} lists 'finished', which is not a workout stage."
            )

        for unit in group.units:
            if unit.resource_id in seen_resources:
                errors.append(f"Duplicate resource id: {unit.resource_id}.")
            seen_resources.add(unit.resource_id)

            # RFID endpoint-pair units need two distinct read zones.
            if unit.sensor_class == HyroxSensorClass.RFID_ENDPOINT_PAIR:
                if unit.start_endpoint is None or unit.finish_endpoint is None:
                    errors.append(
                        f"Resource {unit.resource_id}: rfid_endpoint_pair requires both "
                        f"start and finish endpoints."
                    )
                elif _endpoint_addr(unit.start_endpoint) == _endpoint_addr(
                    unit.finish_endpoint
                ):
                    errors.append(
                        f"Resource {unit.resource_id}: start and finish must use different "
                        f"RFID read zones."
                    )

            # FTMS units need a node to resolve telemetry against.
            if unit.sensor_class == HyroxSensorClass.FTMS_MACHINE and not unit.node_id:
                errors.append(
                    f"Resource {unit.resource_id}: ftms_machine requires a node_id."
                )

            # Collect every occupied sensor address and flag reuse.
            for ep in (unit.start_endpoint, unit.finish_endpoint, unit.entry_gate):
                if ep is None:
                    continue
                addr = _endpoint_addr(ep)
                if addr in seen_endpoints:
                    errors.append(
                        f"Duplicate RFID read zone {addr[0]}/{addr[1]} used by "
                        f"{seen_endpoints[addr]} and {unit.resource_id}."
                    )
                else:
                    seen_endpoints[addr] = unit.resource_id

            if unit.sensor_class == HyroxSensorClass.FTMS_MACHINE and unit.node_id:
                if unit.node_id in seen_ftms_nodes:
                    errors.append(
                        f"Duplicate FTMS node {unit.node_id} used by "
                        f"{seen_ftms_nodes[unit.node_id]} and {unit.resource_id}."
                    )
                else:
                    seen_ftms_nodes[unit.node_id] = unit.resource_id

    return errors


def venue_readiness(
    venue: HyroxVenueConfig, profile: HyroxCourseProfile
) -> list[str]:
    """Completeness check: can this venue actually run the whole course?

    A stage's allowed_resource_groups are OPTIONS (a run may use treadmills OR a
    track). A stage is servable if at least one option exists in the venue and
    can produce the stage's target. Unservable stages are readiness problems,
    not structural config errors, so this is separate from validate_venue_config.
    """
    errors: list[str] = []
    group_by_id = {g.group_id: g for g in venue.resource_groups}
    seen_stages: set[HyroxStage] = set()

    for stage_def in profile.stages:
        if stage_def.stage in seen_stages:
            errors.append(f"Duplicate stage definition: {stage_def.stage.value}.")
        seen_stages.add(stage_def.stage)

        allowed_sensors = _TARGET_SENSORS[stage_def.target_type]
        existing = [group_by_id[g] for g in stage_def.allowed_resource_groups
                    if g in group_by_id]
        if not existing:
            errors.append(
                f"Stage {stage_def.stage.value} has no configured resource group."
            )
            continue
        servable = any(
            {u.sensor_class for u in group.units} & allowed_sensors
            for group in existing
        )
        if not servable:
            errors.append(
                f"Stage {stage_def.stage.value} ({stage_def.target_type.value}) has no "
                f"configured group whose sensors can produce it."
            )

    return errors
