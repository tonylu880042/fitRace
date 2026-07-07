from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class SpeedFtmsPayload(BaseModel):
    kind: Literal["speed"] = "speed"
    rssi: int | float | None = None
    instantaneous_speed: int | float | None = None
    total_distance: int | float | None = None
    instantaneous_power: int | None = None
    total_energy: int | None = None


class RowerFtmsPayload(BaseModel):
    kind: Literal["rower"] = "rower"
    rssi: int | float | None = None
    stroke_rate: int | float | None = None
    total_distance: int | float | None = None
    instantaneous_pace: int | float | None = None
    instantaneous_power: int | None = None
    total_energy: int | None = None


class UnknownFtmsPayload(BaseModel):
    kind: Literal["unknown"] = "unknown"
    rssi: int | float | None = None


TypedFtmsPayload = SpeedFtmsPayload | RowerFtmsPayload | UnknownFtmsPayload


class TelemetryData(BaseModel):
    node_id: str = Field(..., description="Unique identity of the telemetry stream")
    edge_node_id: Optional[str] = Field(
        None,
        description="Physical Edge Node that produced this telemetry stream",
    )
    mac_address: str | None = Field(None, description="BLE MAC address for the physical equipment")
    equipment_id: str = Field(..., description="Identity of the bound equipment")
    equipment_type: str = Field(
        ...,
        description="Type of the equipment (e.g. treadmill, fan_bike, rowing_machine, ski_erg)",
    )
    ftms_type: str | None = Field(None, description="Antenna board FTMS TYPE value such as TMILL, BIKE, ROWER, ELLIP")
    rssi: int | float | None = Field(None, description="Equipment BLE RSSI reported by the antenna board")
    instantaneous_speed_kph: float = Field(0.0, ge=0.0)
    cadence_rpm: int = Field(0, ge=0)
    pace_sec_per_500m: int | float | None = Field(None, ge=0)
    power_watts: int = Field(0, ge=0)
    heart_rate_bpm: int = Field(0, ge=0)
    distance_m: float = Field(0.0, ge=0.0)
    raw_total_distance_m: float | None = Field(
        None,
        ge=0.0,
        description="Raw cumulative distance reported by the equipment.",
    )
    delta_distance_m: float | None = Field(
        None,
        ge=0.0,
        description="Distance increment since the previous telemetry row for this equipment.",
    )
    total_energy_kcal: int | None = Field(None, ge=0)
    calories: float | None = Field(None, ge=0)
    raw_total_energy_kcal: float | None = Field(
        None,
        ge=0.0,
        description="Raw cumulative energy reported by the equipment.",
    )
    delta_energy_kcal: float | None = Field(
        None,
        ge=0.0,
        description="Energy increment since the previous telemetry row for this equipment.",
    )
    elapsed_time_ms: int = Field(0, ge=0)
    timestamp_epoch_ms: int = Field(..., description="Epoch timestamp in milliseconds")
    ftms_payload: TypedFtmsPayload | None = Field(
        None,
        description="Type-specific FTMS payload preserving device-specific fields",
    )
    raw_payload: dict[str, Any] | None = Field(
        None,
        description="Original JSON payload from the UART FTMS line",
    )


class FtmsDevice(BaseModel):
    address: str = Field(..., description="Bluetooth address or platform-specific BLE identifier")
    name: str | None = Field(None, description="Advertised BLE device name")
    rssi: int | None = Field(None, description="Received signal strength in dBm")
    service_uuids: list[str] = Field(default_factory=list)
    matched_services: list[str] = Field(default_factory=list)


class EquipmentBinding(BaseModel):
    node_id: str = Field(
        ...,
        description="Telemetry stream identity for this connected equipment",
    )
    equipment_id: str
    equipment_type: str
    ble_target: str = Field(..., description="BLE MAC address or advertised name")
    antenna_channel: str | None = Field(
        None,
        description="Antenna board UART channel assigned to this equipment stream",
    )

    @field_validator("node_id", "equipment_id", "equipment_type", "ble_target")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("value cannot be blank")
        return value.strip()


class AntennaChannelConfig(BaseModel):
    id: str = Field(..., description="Stable channel id used by equipment bindings")
    port: str = Field(..., description="Linux serial device path")
    uart: str | None = Field(None, description="Raspberry Pi UART number")
    tx_gpio: str | None = Field(None, description="Raspberry Pi TX GPIO pin")
    rx_gpio: str | None = Field(None, description="Raspberry Pi RX GPIO pin")
    baudrate: int = Field(115200, ge=9600)
    rtscts: bool = False
    dtoverlay: str | None = Field(None, description="Required /boot/config.txt overlay")

    @field_validator("id", "port")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("value cannot be blank")
        return value.strip()


class EdgeNodeConfig(BaseModel):
    node_id: str = Field(..., description="Physical Edge Node identity")
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    max_ftms_connections: int = Field(5, ge=1, le=10)
    available_channels: int = Field(2, ge=1)
    software_version: str | None = None
    antenna_protocol_version: str | None = None
    antenna_channels: list[AntennaChannelConfig] = Field(default_factory=list)
    antenna_auto_connect: bool = True
    equipment_bindings: list[EquipmentBinding] = Field(default_factory=list)

    @field_validator("node_id", "mqtt_host")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("value cannot be blank")
        return value.strip()

    @model_validator(mode="after")
    def validate_binding_limit(self):
        if len(self.equipment_bindings) > self.max_ftms_connections:
            raise ValueError("equipment_bindings cannot exceed max_ftms_connections")
        if len(self.equipment_bindings) > 10:
            raise ValueError("equipment_bindings cannot exceed 10 devices")
        return self
