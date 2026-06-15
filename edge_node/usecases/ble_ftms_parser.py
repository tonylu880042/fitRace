from typing import Dict, Any

def parse_indoor_bike(data: bytes) -> Dict[str, Any]:
    if len(data) < 4:
        raise ValueError("Indoor bike data too short")

    flags = int.from_bytes(data[0:2], byteorder="little")
    offset = 2

    # Speed is mandatory (16-bit uint, unit 0.01 km/h)
    speed_raw = int.from_bytes(data[offset : offset + 2], byteorder="little")
    speed_kph = speed_raw / 100.0
    offset += 2

    res = {"speed_kph": speed_kph}

    # Average Speed (16-bit uint, 0.01 km/h)
    if flags & (1 << 1):
        offset += 2

    # Instantaneous Cadence (16-bit uint, 0.5 rpm)
    if flags & (1 << 2):
        cadence_raw = int.from_bytes(data[offset : offset + 2], byteorder="little")
        res["cadence_rpm"] = int(cadence_raw * 0.5)
        offset += 2

    # Average Cadence (16-bit uint, 0.5 rpm)
    if flags & (1 << 3):
        offset += 2

    # Total Distance (24-bit uint, 1 m)
    if flags & (1 << 4):
        dist_raw = int.from_bytes(data[offset : offset + 3], byteorder="little")
        res["distance_m"] = float(dist_raw)
        offset += 3

    # Resistance Level (16-bit int)
    if flags & (1 << 5):
        offset += 2

    # Instantaneous Power (16-bit int, 1 W)
    if flags & (1 << 6):
        power_raw = int.from_bytes(
            data[offset : offset + 2], byteorder="little", signed=True
        )
        res["power_watts"] = power_raw
        offset += 2

    # Average Power (16-bit int)
    if flags & (1 << 7):
        offset += 2

    # Expended Energy (5 bytes: 2 bytes energy, 2 bytes hr, 1 byte min)
    if flags & (1 << 8):
        offset += 5

    # Heart Rate (8-bit uint)
    if flags & (1 << 9):
        res["heart_rate_bpm"] = int(data[offset])
        offset += 1

    # Metabolic Equivalent (8-bit uint)
    if flags & (1 << 10):
        offset += 1

    # Elapsed Time (16-bit uint)
    if flags & (1 << 11):
        time_raw = int.from_bytes(data[offset : offset + 2], byteorder="little")
        res["elapsed_time_sec"] = time_raw
        offset += 2

    return res


def parse_treadmill(data: bytes) -> Dict[str, Any]:
    if len(data) < 4:
        raise ValueError("Treadmill data too short")

    flags = int.from_bytes(data[0:2], byteorder="little")
    offset = 2

    # Speed is mandatory (16-bit uint, unit 0.01 km/h)
    speed_raw = int.from_bytes(data[offset : offset + 2], byteorder="little")
    speed_kph = speed_raw / 100.0
    offset += 2

    res = {"speed_kph": speed_kph}

    # Average Speed (16-bit uint, 0.01 km/h)
    if flags & (1 << 1):
        offset += 2

    # Total Distance (24-bit uint, 1 m)
    if flags & (1 << 2):
        dist_raw = int.from_bytes(data[offset : offset + 3], byteorder="little")
        res["distance_m"] = float(dist_raw)
        offset += 3

    # Inclination and Ramp Angle (2 bytes inc, 2 bytes ramp)
    if flags & (1 << 3):
        offset += 4

    # Elevation Gain (2 bytes positive, 2 bytes negative)
    if flags & (1 << 4):
        offset += 4

    # Instantaneous Pace (8-bit uint, 0.1 km/min)
    if flags & (1 << 5):
        offset += 1

    # Average Pace (8-bit uint, 0.1 km/min)
    if flags & (1 << 6):
        offset += 1

    # Expended Energy (5 bytes)
    if flags & (1 << 7):
        offset += 5

    # Heart Rate (8-bit uint)
    if flags & (1 << 8):
        res["heart_rate_bpm"] = int(data[offset])
        offset += 1

    # Metabolic Equivalent (8-bit uint)
    if flags & (1 << 9):
        offset += 1

    # Elapsed Time (16-bit uint)
    if flags & (1 << 10):
        time_raw = int.from_bytes(data[offset : offset + 2], byteorder="little")
        res["elapsed_time_sec"] = time_raw
        offset += 2

    return res


def parse_rower(data: bytes) -> Dict[str, Any]:
    # Rower mandatory: Stroke Rate (8-bit, 0.5 rpm) and Stroke Count (16-bit uint)
    if len(data) < 5:
        raise ValueError("Rower data too short")

    flags = int.from_bytes(data[0:2], byteorder="little")
    offset = 2

    # Stroke Rate (8-bit uint, unit 0.5 rpm)
    stroke_rate_raw = data[offset]
    cadence_rpm = int(stroke_rate_raw * 0.5)
    offset += 1

    # Stroke Count (16-bit uint)
    stroke_count = int.from_bytes(data[offset : offset + 2], byteorder="little")
    offset += 2

    res = {"cadence_rpm": cadence_rpm, "stroke_count": stroke_count}

    # Average Stroke Rate (8-bit uint)
    if flags & (1 << 1):
        offset += 1

    # Total Distance (24-bit uint, 1 m)
    if flags & (1 << 2):
        dist_raw = int.from_bytes(data[offset : offset + 3], byteorder="little")
        res["distance_m"] = float(dist_raw)
        offset += 3

    # Instantaneous Pace (16-bit uint, 1 s / 500m)
    if flags & (1 << 3):
        # We can map pace to speed if needed, but for now we just parse it.
        # pace_raw = int.from_bytes(data[offset:offset+2], byteorder="little")
        # speed_kph = 1800 / pace_raw (approx)
        offset += 2

    # Average Pace (16-bit uint)
    if flags & (1 << 4):
        offset += 2

    # Instantaneous Power (16-bit int, 1 W)
    if flags & (1 << 5):
        power_raw = int.from_bytes(
            data[offset : offset + 2], byteorder="little", signed=True
        )
        res["power_watts"] = power_raw
        offset += 2

    # Average Power (16-bit int)
    if flags & (1 << 6):
        offset += 2

    # Resistance Level (16-bit int)
    if flags & (1 << 7):
        offset += 2

    # Expended Energy (5 bytes)
    if flags & (1 << 8):
        offset += 5

    # Heart Rate (8-bit uint)
    if flags & (1 << 9):
        res["heart_rate_bpm"] = int(data[offset])
        offset += 1

    # Metabolic Equivalent (8-bit uint)
    if flags & (1 << 10):
        offset += 1

    # Elapsed Time (16-bit uint)
    if flags & (1 << 11):
        time_raw = int.from_bytes(data[offset : offset + 2], byteorder="little")
        res["elapsed_time_sec"] = time_raw
        offset += 2

    return res


def parse_ftms(uuid: str, data: bytes) -> Dict[str, Any]:
    # Normalize UUID to standard lower case
    uuid_str = uuid.lower()
    if "2ad2" in uuid_str:
        return parse_indoor_bike(data)
    elif "2acd" in uuid_str:
        return parse_treadmill(data)
    elif "2ad1" in uuid_str:
        return parse_rower(data)
    else:
        raise ValueError(f"Unsupported FTMS Characteristic UUID: {uuid}")
