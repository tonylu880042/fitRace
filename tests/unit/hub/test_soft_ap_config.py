import os
import pytest
from fastapi.testclient import TestClient
from hub_server.infrastructure.fastapi.app import app
from hub_server.usecases.network_manager import NetworkManager
from hub_server.infrastructure.network.configurator import MockWiFiConfigurator

client = TestClient(app)

TEST_STATE_FILE = "wifi_state_test.json"

def test_network_manager_logic():
    # Clean up state file if exists
    if os.path.exists(TEST_STATE_FILE):
        os.remove(TEST_STATE_FILE)

    configurator = MockWiFiConfigurator(TEST_STATE_FILE)
    manager = NetworkManager(configurator)

    try:
        # 1. Initial State (unconfigured / AP mode)
        status = manager.get_status()
        assert status["mode"] == "AP"
        assert status["ssid"] == "FitRaceStudio_Setup"
        assert status["ip_address"] == "192.168.50.1"
        assert status["connected"] is True

        # 2. Configure WiFi -> Transition to Station mode
        success = manager.configure_wifi("Venue_WiFi", "password123")
        assert success is True

        status = manager.get_status()
        assert status["mode"] == "Station"
        assert status["ssid"] == "Venue_WiFi"
        assert status["ip_address"] == "192.168.0.100"  # Simulated IP
        assert status["connected"] is True

        # 3. Reset -> Revert to AP mode
        manager.reset_to_ap()
        status = manager.get_status()
        assert status["mode"] == "AP"
        assert status["ssid"] == "FitRaceStudio_Setup"
        assert status["ip_address"] == "192.168.50.1"
    finally:
        # Tear down
        if os.path.exists(TEST_STATE_FILE):
            os.remove(TEST_STATE_FILE)


def test_wifi_setup_api_endpoints():
    # Ensure starting in AP mode
    client.post("/api/setup/reset")

    # Test GET /api/setup/status
    res = client.get("/api/setup/status")
    assert res.status_code == 200
    data = res.json()
    assert "mode" in data
    assert "ssid" in data
    assert "ip_address" in data

    # Test POST /api/setup/wifi
    res = client.post("/api/setup/wifi", json={"ssid": "API_WiFi", "password": "securepassword"})
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "Station"
    assert data["ssid"] == "API_WiFi"

    # Test POST /api/setup/wifi validation failure
    res = client.post("/api/setup/wifi", json={"password": "securepassword"})  # Missing ssid
    assert res.status_code == 422

    # Test POST /api/setup/reset
    res = client.post("/api/setup/reset")
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "AP"
    assert data["ssid"] == "FitRaceStudio_Setup"
