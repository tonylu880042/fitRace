from enum import Enum
from pydantic import BaseModel, Field


class RaceState(str, Enum):
    IDLE = "IDLE"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"


class RaceConfig(BaseModel):
    race_type: str = Field(..., description="Type of race: 'distance', 'time', 'calories', or 'max_power'")
    target_value: float = Field(
        0.0, description="Target value for distance (m) or calories (kcal) based race"
    )
    duration_sec: int = Field(
        0, description="Target duration for time-based race in seconds"
    )


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
