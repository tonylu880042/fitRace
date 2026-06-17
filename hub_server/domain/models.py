from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RaceState(str, Enum):
    IDLE = "IDLE"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"


class RaceConfig(BaseModel):
    race_type: Literal["distance", "time", "calories", "max_power", "watts"] = Field(
        ..., description="Type of race"
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

    @model_validator(mode="after")
    def validate_required_target(self):
        if self.race_type in ("distance", "calories") and self.target_value <= 0:
            raise ValueError("target_value must be greater than 0 for distance and calories races")
        if self.race_type in ("time", "max_power", "watts") and self.duration_sec <= 0:
            raise ValueError("duration_sec must be greater than 0 for time, max_power, and watts races")
        return self


class EquipmentStreamStatus(BaseModel):
    node_id: str
    equipment_id: str | None = None
    equipment_type: str | None = None
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
    equipment_streams: list[EquipmentStreamStatus] = Field(default_factory=list)
