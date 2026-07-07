from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass(frozen=True)
class BootInfo:
    has_list: bool
    count: int = 0


@dataclass(frozen=True)
class DeviceInfo:
    mac: str
    rssi: int
    name: str
    device_type: str  # "TMILL" | "BIKE" | "ROWER" | "ELLIP" | "UNKNOWN"


@dataclass(frozen=True)
class FTMSData:
    mac: str
    device_type: str
    rssi: int
    timestamp: float = field(default_factory=time.time)
    # TMILL / BIKE / ELLIP
    instantaneous_speed: Optional[float] = None   # km/h (firmware pre-converted)
    # ROWER
    stroke_rate: Optional[float] = None           # strokes/min (firmware pre-converted)
    instantaneous_pace: Optional[int] = None      # s/500m
    # 共用
    total_distance: Optional[int] = None          # m
    instantaneous_power: Optional[int] = None     # W
    total_energy: Optional[int] = None            # kcal


@dataclass(frozen=True)
class TaggedFTMSData:
    """GatewayManager 統一分派的 FTMS 資料，附帶 gateway_id 識別來源板子。"""
    gateway_id: str
    mac: str
    device_type: str
    rssi: int
    timestamp: float = field(default_factory=time.time)
    instantaneous_speed: Optional[float] = None
    stroke_rate: Optional[float] = None
    instantaneous_pace: Optional[int] = None
    total_distance: Optional[int] = None
    instantaneous_power: Optional[int] = None
    total_energy: Optional[int] = None


@dataclass(frozen=True)
class StatusInfo:
    state: str        # "IDLE" | "SCAN" | "CONN" | "REPORT"
    conn_count: int
    target_count: int
