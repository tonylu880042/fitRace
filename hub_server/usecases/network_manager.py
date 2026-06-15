from typing import Dict, Any
from hub_server.infrastructure.network.configurator import WiFiConfigurator

class NetworkManager:
    def __init__(self, configurator: WiFiConfigurator):
        self._configurator = configurator

    def configure_wifi(self, ssid: str, password: str) -> bool:
        if not ssid:
            raise ValueError("SSID cannot be empty")
        return self._configurator.connect_wifi(ssid, password)

    def reset_to_ap(self) -> None:
        self._configurator.disconnect_wifi()

    def get_status(self) -> Dict[str, Any]:
        return self._configurator.get_status()
