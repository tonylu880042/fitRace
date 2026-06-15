import asyncio
import time
import random
from edge_node.domain.models import TelemetryData


async def generate_mock_telemetry(
    node_id: str, equipment_id: str, equipment_type: str, interval_sec: float = 0.5
):
    """
    Asynchronous generator that yields simulated telemetry data for aerobic equipment.
    """
    elapsed_time_ms = 0
    distance_m = 0.0

    # Establish baseline values based on equipment type
    if equipment_type == "treadmill":
        base_speed = 10.0  # kph
        base_cadence = 160  # strides/min
        base_power = 180  # watts
        base_hr = 130  # bpm
    elif equipment_type == "fan_bike":
        base_speed = 25.0
        base_cadence = 75
        base_power = 150
        base_hr = 125
    elif equipment_type == "rowing_machine":
        base_speed = 12.0
        base_cadence = 28
        base_power = 200
        base_hr = 140
    else:  # e.g., ski_erg or fallback
        base_speed = 15.0
        base_cadence = 40
        base_power = 160
        base_hr = 135

    while True:
        await asyncio.sleep(interval_sec)

        # Add slight random variations
        speed_var = random.uniform(-1.0, 1.0)
        cadence_var = random.randint(-5, 5)
        power_var = random.randint(-20, 20)
        hr_var = random.randint(-3, 3)

        speed = max(1.0, base_speed + speed_var)
        cadence = max(0, base_cadence + cadence_var)
        power = max(0, base_power + power_var)
        hr = max(40, base_hr + hr_var)

        # Calculate increment in distance: meters = (speed in km/h / 3.6) * elapsed_seconds
        distance_increment = (speed / 3.6) * interval_sec
        distance_m += distance_increment
        elapsed_time_ms += int(interval_sec * 1000)

        yield TelemetryData(
            node_id=node_id,
            equipment_id=equipment_id,
            equipment_type=equipment_type,
            instantaneous_speed_kph=round(speed, 2),
            cadence_rpm=cadence,
            power_watts=power,
            heart_rate_bpm=hr,
            distance_m=round(distance_m, 2),
            elapsed_time_ms=elapsed_time_ms,
            timestamp_epoch_ms=int(time.time() * 1000),
        )
