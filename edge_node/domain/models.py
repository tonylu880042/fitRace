from pydantic import BaseModel, Field


class TelemetryData(BaseModel):
    node_id: str = Field(..., description="Unique identity of the Edge Node")
    equipment_id: str = Field(..., description="Identity of the bound equipment")
    equipment_type: str = Field(
        ...,
        description="Type of the equipment (e.g. treadmill, fan_bike, rowing_machine, ski_erg)",
    )
    instantaneous_speed_kph: float = Field(0.0, ge=0.0)
    cadence_rpm: int = Field(0, ge=0)
    power_watts: int = Field(0, ge=0)
    heart_rate_bpm: int = Field(0, ge=0)
    distance_m: float = Field(0.0, ge=0.0)
    elapsed_time_ms: int = Field(0, ge=0)
    timestamp_epoch_ms: int = Field(..., description="Epoch timestamp in milliseconds")

    def to_dict(self) -> dict:
        return self.model_dump()
