import re
import subprocess
from typing import Callable

from pydantic import BaseModel


class WifiStatus(BaseModel):
    interface: str
    connected: bool
    ssid: str | None = None
    rssi_dbm: int | None = None
    quality_percent: int | None = None
    quality_level: str = "unknown"
    recommendation: str


class LinuxWifiStatusReader:
    def __init__(
        self,
        command_runner: Callable[..., subprocess.CompletedProcess] | None = None,
        wireless_path: str = "/proc/net/wireless",
    ):
        self._run = command_runner or subprocess.run
        self._wireless_path = wireless_path

    def read(self, interface: str = "wlan0") -> WifiStatus:
        interface = (interface or "wlan0").strip()
        if not interface:
            interface = "wlan0"

        iw_status = self._read_iw_status(interface)
        if iw_status:
            return iw_status

        proc_status = self._read_proc_wireless_status(interface)
        if proc_status:
            return proc_status

        return WifiStatus(
            interface=interface,
            connected=False,
            recommendation="No Wi-Fi RSSI available. Confirm the Edge Node is connected to fitRace26.",
        )

    def _read_iw_status(self, interface: str) -> WifiStatus | None:
        try:
            result = self._run(
                ["iw", "dev", interface, "link"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            return None

        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            return None
        if "Not connected" in output:
            return WifiStatus(
                interface=interface,
                connected=False,
                recommendation="Wi-Fi is not connected. Move the Edge Node closer to the AP or check AP provisioning.",
            )

        signal_match = re.search(r"signal:\s*(-?\d+)\s*dBm", output)
        if not signal_match:
            return None

        ssid_match = re.search(r"SSID:\s*(.+)", output)
        rssi = int(signal_match.group(1))
        return build_wifi_status(
            interface=interface,
            rssi_dbm=rssi,
            ssid=ssid_match.group(1).strip() if ssid_match else None,
        )

    def _read_proc_wireless_status(self, interface: str) -> WifiStatus | None:
        try:
            with open(self._wireless_path, "r", encoding="utf-8") as file:
                lines = file.readlines()
        except OSError:
            return None

        for line in lines:
            if not line.strip().startswith(f"{interface}:"):
                continue
            parts = line.replace(".", "").split()
            if len(parts) < 4:
                return None
            try:
                rssi = int(float(parts[3]))
            except ValueError:
                return None
            return build_wifi_status(interface=interface, rssi_dbm=rssi)
        return None


def build_wifi_status(
    interface: str,
    rssi_dbm: int,
    ssid: str | None = None,
) -> WifiStatus:
    quality_percent = max(0, min(100, int((rssi_dbm + 100) * 2)))
    quality_level = classify_rssi(rssi_dbm)
    return WifiStatus(
        interface=interface,
        connected=True,
        ssid=ssid,
        rssi_dbm=rssi_dbm,
        quality_percent=quality_percent,
        quality_level=quality_level,
        recommendation=recommend_for_rssi(rssi_dbm),
    )


def classify_rssi(rssi_dbm: int) -> str:
    if rssi_dbm >= -55:
        return "excellent"
    if rssi_dbm >= -67:
        return "good"
    if rssi_dbm >= -75:
        return "fair"
    if rssi_dbm >= -82:
        return "weak"
    return "poor"


def recommend_for_rssi(rssi_dbm: int) -> str:
    level = classify_rssi(rssi_dbm)
    if level in {"excellent", "good"}:
        return "Signal is suitable for live race operation."
    if level == "fair":
        return "Signal is usable, but avoid placing the Edge Node behind metal equipment."
    if level == "weak":
        return "Move the Edge Node closer to the AP or raise it above equipment frames."
    return "Signal is poor. Reposition the AP or Edge Node before running a race."
