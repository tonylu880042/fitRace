"""Shared nmcli-based Wi-Fi management for edge node and hub server."""

import subprocess


class WifiError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _run_nmcli(args: list[str], timeout: int = 45) -> str:
    try:
        result = subprocess.run(
            ["nmcli", *args], capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        raise WifiError("nmcli not available on this system", status_code=501)
    except subprocess.TimeoutExpired:
        raise WifiError("nmcli timed out", status_code=504)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "nmcli failed"
        raise WifiError(detail)
    return result.stdout


def saved_wifi_profiles() -> set[str]:
    out = _run_nmcli(["-t", "-f", "NAME,TYPE", "connection", "show"], timeout=15)
    saved = set()
    for line in out.splitlines():
        name, _, ctype = line.rpartition(":")
        if ctype == "802-11-wireless":
            saved.add(name)
    return saved


def list_networks(interface: str = "wlan0") -> list[dict]:
    # SSID last so embedded colons survive the terse-format split
    out = _run_nmcli(
        ["-t", "-f", "IN-USE,SIGNAL,SECURITY,SSID", "device", "wifi", "list",
         "ifname", interface, "--rescan", "yes"]
    )
    saved = saved_wifi_profiles()
    networks: dict[str, dict] = {}
    for line in out.splitlines():
        parts = line.split(":", 3)
        if len(parts) != 4 or not parts[3]:
            continue
        in_use, signal, security, ssid = parts
        entry = {
            "ssid": ssid,
            "signal": int(signal) if signal.isdigit() else 0,
            "secured": security not in ("", "--"),
            "active": in_use.strip() == "*",
            "saved": ssid in saved,
        }
        current = networks.get(ssid)
        if not current or entry["signal"] > current["signal"] or entry["active"]:
            networks[ssid] = entry
    return sorted(networks.values(), key=lambda n: (-n["active"], -n["signal"]))


def connect(ssid: str, password: str | None = None, interface: str = "wlan0") -> str:
    if ssid in saved_wifi_profiles() and not password:
        return _run_nmcli(["connection", "up", "id", ssid], timeout=60).strip()
    args = ["device", "wifi", "connect", ssid]
    if password:
        args += ["password", password]
    args += ["ifname", interface]
    return _run_nmcli(args, timeout=60).strip()
