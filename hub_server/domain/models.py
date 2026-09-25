from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class RaceState(str, Enum):
    IDLE = "IDLE"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"


def _require_target_for_race_type(
    race_type: str, target_value: float, duration_sec: int
) -> None:
    """Shared by RaceConfig and RaceGroup: a target-based race_type
    (distance/calories) needs a positive target_value, and a duration-based
    one (time/max_power/watts) needs a positive duration_sec. Pulled out to
    one place so RaceGroup doesn't copy-paste RaceConfig's rule."""
    if race_type in ("distance", "calories") and target_value <= 0:
        raise ValueError(
            "target_value must be greater than 0 for distance and calories races"
        )
    if race_type in ("time", "max_power", "watts") and duration_sec <= 0:
        raise ValueError(
            "duration_sec must be greater than 0 for time, max_power, and watts races"
        )


class RaceGroup(BaseModel):
    """One equipment group inside a "mixed" RaceConfig -- e.g. treadmills
    racing 800 m while rowers race 500 m, all from the same start. Each
    group is otherwise a self-contained race (its own race_type/target),
    scoped down to the node_ids whose equipment_type falls in
    equipment_types. See RaceConfig.group_index_for/scoped_config, the pure
    methods that let every existing per-race_type usecase reuse this
    without any changes -- they just get handed a scoped_config()."""

    equipment_types: list[str] = Field(
        ...,
        min_length=1,
        description="Equipment types scored as this group, e.g. ['treadmill']",
    )
    race_type: Literal["distance", "time", "calories", "max_power", "watts"] = Field(
        ..., description="Type of race for this group ('mixed' is not allowed here)"
    )
    target_value: float = Field(
        0.0,
        ge=0.0,
        description="Target value for distance (m) or calories (kcal) based groups",
    )
    duration_sec: int = Field(
        0,
        ge=0,
        description="Target duration for time-based groups in seconds",
    )

    @field_validator("equipment_types", mode="before")
    @classmethod
    def _clean_equipment_types(cls, value):
        if not isinstance(value, list):
            return value
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("equipment_types entries must be strings")
            stripped = item.strip()
            if not stripped:
                raise ValueError("equipment_types entries must not be blank")
            if stripped == "unknown":
                raise ValueError('equipment_types entries must not be "unknown"')
            cleaned.append(stripped)
        return cleaned

    @model_validator(mode="after")
    def validate_no_duplicate_equipment_types(self):
        if len(set(self.equipment_types)) != len(self.equipment_types):
            raise ValueError(
                "equipment_types must not contain duplicates within a group"
            )
        return self

    @model_validator(mode="after")
    def validate_required_target(self):
        _require_target_for_race_type(
            self.race_type, self.target_value, self.duration_sec
        )
        return self


