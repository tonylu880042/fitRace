import json
import re
from typing import Any

FTMS_EQUIPMENT_TYPES = {
    "TMILL": "treadmill",
    "BIKE": "fan_bike",
    "ROWER": "rowing_machine",
    "ELLIP": "elliptical",
    "UNKNOWN": "unknown",
}
SPEED_BASED_FTMS_TYPES = {"TMILL", "BIKE", "ELLIP"}


def build_ping() -> str:
    return "PING;\r\n"


def build_scan_start() -> str:
    return "SCAN:START;\r\n"


def build_scan_stop() -> str:
    return "SCAN:STOP;\r\n"


def build_connect(macs: list[str]) -> str:
    if not macs:
        raise ValueError("connect requires at least one device identifier")
    return f"CONNECT:{','.join(macs)};\r\n"


def build_disconnect_all() -> str:
    return "DISCONNECT:ALL;\r\n"


def build_report_interval(ms: int) -> str:
    if not 100 <= ms <= 10000:
        raise ValueError("report interval must be between 100 and 10000 ms")
    return f"REPORT:{ms};\r\n"


def build_status() -> str:
    return "STATUS;\r\n"


def build_version() -> str:
    return "VERSION;\r\n"


def build_reboot() -> str:
    return "REBOOT;\r\n"


def normalize_raw_command(command: str) -> str:
    command = command.strip()
    if not command:
        raise ValueError("raw command cannot be blank")
    if not command.endswith(";"):
        command = f"{command};"
    return f"{command}\r\n"


def parse_line(line: str) -> dict[str, Any]:
    raw = line.strip()
    value = raw.rstrip(";")
    if not value:
        return {"type": "empty", "raw": raw}

    if value.startswith("BOOT:"):
        payload = value[5:]
        if payload.startswith("HAS_LIST"):
            match = re.search(r"count=(\d+)", payload)
            return {
                "type": "boot",
                "has_list": True,
                "count": int(match.group(1)) if match else 0,
                "raw": raw,
            }
        return {"type": "boot", "has_list": False, "count": 0, "raw": raw}

    if value.startswith("DEVICE:"):
        parts = value[7:].split(",", 3)
        if len(parts) >= 4:
            return {
                "type": "device",
                "address": parts[0].strip(),
                "rssi": _parse_int(parts[1]),
                "name": parts[2].strip(),
                "device_type": parts[3].strip(),
                "raw": raw,
            }

    if value.startswith("BLE_DEVICE:"):
        parts = value[11:].split(",", 3)
        if len(parts) >= 3:
            return {
                "type": "device",
                "address": parts[0].strip(),
                "rssi": _parse_int(parts[1]),
                "name": parts[2].strip(),
                "device_type": parts[3].strip() if len(parts) > 3 else "UNKNOWN",
                "raw": raw,
            }

    if value.startswith("FTMS:"):
        parts = value[5:].split(",", 2)
        if len(parts) == 3:
            payload = _parse_json_object(parts[2])
            if payload is None:
                return {
                    "type": "invalid",
                    "message_type": "telemetry",
                    "reason": "invalid_json",
                    "raw": raw,
                }
            device_type = parts[1].strip().upper()
            parsed = {
                "type": "telemetry",
                "address": parts[0].strip(),
                "device_type": device_type,
                "payload": payload,
                "raw": raw,
            }
            parsed.update(_normalize_ftms_payload(device_type, payload))
            return parsed

    if value.startswith("STATUS:"):
        payload = value[7:]
        parts = payload.split(",")
        parsed = {"type": "status", "state": parts[0].strip(), "raw": raw}
        for part in parts[1:]:
            if "/" in part:
                connected, separator, target = part.strip().partition("/")
                if separator:
                    parsed["connected"] = _parse_int(connected)
                    parsed["target"] = _parse_int(target)
                continue
            key, separator, val = part.strip().partition("=")
            if separator:
                normalized_key = "connected" if key == "conn" else key
                parsed[normalized_key] = _parse_int(val)
        return parsed

    if value.startswith("VERSION:"):
        return {"type": "version", "version": value[8:].strip(), "raw": raw}

    if ":OK" in value:
        return {"type": "ok", "command": value.split(":", 1)[0].strip(), "raw": raw}

    if "ERROR" in value.upper():
        return {"type": "error", "message": value, "raw": raw}

    return {"type": "raw", "raw": raw}


def _parse_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return None


def _parse_json_object(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_ftms_payload(device_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "equipment_type": FTMS_EQUIPMENT_TYPES.get(device_type, "unknown"),
        "ftms_type": device_type,
        "rssi": _number_or_none(payload.get("rssi")),
        "instantaneous_speed_kph": _number_or_default(
            payload.get("instantaneous_speed"), 0.0
        ),
        "cadence_rpm": _number_or_default(payload.get("stroke_rate"), 0.0),
        "pace_sec_per_500m": _number_or_none(payload.get("instantaneous_pace")),
        "distance_m": _number_or_default(payload.get("total_distance"), 0.0),
        "power_watts": _int_or_default(payload.get("instantaneous_power"), 0),
        "total_energy_kcal": _int_or_default(payload.get("total_energy"), 0),
        "raw_payload": payload,
    }
    normalized["ftms_payload"] = _typed_ftms_payload(device_type, payload)
    return normalized


def _typed_ftms_payload(device_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if device_type in SPEED_BASED_FTMS_TYPES:
        return {
            "kind": "speed",
            "rssi": _number_or_none(payload.get("rssi")),
            "instantaneous_speed": _number_or_none(payload.get("instantaneous_speed")),
            "total_distance": _number_or_none(payload.get("total_distance")),
            "instantaneous_power": _int_or_none(payload.get("instantaneous_power")),
            "total_energy": _int_or_none(payload.get("total_energy")),
        }
    if device_type == "ROWER":
        return {
            "kind": "rower",
            "rssi": _number_or_none(payload.get("rssi")),
            "stroke_rate": _number_or_none(payload.get("stroke_rate")),
            "total_distance": _number_or_none(payload.get("total_distance")),
            "instantaneous_pace": _number_or_none(payload.get("instantaneous_pace")),
            "instantaneous_power": _int_or_none(payload.get("instantaneous_power")),
            "total_energy": _int_or_none(payload.get("total_energy")),
        }
    return {
        "kind": "unknown",
        "rssi": _number_or_none(payload.get("rssi")),
    }


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number_or_default(value: Any, default: float) -> int | float:
    parsed = _number_or_none(value)
    return default if parsed is None else parsed


def _int_or_default(value: Any, default: int) -> int:
    parsed = _number_or_none(value)
    return default if parsed is None else int(parsed)


def _int_or_none(value: Any) -> int | None:
    parsed = _number_or_none(value)
    return None if parsed is None else int(parsed)
