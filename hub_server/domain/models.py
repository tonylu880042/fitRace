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