class RaceConfig(BaseModel):
    race_type: Literal[
        "distance", "time", "calories", "max_power", "watts", "mixed"
    ] = Field(..., description="Type of race")
    competition_mode: Literal["individual", "team", "relay"] = Field(
        "individual", description="Whether rankings are scored by athlete or team"
    )
    relay_legs: int | None = Field(
        None,
        ge=2,
        le=10,
        description="Number of relay legs (team size) for a relay race; "
        "required when competition_mode is relay, must be unset otherwise",
    )
    team_scoring_policy: Literal["average", "total"] = Field(
        "average",
        description="How team scores are aggregated when competition_mode is team",
    )
    team_completion_policy: Literal["aggregate", "all_members"] = Field(
        "aggregate",
        description="Whether target-based team races finish by aggregate progress or every member target completion",
    )
    target_value: float = Field(
        0.0,
        ge=0.0,
        description="Target value for distance (m) or calories (kcal) based race",
    )
    duration_sec: int = Field(
        0,
        ge=0,
        description="Target duration for time-based race in seconds",
    )
    groups: list[RaceGroup] = Field(
        default_factory=list,
        max_length=8,
        description="Equipment groups for a 'mixed' race_type; empty otherwise",
    )

    @model_validator(mode="after")
    def validate_required_target(self):
        # A mixed race carries its targets inside each group -- the
        # top-level target_value/duration_sec are unused and unvalidated.
        if self.race_type == "mixed":
            return self
        _require_target_for_race_type(
            self.race_type, self.target_value, self.duration_sec
        )
        return self

    @model_validator(mode="after")
    def validate_groups(self):
        if self.race_type == "mixed":
            if len(self.groups) < 2:
                raise ValueError("race_type 'mixed' requires at least 2 groups")
            if self.competition_mode != "individual":
                raise ValueError(
                    "race_type 'mixed' requires competition_mode 'individual'"
                )
        elif self.groups:
            raise ValueError("groups are only allowed when race_type is 'mixed'")

        seen_equipment_types: set[str] = set()
        for group in self.groups:
            for equipment_type in group.equipment_types:
                if equipment_type in seen_equipment_types:
                    raise ValueError(
                        f"equipment type {equipment_type!r} is assigned to "
                        "more than one group"
                    )
                seen_equipment_types.add(equipment_type)
        return self

    def group_index_for(self, equipment_type: str | None) -> int | None:
        """Index of the group that scores `equipment_type`, or None if this
        isn't a mixed race, the type is None/"unknown", or no group claims
        it. Pure lookup -- no I/O, no mutation."""
        if (
            self.race_type != "mixed"
            or not equipment_type
            or equipment_type == "unknown"
        ):
            return None
        for index, group in enumerate(self.groups):
            if equipment_type in group.equipment_types:
                return index
        return None

    def scoped_config(self, group_index: int) -> "RaceConfig":
        """A plain individual RaceConfig carrying group_index's own
        race_type/target_value/duration_sec and no groups -- the reuse
        mechanism that lets existing per-race_type code (progress/finish
        computation, readiness, event engine) stay untouched: it just runs
        against this scoped config instead of the mixed top-level one."""
        group = self.groups[group_index]
        return RaceConfig(
            race_type=group.race_type,
            competition_mode="individual",
            target_value=group.target_value,
            duration_sec=group.duration_sec,
        )

    @model_validator(mode="after")
    def validate_relay_legs(self):
        if self.competition_mode == "relay":
            if self.race_type != "distance":
                raise ValueError("Relay races must use race_type distance")
            if self.relay_legs is None:
                raise ValueError(
                    "relay_legs is required when competition_mode is relay"
                )
        elif self.relay_legs is not None:
            # Non-relay races never carry a leg count -- force it back to
            # None rather than reject, so a stale value left over from
            # switching competition_mode away from relay doesn't need to be
            # scrubbed by every caller.
            self.relay_legs = None
        return self


class EquipmentStreamStatus(BaseModel):
    node_id: str
    equipment_id: str | None = None
    equipment_type: str | None = None
    ble_target: str | None = None
    mac_address: str | None = None
    status: str = "unknown"
    antenna_channel: str | None = None
    rssi: int | None = None
    last_telemetry_epoch_ms: int | None = None
    error_code: str | None = None


class EdgeNodeStatus(BaseModel):
    edge_node_id: str
    hostname: str | None = None
    ip: str | None = None
    status: str = "online"
    firmware_version: str | None = None
    software_version: str | None = None
    antenna_protocol_version: str | None = None
    max_ftms_connections: int = 5
    available_channels: int = 2
    last_seen_epoch_ms: int
    # The edge's own last_seen_epoch_ms as it reported it, kept only for
    # diagnostics -- last_seen_epoch_ms above always holds the hub's own
    # receipt time now, so an edge with a wrong clock can't be judged
    # offline early (fast clock) or never (slow clock). See NodeRegistry.
    edge_reported_epoch_ms: int | None = None
    # hub_now_ms - edge_reported_epoch_ms, taken as the minimum over the
    # last N heartbeat samples (network delay only ever makes a sample
    # larger, never smaller). 0 when no sample has been recorded yet.
    clock_offset_ms: int = 0
    equipment_streams: list[EquipmentStreamStatus] = Field(default_factory=list)
