from fastapi.testclient import TestClient

from edge_node.domain.models import FtmsDevice
from edge_node.infrastructure.fastapi import app as edge_app_module


class FakeScanner:
    async def scan(self, timeout_sec: float, adapter: str | None = None):
        return [
            FtmsDevice(
                address="AA:BB:CC:DD:EE:01",
                name="FTMS Bike",
                rssi=-45,
                service_uuids=["00001826-0000-1000-8000-00805f9b34fb"],
                matched_services=["00001826-0000-1000-8000-00805f9b34fb"],
            ),
            FtmsDevice(
                address="AA:BB:CC:DD:EE:02",
                name="Non FTMS Device",
                rssi=-70,
                service_uuids=["0000180a-0000-1000-8000-00805f9b34fb"],
            ),
        ]


def test_edge_health_endpoint():
    client = TestClient(edge_app_module.app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "role": "edge"}


def test_edge_setup_page_includes_ftms_scan_controls():
    client = TestClient(edge_app_module.app)

    response = client.get("/")

    assert response.status_code == 200
    assert "FitRace Edge Node" in response.text
    assert "language-select" in response.text
    assert "Deutsch (Schweiz)" in response.text
    assert "Wi-Fi Signal" in response.text
    assert "wifi-icon" in response.text
    assert "極佳 Wi-Fi" in response.text
    assert 'id="wifi-rssi"' not in response.text
    assert "Scan FTMS Devices" in response.text
    assert "hci1 USB dongle" in response.text


def test_edge_wifi_status_endpoint(monkeypatch):
    class FakeWifiStatusReader:
        def read(self, interface: str = "wlan0"):
            return edge_app_module.WifiStatus(
                interface=interface,
                connected=True,
                ssid="fitRace26",
                rssi_dbm=-58,
                quality_percent=84,
                quality_level="good",
                recommendation="Signal is suitable for live race operation.",
            )

    monkeypatch.setattr(edge_app_module, "wifi_status_reader", FakeWifiStatusReader())
    client = TestClient(edge_app_module.app)

    response = client.get("/api/wifi/status?interface=wlan0")

    assert response.status_code == 200
    payload = response.json()
    assert payload["interface"] == "wlan0"
    assert payload["ssid"] == "fitRace26"
    assert payload["rssi_dbm"] == -58
    assert payload["quality_level"] == "good"


def test_edge_power_shutdown_requires_confirmation_and_defaults_to_dry_run():
    client = TestClient(edge_app_module.app)

    missing_confirmation = client.post("/api/system/power/shutdown", json={})
    assert missing_confirmation.status_code == 409

    response = client.post(
        "/api/system/power/shutdown",
        json={"confirmation": "SHUTDOWN"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dry_run"] is True
    assert payload["executed"] is False
    assert payload["command"] == ["sudo", "systemctl", "poweroff"]


def test_edge_ble_scan_endpoint_filters_ftms_devices(monkeypatch):
    monkeypatch.setattr(edge_app_module, "ftms_scanner", FakeScanner())
    client = TestClient(edge_app_module.app)

    response = client.get("/api/ble/scan?adapter=hci1&timeout_sec=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["adapter"] == "hci1"
    assert payload["timeout_sec"] == 2
    assert len(payload["devices"]) == 1
    assert payload["devices"][0]["name"] == "FTMS Bike"


def test_edge_ble_scan_endpoint_can_include_all_devices(monkeypatch):
    monkeypatch.setattr(edge_app_module, "ftms_scanner", FakeScanner())
    client = TestClient(edge_app_module.app)

    response = client.get("/api/ble/scan?include_all=true")

    assert response.status_code == 200
    assert len(response.json()["devices"]) == 2
