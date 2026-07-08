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


def _endpoint_addr(ep: HyroxEndpointSensor) -> tuple[str, str]:
    return (ep.node_id, ep.antenna_id)


def validate_venue_config(
    venue: HyroxVenueConfig, profile: HyroxCourseProfile
) -> list[str]:
    """Return a list of human-readable config errors; empty means valid.

    Covers the Phase 1 rules: duplicate sensors, missing/duplicate RFID
    endpoint pairs, unique ids, unknown resource-group references, and
    target/resource-class mismatch.
    """
    errors: list[str] = []

    # --- Unique group ids and resource ids ---
    seen_groups: set[str] = set()
    for group in venue.resource_groups:
        if group.group_id in seen_groups:
            errors.append(f"Duplicate resource group id: {group.group_id}.")
        seen_groups.add(group.group_id)

    seen_resources: set[str] = set()
    # Every physical sensor address (node_id, antenna_id) must be unique across
    # the whole venue; likewise an FTMS node may back only one resource unit.
    seen_endpoints: dict[tuple[str, str], str] = {}
    seen_ftms_nodes: dict[str, str] = {}

    for group in venue.resource_groups:
        if not group.stage_candidates:
            errors.append(f"Resource group {group.group_id} has no stage candidates.")
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

    # --- Stage definitions: unique, resolvable groups, target/sensor match ---
    group_by_id = {g.group_id: g for g in venue.resource_groups}
    seen_stages: set[HyroxStage] = set()
    for stage_def in profile.stages:
        if stage_def.stage in seen_stages:
            errors.append(f"Duplicate stage definition: {stage_def.stage.value}.")
        seen_stages.add(stage_def.stage)

        allowed_sensors = _TARGET_SENSORS[stage_def.target_type]
        for group_id in stage_def.allowed_resource_groups:
            group = group_by_id.get(group_id)
            if group is None:
                errors.append(
                    f"Stage {stage_def.stage.value} references unknown resource group {group_id}."
                )
                continue
            unit_classes = {u.sensor_class for u in group.units}
            if not (unit_classes & allowed_sensors):
                errors.append(
                    f"Stage {stage_def.stage.value} ({stage_def.target_type.value}) allows group "
                    f"{group_id}, whose sensors {sorted(c.value for c in unit_classes)} cannot "
                    f"produce {stage_def.target_type.value}."
                )

    return errors
