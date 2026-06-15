import os
import json
import logging
import subprocess
import socket
from abc import ABC, abstractmethod
from typing import Dict, Any

logger = logging.getLogger("hub_server.network_configurator")

class WiFiConfigurator(ABC):
    @abstractmethod
    def connect_wifi(self, ssid: str, password: str) -> bool:
        pass

    @abstractmethod
    def disconnect_wifi(self) -> None:
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        pass


class MockWiFiConfigurator(WiFiConfigurator):
    def __init__(self, state_file_path: str = "wifi_state.json"):
        self._state_file_path = state_file_path
        self._load_state()

    def _load_state(self):
        if os.path.exists(self._state_file_path):
            try:
                with open(self._state_file_path, "r") as f:
                    self._state = json.load(f)
            except Exception:
                self._default_state()
        else:
            self._default_state()

    def _default_state(self):
        self._state = {
            "mode": "AP",
            "ssid": "FitRaceStudio_Setup",
            "ip_address": "192.168.50.1",
            "connected": True
        }
        self._save_state()

    def _save_state(self):
        try:
            with open(self._state_file_path, "w") as f:
                json.dump(self._state, f)
        except Exception as e:
            logger.error(f"Failed to save mock wifi state: {e}")

    def connect_wifi(self, ssid: str, password: str) -> bool:
        logger.info(f"[Mock] Connecting to WiFi SSID: {ssid}")
        self._state = {
            "mode": "Station",
            "ssid": ssid,
            "ip_address": "192.168.0.100",
            "connected": True
        }
        self._save_state()
        return True

    def disconnect_wifi(self) -> None:
        logger.info("[Mock] Reverting to AP mode")
        self._default_state()

    def get_status(self) -> Dict[str, Any]:
        self._load_state()
        return self._state


class LinuxWiFiConfigurator(WiFiConfigurator):
    """
    Concrete implementation of WiFi configurator using nmcli (NetworkManager).
    Compatible with Raspberry Pi OS (Bookworm).
    """
    def __init__(self):
        pass

    def _run_cmd(self, cmd: list) -> str:
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=15.0
            )
            if result.returncode != 0:
                logger.error(f"Command {' '.join(cmd)} failed: {result.stderr.strip()}")
                return ""
            return result.stdout.strip()
        except Exception as e:
            logger.error(f"Exception running cmd {' '.join(cmd)}: {e}")
            return ""

    def connect_wifi(self, ssid: str, password: str) -> bool:
        logger.info(f"Connecting wlan0 to WiFi SSID: {ssid}...")
        # Use nmcli to connect.
        # First, ensure that wifi device is enabled.
        self._run_cmd(["nmcli", "radio", "wifi", "on"])
        
        # Connect to wifi. This command blocks until connection is established or timeout.
        cmd = ["nmcli", "device", "wifi", "connect", ssid, "password", password]
        output = self._run_cmd(cmd)
        if not output or "successfully activated" not in output.lower():
            logger.error(f"Failed to connect to WiFi SSID '{ssid}'. Output: {output}")
            return False
        
        logger.info(f"Successfully connected to WiFi SSID: {ssid}")
        return True

    def disconnect_wifi(self) -> None:
        logger.info("Disconnecting from WiFi and enabling AP mode...")
        # Turn off Station connections and start local Hotspot.
        # Raspberry Pi NetworkManager hotspot creation command:
        # nmcli device wifi hotspot [ssid <S>] [password <P>]
        hotspot_cmd = [
            "nmcli", "device", "wifi", "hotspot",
            "ssid", "FitRaceStudio_Setup",
            "password", "fitrace123"
        ]
        self._run_cmd(hotspot_cmd)

    def _get_local_ip(self) -> str:
        # Try to resolve IP of wlan0 interface
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Doesn't need to be reachable, just triggers OS routing to find interface IP
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def get_status(self) -> Dict[str, Any]:
        # Determine mode by checking active connection SSID
        # nmcli -t -f ACTIVE,SSID dev wifi
        ssid_output = self._run_cmd(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"])
        
        active_ssid = ""
        for line in ssid_output.split("\n"):
            if line.startswith("yes:"):
                active_ssid = line.split(":", 1)[1]
                break

        ip_address = self._get_local_ip()
        
        # If the active connection is a hotspot (FitRaceStudio_Setup), we are in AP mode
        if active_ssid == "FitRaceStudio_Setup":
            return {
                "mode": "AP",
                "ssid": "FitRaceStudio_Setup",
                "ip_address": ip_address if ip_address != "127.0.0.1" else "192.168.50.1",
                "connected": True
            }
        elif active_ssid:
            return {
                "mode": "Station",
                "ssid": active_ssid,
                "ip_address": ip_address,
                "connected": True
            }
        else:
            # Fallback if no active connection (let's assume AP for setup fallback)
            return {
                "mode": "AP",
                "ssid": "FitRaceStudio_Setup",
                "ip_address": "192.168.50.1",
                "connected": False
            }
