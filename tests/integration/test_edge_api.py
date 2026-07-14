from fastapi.testclient import TestClient
import json

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


def test_edge_setup_page_includes_uart_antenna_controls_without_ble_scan_panel():
    client = TestClient(edge_app_module.app)

    response = client.get("/")

    assert response.status_code == 200
    assert "FitRace Edge Node" in response.text
    assert "language-select" in response.text
    assert "Deutsch (Schweiz)" not in response.text
    assert "繁體中文" in response.text
    assert "Wi-Fi Signal" in response.text
    assert "wifi-icon" in response.text
    assert "極佳 Wi-Fi" in response.text
    assert 'id="wifi-rssi"' not in response.text
    assert "Scan FTMS Devices" not in response.text
    assert "hci1 USB dongle" not in response.text
    assert "BLE Scan" not in response.text
    assert "UART Antenna Control" in response.text
    assert 'id="antenna-channel"' in response.text
    assert 'data-command="connect"' in response.text
    assert "/api/antenna/config" in response.text
    assert "/api/antenna/command" in response.text
    assert "/api/config" in response.text
    assert "Equipment Bindings" in response.text
    assert 'class="binding-target-input readonly-input"' in response.text
    assert "readonly" in response.text
    assert "UART Response" in response.text
    assert "Runtime Monitor" in response.text
    assert 'id="monitor-grid"' in response.text
    assert "Fixed equipment telemetry slots" in response.text
    assert "monitor-card" in response.text
    assert "/api/monitor/events" in response.text
    assert 'id="antenna-reconnect-configured-btn"' in response.text
    assert "/api/antenna/reconnect-configured" in response.text
    assert 'id="antenna-report-interval" type="number" min="100" max="10000" value="250"' in response.text
    assert "ANTENNA_DEFAULT_REPORT_INTERVAL_MS = 250" in response.text
    assert "MONITOR_REFRESH_MS = 250" in response.text
    assert "MONITOR_LIVE_WINDOW_MS = 3000" in response.text
    assert "MONITOR_SMOOTHING_MS = 180" in response.text
    assert "requestAnimationFrame(animateMonitorEquipment)" in response.text
    assert '"monitor.stale": "Idle (no data)"' in response.text
    assert '"monitor.stale": "閒置（無數據）"' in response.text
    assert "function monitorNowEpochMs()" in response.text
    assert "monitorServerNowEpochMs" in response.text
    assert "monitorNowEpochMs() - timestamp" in response.text
    assert "Date.now() - timestamp" not in response.text
    assert 'setInterval(refreshMonitorEvents, MONITOR_REFRESH_MS)' in response.text


