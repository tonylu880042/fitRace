"""
protocol.py — BLE Central Board UART 訊息解析與指令組裝

純字串操作，不持有任何 serial 狀態。
所有函式為 pure function，可獨立單元測試。

UART 協議格式（ASCII，`;` 結尾，`\r\n` 換行）：
  指令  PC → Board：COMMAND[:PAYLOAD];\r\n
  回應  Board → PC：TYPE:PAYLOAD;\r\n
"""
import json
import re
from typing import Optional, Tuple

from .models import BootInfo, DeviceInfo, FTMSData, StatusInfo


# ── 指令組裝 ─────────────────────────────────────────────────

def build_ping() -> str:
    return "PING;\r\n"


def build_scan_start() -> str:
    return "SCAN:START;\r\n"


def build_scan_stop() -> str:
    return "SCAN:STOP;\r\n"


def build_connect(*macs: str) -> str:
    if not macs or len(macs) > 3:
        raise ValueError(f"需 1–3 個 MAC，收到 {len(macs)}")
    return f"CONNECT:{','.join(macs)};\r\n"


def build_disconnect_all() -> str:
    return "DISCONNECT:ALL;\r\n"


def build_report_interval(ms: int) -> str:
    if not (100 <= ms <= 10000):
        raise ValueError(f"REPORT ms 需在 100–10000，收到 {ms}")
    return f"REPORT:{ms};\r\n"


def build_status() -> str:
    return "STATUS;\r\n"


def build_version() -> str:
    return "VERSION;\r\n"


def build_reboot() -> str:
    return "REBOOT;\r\n"


# ── 訊息解析 ─────────────────────────────────────────────────

def parse_line(line: str) -> Optional[Tuple[str, object]]:
    """
    解析 Central Board 送出的一行訊息。

    回傳 (msg_type, parsed_object) 或 None（無法識別）。

    msg_type:
      'boot'    → BootInfo
      'device'  → DeviceInfo
      'ftms'    → FTMSData
      'status'  → StatusInfo
      'version' → str
      'ok'      → str（指令名稱，例如 "CONNECT"）
      'error'   → str（原始錯誤行）
    """
    line = line.strip().rstrip(";")
    if not line:
        return None

    # BOOT:HAS_LIST,count=N  or  BOOT:NO_LIST
    if line.startswith("BOOT:"):
        payload = line[5:]
        if payload.startswith("HAS_LIST"):
            m = re.search(r"count=(\d+)", payload)
            count = int(m.group(1)) if m else 0
            return "boot", BootInfo(has_list=True, count=count)
        return "boot", BootInfo(has_list=False)

    # DEVICE:MAC,RSSI,NAME,TYPE  （FTMS UUID 識別路徑，type = TMILL/BIKE/ROWER/ELLIP）
    if line.startswith("DEVICE:"):
        parts = line[7:].split(",", 3)
        if len(parts) < 4:
            return None
        try:
            return "device", DeviceInfo(
                mac=parts[0].strip(),
                rssi=int(parts[1].strip()),
                name=parts[2].strip(),
                device_type=parts[3].strip(),
            )
        except (ValueError, IndexError):
            return None

    # BLE_DEVICE:MAC,RSSI,NAME,RAW_HEX  （Vmax 製造商私有廣播路徑）
    # 韌體無法從 Manufacturer Data 直接確認 FTMS 設備類型，
    # 以 "UNKNOWN" 暫代，連線後可透過 GATT 確認。
    if line.startswith("BLE_DEVICE:"):
        parts = line[11:].split(",", 3)
        if len(parts) < 3:
            return None
        try:
            return "device", DeviceInfo(
                mac=parts[0].strip(),
                rssi=int(parts[1].strip()),
                name=parts[2].strip(),
                device_type=parts[3].strip() if len(parts) > 3 else "UNKNOWN",
            )
        except (ValueError, IndexError):
            return None

    # FTMS:MAC,TYPE,{json}
    if line.startswith("FTMS:"):
        try:
            return "ftms", _parse_ftms(line[5:])
        except Exception:
            return None

    # STATUS:STATE,conn=N,target=M
    if line.startswith("STATUS:"):
        try:
            return "status", _parse_status(line[7:])
        except Exception:
            return None

    # VERSION:x.y.z
    if line.startswith("VERSION:"):
        return "version", line[8:].strip()

    # COMMAND:OK
    if ":OK" in line:
        cmd = line.split(":")[0].strip()
        return "ok", cmd

    # ERROR or COMMAND:ERROR:reason
    if "ERROR" in line.upper():
        return "error", line

    return None


def _parse_ftms(payload: str) -> FTMSData:
    """解析 FTMS payload：MAC,TYPE,{json}"""
    mac, dev_type, json_part = payload.split(",", 2)
    d = json.loads(json_part)
    return FTMSData(
        mac=mac.strip(),
        device_type=dev_type.strip(),
        rssi=d.get("rssi", 0),
        instantaneous_speed=d.get("instantaneous_speed"),
        stroke_rate=d.get("stroke_rate"),
        instantaneous_pace=d.get("instantaneous_pace"),
        total_distance=d.get("total_distance"),
        instantaneous_power=d.get("instantaneous_power"),
        total_energy=d.get("total_energy"),
    )


def _parse_status(payload: str) -> StatusInfo:
    """解析 STATUS payload：STATE,conn=N,target=M"""
    parts = payload.split(",")
    state = parts[0].strip()
    conn_count = 0
    target_count = 0
    for p in parts[1:]:
        p = p.strip()
        if p.startswith("conn="):
            conn_count = int(p[5:])
        elif p.startswith("target="):
            target_count = int(p[7:])
    return StatusInfo(state=state, conn_count=conn_count, target_count=target_count)