def test_edge_config_endpoint_reads_and_writes_equipment_bindings(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "mqtt_host": "192.168.0.130",
                "max_ftms_connections": 1,
                "antenna_channels": [
                    {
                        "id": "uart-1",
                        "port": "/dev/ttyAMA0",
                    }
                ],
                "equipment_bindings": [
                    {
                        "node_id": "fitrace-edge-test-01",
                        "equipment_id": "TREAD_01",
                        "equipment_type": "treadmill",
                        "ble_target": "TREAD_01_TARGET",
                        "antenna_channel": "uart-1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    client = TestClient(edge_app_module.app)

    response = client.get("/api/config")

    assert response.status_code == 200
    payload = response.json()
    payload["equipment_bindings"][0]["equipment_id"] = "跑步機_A"

    update_response = client.post("/api/config", json=payload)

    assert update_response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["equipment_bindings"][0]["equipment_id"] == "跑步機_A"


def test_reconnect_configured_antenna_devices_groups_targets_by_channel(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "mqtt_host": "192.168.0.130",
                "max_ftms_connections": 3,
                "antenna_channels": [
                    {
                        "id": "uart-1",
                        "port": "/dev/ttyAMA0",
                        "baudrate": 115200,
                    },
                    {
                        "id": "uart-2",
                        "port": "/dev/ttyAMA4",
                        "baudrate": 57600,
                        "rtscts": True,
                    },
                ],
                "equipment_bindings": [
                    {
                        "node_id": "fitrace-edge-test-01",
                        "equipment_id": "BIKE_01",
                        "equipment_type": "fan_bike",
                        "ble_target": "AA:BB:CC:DD:EE:01",
                        "antenna_channel": "uart-1",
                    },
                    {
                        "node_id": "fitrace-edge-test-02",
                        "equipment_id": "BIKE_02",
                        "equipment_type": "fan_bike",
                        "ble_target": "AA:BB:CC:DD:EE:02",
                        "antenna_channel": "uart-1",
                    },
                    {
                        "node_id": "fitrace-edge-test-03",
                        "equipment_id": "BIKE_03",
                        "equipment_type": "fan_bike",
                        "ble_target": "AA:BB:CC:DD:EE:03",
                        "antenna_channel": "uart-2",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    class FakeAntennaCommandRunner:
        def __init__(self):
            self.requests = []

        def run(self, request):
            self.requests.append(request)
            return {
                "port": request.port,
                "command": request.command,
                "rx": [f"{request.command}:OK"],
            }

    fake_runner = FakeAntennaCommandRunner()
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(edge_app_module, "antenna_command_runner", fake_runner)
    client = TestClient(edge_app_module.app)

    response = client.post(
        "/api/antenna/reconnect-configured",
        json={"timeout_sec": 2, "report_interval_ms": 750},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "reconnected"
    assert payload["channels"][0]["macs"] == [
        "AA:BB:CC:DD:EE:01",
        "AA:BB:CC:DD:EE:02",
    ]
    assert payload["channels"][1]["macs"] == ["AA:BB:CC:DD:EE:03"]
    connect_requests = [request for request in fake_runner.requests if request.command == "connect"]
    report_requests = [request for request in fake_runner.requests if request.command == "report"]
    assert connect_requests[0].port == "/dev/ttyAMA0"
    assert connect_requests[0].macs == ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"]
    assert connect_requests[1].port == "/dev/ttyAMA4"
    assert connect_requests[1].baudrate == 57600
    assert connect_requests[1].rtscts is True
    assert connect_requests[1].macs == ["AA:BB:CC:DD:EE:03"]
    assert [request.report_interval_ms for request in report_requests] == [750, 750]


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


def test_edge_setup_sensitive_endpoints_require_admin_token(monkeypatch):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setattr(edge_app_module, "ftms_scanner", FakeScanner())

    class FakeWifiStatusReader:
        def read(self, interface: str = "wlan0"):
            return edge_app_module.WifiStatus(
                interface=interface,
                connected=True,
                recommendation="Signal is suitable for live race operation.",
            )

    monkeypatch.setattr(edge_app_module, "wifi_status_reader", FakeWifiStatusReader())
    client = TestClient(edge_app_module.app)

    assert client.get("/api/ble/scan").status_code == 401
    assert client.get("/api/wifi/status").status_code == 401

    headers = {"X-FitRace-Admin-Token": "admin-secret"}
    assert client.get("/api/ble/scan", headers=headers).status_code == 200
    assert client.get("/api/wifi/status", headers=headers).status_code == 200


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


def test_edge_antenna_command_endpoint_uses_runner(monkeypatch):
    class FakeAntennaCommandRunner:
        def run(self, request):
            return {
                "port": request.port,
                "command": request.command,
                "tx": ["PING;"],
                "rx": ["BOOT:NO_LIST;"],
                "parsed": [{"type": "boot", "has_list": False, "count": 0}],
            }

    monkeypatch.setattr(
        edge_app_module,
        "antenna_command_runner",
        FakeAntennaCommandRunner(),
    )
    client = TestClient(edge_app_module.app)

    response = client.post(
        "/api/antenna/command",
        json={"port": "/dev/ttyAMA0", "command": "ping"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["port"] == "/dev/ttyAMA0"
    assert payload["command"] == "ping"
    assert payload["rx"] == ["BOOT:NO_LIST;"]


def test_edge_antenna_config_endpoint_reads_config_file(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "antenna_channels": [
                    {
                        "id": "uart-1",
                        "port": "/dev/serial0",
                        "uart": "UART0",
                        "tx_gpio": "GPIO14",
                        "rx_gpio": "GPIO15",
                    },
                    {
                        "id": "uart-2",
                        "port": "/dev/ttyAMA4",
                        "uart": "UART4",
                        "tx_gpio": "GPIO12",
                        "rx_gpio": "GPIO13",
                        "dtoverlay": "uart4-pi5",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    client = TestClient(edge_app_module.app)

    response = client.get("/api/antenna/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_port"] == "/dev/serial0"
    assert payload["channels"][0]["port"] == "/dev/serial0"
    assert payload["channels"][1]["port"] == "/dev/ttyAMA4"
    assert payload["channels"][1]["dtoverlay"] == "uart4-pi5"


def test_edge_monitor_events_endpoint_reads_event_log(monkeypatch, tmp_path):
    event_log = edge_app_module.EdgeEventLog(tmp_path / "edge_monitor.jsonl")
    event_log.record("uart", "rx", channel="uart-1", message="BOOT:NO_LIST;")
    monkeypatch.setattr(edge_app_module, "edge_event_log", event_log)
    client = TestClient(edge_app_module.app)

    response = client.get("/api/monitor/events?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"].endswith("edge_monitor.jsonl")
    assert isinstance(payload["server_now_epoch_ms"], int)
    assert payload["events"][0]["source"] == "uart"
    assert payload["events"][0]["direction"] == "rx"
    assert payload["events"][0]["message"] == "BOOT:NO_LIST;"


def test_edge_antenna_command_defaults_to_configured_first_channel(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "antenna_channels": [
                    {
                        "id": "uart-1",
                        "port": "/dev/serial0",
                        "uart": "UART0",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class FakeAntennaCommandRunner:
        def run(self, request):
            return {"port": request.port, "command": request.command}

    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        edge_app_module,
        "antenna_command_runner",
        FakeAntennaCommandRunner(),
    )
    client = TestClient(edge_app_module.app)

    response = client.post("/api/antenna/command", json={"command": "ping"})

    assert response.status_code == 200
    assert response.json()["port"] == "/dev/serial0"


def test_edge_antenna_command_endpoint_requires_admin_token(monkeypatch):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "admin-secret")
    client = TestClient(edge_app_module.app)

    response = client.post(
        "/api/antenna/command",
        json={"port": "/dev/ttyAMA0", "command": "ping"},
    )

    assert response.status_code == 401
