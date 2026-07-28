from fastapi.testclient import TestClient
import json
from pathlib import Path

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


def test_edge_operator_page_served_at_root():
    # AGENT.md architecture decision: `/` now serves the new simplified
    # operator page (a real static file, operator.html), i18n-injected the
    # same way as the old page. `/maintenance` keeps the full old page.
    client = TestClient(edge_app_module.app)

    response = client.get("/")

    assert response.status_code == 200
    assert 'name="fitrace-page" content="operator"' in response.text
    assert "__I18N_DICTIONARIES__" not in response.text
    # zh-TW sample string proves server-side i18n injection actually ran.
    assert "新增設備" in response.text


def test_edge_operator_page_uses_ip_node_number_in_setup_title(monkeypatch):
    monkeypatch.setattr(edge_app_module, "local_ip_address", lambda: "192.168.0.130")
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    assert "<title>Node130-Edge Node Setup</title>" in source
    assert '<h1 id="operator-title">Node130-Edge Node Setup</h1>' in source
    assert 'const edgeNodeLabel = "Node130";' in source
    assert '"operator.title": "Edge Node Setup"' in source
    assert '"operator.title": "Edge Node 設定"' in source
    assert 'const title = `${edgeNodeLabel}-${t("operator.title")}`' in source
    assert "document.title = title" in source


def test_edge_operator_page_exposes_disconnect_all_and_clear_bindings_action():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    assert 'id="disconnect-all-btn"' in source
    assert 'class="btn-primary disconnect-all-button"' in source
    assert ".disconnect-all-bar {" in source
    assert "border: 1px solid var(--border);" in source
    assert ".btn-danger" not in source
    assert 'id="disconnect-all-message"' in source
    assert 'data-i18n="operator.disconnect_all"\n          disabled' in source
    assert '"operator.disconnect_all": "Disconnect all & clear bindings"' in source
    assert '"operator.disconnect_all": "全部斷線並清除綁定"' in source
    assert 'adminFetch("/api/equipment-bindings", {' in source
    assert 'method: "DELETE"' in source
    assert "async function disconnectAllAndClearBindings()" in source


def test_edge_operator_page_can_scan_and_connect_wifi_without_maintenance_page():
    client = TestClient(edge_app_module.app)

    response = client.get("/")

    assert response.status_code == 200
    source = response.text
    assert 'id="edge-workspace"' in source
    assert 'id="wifi-column"' in source
    assert 'id="edge-column"' in source
    assert 'id="wifi-networks"' in source
    assert 'id="wifi-scan-message"' in source
    assert 'aria-live="polite"' in source
    assert 'id="wifi-toggle-btn"' in source
    assert 'aria-expanded="false"' in source
    assert 'aria-controls="wifi-picker-body"' in source
    assert 'id="wifi-picker-body" class="wifi-picker-body" hidden' in source
    assert source.index('id="wifi-column"') < source.index('id="edge-column"')
    assert 'id="wifi-settings-btn"' not in source
    assert 'id="view-wifi"' not in source


def test_edge_operator_workspace_is_two_columns_and_stacks_on_narrow_screens():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    assert "grid-template-columns: minmax(300px, 360px) minmax(0, 1fr)" in source
    assert "max-height: calc(100vh - 144px)" in source
    assert "@media (max-width: 820px)" in source
    assert "grid-template-columns: 1fr" in source


def test_edge_operator_disconnect_action_stacks_before_the_main_mobile_breakpoint():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    assert "@media (max-width: 1100px)" in source
    responsive_start = source.index("@media (max-width: 1100px)")
    responsive_end = source.index("@media (max-width: 820px)", responsive_start)
    responsive_source = source[responsive_start:responsive_end]
    assert ".disconnect-all-bar" in responsive_source
    assert "flex-direction: column" in responsive_source
    assert ".disconnect-all-message" in responsive_source
    assert "text-align: left" in responsive_source


def test_edge_operator_wifi_view_uses_existing_scan_and_connect_apis():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    assert "/api/wifi/networks" in source
    assert "/api/wifi/connect" in source
    assert "async function scanWifiNetworks()" in source
    assert "async function toggleWifiPicker()" in source
    assert "function renderWifiConnect(net)" in source
    assert "async function openWifiSettings()" not in source
    assert "showView(\"wifi\")" not in source
    assert 'body: JSON.stringify({ ssid: net.ssid, password })' in source
    assert 'id="wifi-password"' in source
    assert (
        '${net.active ? t("wifi.connected") : t("wifi.connect")} ${net.ssid}'
        in source
    )
    assert 't("wifi.back_to_list")' in source
    assert "if (expanded && !wifiNetworksLoaded)" in source
    init_start = source.index("applyTranslations();")
    init_source = source[init_start:]
    assert "scanWifiNetworks();" not in init_source


def test_edge_operator_page_shows_live_uart_rx_tx_monitor_below_wifi():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    wifi_title_index = source.index('id="wifi-settings-title"')
    uart_title_index = source.index('id="uart-monitor-title"')
    edge_column_index = source.index('id="edge-column"')
    assert wifi_title_index < uart_title_index < edge_column_index
    assert 'aria-labelledby="wifi-settings-title uart-monitor-title"' in source
    assert 'aria-labelledby="uart-monitor-title"' in source
    assert 'id="uart-monitor-log"' in source
    assert 'tabindex="0"' in source
    assert '"uartmonitor.title": "UART RX/TX Monitor"' in source
    assert '"uartmonitor.title": "UART 收發監看"' in source
    assert '"uartmonitor.rx": "RX Receive"' in source
    assert '"uartmonitor.tx": "TX Send"' in source


def test_edge_operator_uart_monitor_is_three_times_taller():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    assert ".uart-monitor-log {\n      height: 840px;" in source
    mobile_start = source.index("@media (max-width: 820px)")
    mobile_source = source[mobile_start:]
    assert ".uart-monitor-log {\n        height: 720px;" in mobile_source


def test_edge_operator_uart_monitor_shows_latest_first_and_keeps_only_200_messages():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text
    render_start = source.index("function renderUartMonitor(events)")
    render_end = source.index("async function refreshTelemetry()", render_start)
    render_source = source[render_start:render_end]
    refresh_start = render_end
    refresh_end = source.index("// -- view switching", refresh_start)
    refresh_source = source[refresh_start:refresh_end]

    assert "const UART_MONITOR_MAX = 200" in source
    assert 'event.source === "uart"' in render_source
    assert 'event.direction === "rx" || event.direction === "tx"' in render_source
    assert ".slice(-UART_MONITOR_MAX).reverse()" in render_source
    assert 'event.parsed && event.parsed.raw != null' in render_source
    assert "parsed.type === \"telemetry\"" not in render_source
    assert "const wasAtTop = log.scrollTop < 24;" in render_source
    assert "if (firstRender || wasAtTop)" in render_source
    assert "log.scrollTop = 0;" in render_source
    assert "wasAtBottom" not in render_source
    assert "log.scrollTop = log.scrollHeight" not in render_source
    assert 'adminFetch("/api/monitor/events?limit=500")' in refresh_source
    assert "renderUartMonitor(events);" in refresh_source


def test_edge_maintenance_page_serves_old_setup_page():
    client = TestClient(edge_app_module.app)

    response = client.get("/maintenance")

    assert response.status_code == 200
    assert "UART Antenna Control" in response.text
    assert 'name="fitrace-page" content="operator"' not in response.text


def test_edge_setup_page_includes_uart_antenna_controls_without_ble_scan_panel():
    client = TestClient(edge_app_module.app)

    response = client.get("/maintenance")

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
    assert (
        'id="antenna-report-interval" type="number" min="100" max="10000" value="250"'
        in response.text
    )
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
    assert "setInterval(refreshMonitorEvents, MONITOR_REFRESH_MS)" in response.text


def test_edge_config_endpoint_reads_and_writes_equipment_bindings(
    monkeypatch, tmp_path
):
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


def test_edge_config_endpoint_rejects_duplicate_binding_macs(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    original = {
        "node_id": "fitrace-edge-test",
        "max_ftms_connections": 2,
        "antenna_channels": [{"id": "uart-1", "port": "/dev/ttyAMA0"}],
        "equipment_bindings": [],
    }
    config_path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    client = TestClient(edge_app_module.app)

    duplicate_payload = {
        **original,
        "equipment_bindings": [
            {
                "node_id": "fitrace-edge-test-01",
                "equipment_id": "BIKE_A",
                "equipment_type": "fan_bike",
                "ble_target": "74:46:B3:DB:48:49",
                "antenna_channel": "uart-1",
            },
            {
                "node_id": "fitrace-edge-test-02",
                "equipment_id": "BIKE_B",
                "equipment_type": "fan_bike",
                "ble_target": "74:46:b3:db:48:49",
                "antenna_channel": "uart-1",
            },
        ],
    }

    response = client.post("/api/config", json=duplicate_payload)

    assert response.status_code == 422
    assert json.loads(config_path.read_text(encoding="utf-8")) == original


def test_clear_equipment_bindings_disconnects_every_channel_and_restarts_runtime(
    monkeypatch, tmp_path
):
    from fitrace_common.power_manager import PowerActionResult

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "mqtt_host": "192.168.0.130",
                "max_ftms_connections": 2,
                "antenna_channels": [
                    {"id": "uart-1", "port": "/dev/ttyAMA0"},
                    {"id": "uart-2", "port": "/dev/ttyAMA4"},
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
                        "antenna_channel": "uart-2",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    call_order = []

    class FakeAntennaCommandRunner:
        def run(self, request):
            call_order.append(("disconnect", request.port, request.command))
            return {
                "port": request.port,
                "command": request.command,
                "rx": ["OK:DISCONNECT"],
            }

    class FakeServiceRestartManager:
        def restart_service(self):
            call_order.append(("restart",))
            return PowerActionResult(
                action="restart-service",
                target="edge",
                dry_run=False,
                command=[
                    "sudo",
                    "systemctl",
                    "restart",
                    "fitracestudio-edge.service",
                ],
                executed=True,
            )

    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        edge_app_module, "antenna_command_runner", FakeAntennaCommandRunner()
    )
    monkeypatch.setattr(
        edge_app_module, "service_restart_manager", FakeServiceRestartManager()
    )
    client = TestClient(edge_app_module.app)

    response = client.delete("/api/equipment-bindings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "cleared"
    assert payload["cleared_count"] == 2
    assert payload["config"]["equipment_bindings"] == []
    assert payload["config"]["max_ftms_connections"] == 2
    assert [channel["channel_id"] for channel in payload["channels"]] == [
        "uart-1",
        "uart-2",
    ]
    assert call_order == [
        ("disconnect", "/dev/ttyAMA0", "disconnect_all"),
        ("disconnect", "/dev/ttyAMA4", "disconnect_all"),
        ("restart",),
    ]
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["equipment_bindings"] == []
    assert saved["max_ftms_connections"] == 2


def test_clear_equipment_bindings_requires_admin_token(monkeypatch):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "admin-secret")
    client = TestClient(edge_app_module.app)

    response = client.delete("/api/equipment-bindings")

    assert response.status_code == 401


def test_reconnect_configured_antenna_devices_groups_targets_by_channel(
    monkeypatch, tmp_path
):
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
    connect_requests = [
        request for request in fake_runner.requests if request.command == "connect"
    ]
    report_requests = [
        request for request in fake_runner.requests if request.command == "report"
    ]
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


def test_edge_runtime_restart_flag_keeps_full_power_flag_backward_compatible(
    monkeypatch,
):
    monkeypatch.delenv("FITRACE_EDGE_SERVICE_RESTART_ENABLED", raising=False)
    monkeypatch.delenv("FITRACE_POWER_COMMANDS_ENABLED", raising=False)
    assert edge_app_module._edge_service_restart_enabled() is False

    monkeypatch.setenv("FITRACE_EDGE_SERVICE_RESTART_ENABLED", "1")
    assert edge_app_module._edge_service_restart_enabled() is True

    monkeypatch.delenv("FITRACE_EDGE_SERVICE_RESTART_ENABLED")
    monkeypatch.setenv("FITRACE_POWER_COMMANDS_ENABLED", "1")
    assert edge_app_module._edge_service_restart_enabled() is True


def test_edge_runtime_restart_can_be_enabled_without_enabling_machine_power(
    monkeypatch,
):
    from fitrace_common.power_manager import PowerManager

    executed_commands = []
    service_restart_manager = PowerManager(
        target="edge",
        service_name="fitracestudio-edge.service",
        dry_run=False,
        command_runner=executed_commands.append,
    )
    machine_power_manager = PowerManager(
        target="edge",
        service_name="fitracestudio-edge.service",
        dry_run=True,
        command_runner=lambda command: (_ for _ in ()).throw(
            AssertionError(f"machine power command must not run: {command}")
        ),
    )
    monkeypatch.setattr(
        edge_app_module, "service_restart_manager", service_restart_manager
    )
    monkeypatch.setattr(edge_app_module, "power_manager", machine_power_manager)
    client = TestClient(edge_app_module.app)

    status = client.get("/api/system/power/status").json()
    assert status["dry_run"] is True
    assert status["restart_service_dry_run"] is False
    assert status["restart_service_enabled"] is True

    restart = client.post("/api/system/power/restart-service", json={})
    assert restart.status_code == 200
    assert restart.json()["executed"] is True
    assert executed_commands == [
        ["sudo", "systemctl", "restart", "fitracestudio-edge.service"]
    ]

    reboot = client.post(
        "/api/system/power/reboot",
        json={"confirmation": "REBOOT"},
    )
    assert reboot.status_code == 200
    assert reboot.json()["executed"] is False
    assert reboot.json()["dry_run"] is True


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


def test_edge_antenna_command_defaults_to_configured_first_channel(
    monkeypatch, tmp_path
):
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


def test_edge_setup_page_monitor_poll_failure_resets_rebuild_guard():
    # Fix A: refreshMonitorEvents' catch block writes an error into monitorGrid
    # but must also null out monitorGridSignature, otherwise the very next
    # successful poll thinks nothing changed and never rebuilds the cards.
    client = TestClient(edge_app_module.app)

    response = client.get("/maintenance")

    assert response.status_code == 200
    # One `let` declaration plus one reset inside the catch block.
    assert response.text.count("monitorGridSignature = null;") == 2


def test_edge_setup_page_empty_channels_keep_uart_controls_reachable():
    # Fix B: an empty/failed channel list must fall back to the server's
    # default_port instead of bricking every antenna button (including REBOOT).
    client = TestClient(edge_app_module.app)

    response = client.get("/maintenance")

    assert response.status_code == 200
    assert "antenna.no_channels" in response.text
    assert (
        '"antenna.no_channels": "No UART channels configured — using default serial port."'
        in response.text
    )
    assert (
        '"antenna.no_channels": "尚未設定 UART 通道，使用預設串口。"' in response.text
    )
    assert "config.default_port" in response.text
    # loadAntennaConfig must stop clobbering selectedAntennaPort/antennaChannels
    # on a failed fetch and instead defer to updateChannelOccupancy's fallback logic.
    assert response.text.count('t("antenna.channel_load_failed")') == 1


def test_edge_setup_page_unassigned_bindings_stay_reachable_on_every_channel():
    # Fix C: a binding with a blank/unknown antenna_channel must never be
    # permanently dimmed or excluded from CONNECT targets on every channel.
    client = TestClient(edge_app_module.app)

    response = client.get("/maintenance")

    assert response.status_code == 200
    assert "function knownChannelIds()" in response.text
    assert "binding.antenna_channel === channelId : true" in response.text
    assert "if (!assigned) return;" in response.text


def test_edge_setup_page_single_fetch_wrapper_owns_401_banner():
    # Fix D: adminFetch() must be the only place that reacts to a 401 so the
    # banner can never be silently skipped by a copy-pasted call site.
    client = TestClient(edge_app_module.app)

    response = client.get("/maintenance")

    assert response.status_code == 200
    assert "async function adminFetch(url, options = {}) {" in response.text
    # The only raw `await fetch(` left must be the one inside adminFetch itself —
    # every call site should go through the wrapper instead.
    assert response.text.count("await fetch(") == 1
    assert (
        response.text.count("if (response.status === 401) showAdminAuthBanner();") == 1
    )
    # clearAllBindings' reconnect call must stop swallowing failures silently.
    assert (
        'setConfigMessage(detail ? `${t("bindings.failed")}: ${detail}` : t("bindings.failed"), "error");'
        in response.text
    )


def test_edge_setup_page_monitor_hidden_poll_only_recomputes_live_count():
    # The 250ms poll (updateMonitorFromEvents) must not do full card DOM work
    # while the Monitor tab is hidden — it only needs monitorLiveCount fresh
    # for the SCAN confirm guard. When hidden it should call the cheaper
    # recomputeMonitorLiveCount() instead of the full renderMonitorEquipment().
    client = TestClient(edge_app_module.app)

    response = client.get("/maintenance")

    assert response.status_code == 200
    assert "function recomputeMonitorLiveCount()" in response.text
    assert "if (monitorSection.hidden) {" in response.text
    assert "recomputeMonitorLiveCount();" in response.text
    assert "} else {\n        renderMonitorEquipment();\n      }" in response.text


def test_edge_setup_page_serves_i18n_via_server_side_injection_not_inline_dict():
    # AGENT.md §2.3: locale dictionaries live under infrastructure/locales/ and
    # get injected server-side into the rendered page at module-load time —
    # the static HTML/JS must no longer hardcode the translation strings.
    client = TestClient(edge_app_module.app)

    response = client.get("/maintenance")

    assert response.status_code == 200
    assert "掃描會中斷目前已連線設備的即時數據。" in response.text
    assert "__I18N_DICTIONARIES__" not in response.text


def test_edge_setup_page_uart_log_filter_keeps_tx_events_without_parsed():
    # Bug: TX events are recorded without a `parsed` field (see
    # antenna_ftms_manager._write / command_runner._record_event), so the old
    # filter `e.parsed && e.parsed.type !== "telemetry"` dropped every TX row.
    # The fix keeps a uart event unless it is specifically parsed telemetry.
    client = TestClient(edge_app_module.app)

    response = client.get("/maintenance")

    assert response.status_code == 200
    assert (
        'e.source === "uart" && e.parsed && e.parsed.type !== "telemetry"'
        not in response.text
    )
    assert (
        'e.source === "uart" && !(e.parsed && e.parsed.type === "telemetry")'
        in response.text
    )


def test_edge_i18n_endpoint_returns_both_locale_dictionaries():
    # NOTE: the runtime locale key is "en-US" (matching dictionaries["en-US"]
    # and getBrowserLocale()/localStorage in the page JS), not "en".
    client = TestClient(edge_app_module.app)

    response = client.get("/api/i18n")

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) >= {"en-US", "zh-TW"}
    assert "antenna.scan_live_warning" in data["en-US"]
    assert "antenna.scan_live_warning" in data["zh-TW"]


def test_edge_locale_json_files_parse_and_share_identical_key_sets():
    locales_dir = Path(edge_app_module.__file__).resolve().parent.parent / "locales"
    en = json.loads((locales_dir / "en.json").read_text(encoding="utf-8"))
    zh_tw = json.loads((locales_dir / "zh_tw.json").read_text(encoding="utf-8"))

    assert set(en.keys()) == set(zh_tw.keys())
    assert "antenna.scan_live_warning" in en
    assert "antenna.scan_live_warning" in zh_tw


def test_edge_setup_page_restart_runtime_surfaces_dry_run_instead_of_false_success():
    # Bug: PowerManager in dry-run mode (the default unless
    # FITRACE_POWER_COMMANDS_ENABLED=1 is set) returns HTTP 200 with
    # dry_run=True, executed=False, but restartEdgeRuntime() only checked
    # response.ok -- so the operator saw a cheerful "restarted" success
    # message while the runtime never actually restarted. The fix must
    # branch on dry_run/executed instead of just response.ok.
    client = TestClient(edge_app_module.app)

    response = client.get("/maintenance")

    assert response.status_code == 200
    source = response.text
    restart_fn_start = source.index("async function restartEdgeRuntime()")
    restart_fn = source[restart_fn_start : restart_fn_start + 1500]

    assert "dry_run" in restart_fn
    assert "power.dry_run_warning" in restart_fn


def test_edge_pages_use_the_runtime_restart_capability_for_the_warning_banner():
    client = TestClient(edge_app_module.app)

    for path in ("/", "/maintenance"):
        source = client.get(path).text
        check_start = source.index("async function checkPowerDryRunMode()")
        check_fn = source[check_start : check_start + 800]
        assert "status.restart_service_dry_run ?? status.dry_run" in check_fn


def test_edge_locale_files_contain_power_dry_run_keys():
    locales_dir = Path(edge_app_module.__file__).resolve().parent.parent / "locales"
    en = json.loads((locales_dir / "en.json").read_text(encoding="utf-8"))
    zh_tw = json.loads((locales_dir / "zh_tw.json").read_text(encoding="utf-8"))

    assert "power.dry_run_warning" in en
    assert "power.dry_run_warning" in zh_tw
    assert set(en.keys()) == set(zh_tw.keys())


def test_edge_setup_page_monitor_matches_telemetry_by_mac_not_only_node_id():
    # Bug seen on hardware: node_id is only a label. When the runtime runs a
    # stale config it cannot find a binding for an incoming MAC and falls back
    # to a derived "<edge_node_id>-<mac>" node_id (see AntennaFtmsManager.
    # _to_telemetry), while the page looks up config.json's binding.node_id.
    # The two never match, so a card renders completely blank even though
    # correct telemetry for that exact machine is streaming. The MAC is the
    # machine's real identity, so it must be the primary lookup key.
    client = TestClient(edge_app_module.app)

    source = client.get("/maintenance").text

    assert "monitorNodeIdByMac" in source
    assert "function monitorKeyForBinding(" in source

    leaves_start = source.index("function updateMonitorCardLeaves(")
    leaves_fn = source[leaves_start : leaves_start + 1800]
    assert "monitorKeyForBinding(binding)" in leaves_fn
    assert "monitorLatestByNode.get(binding.node_id)" not in leaves_fn
    assert "monitorDisplayedByNode.get(binding.node_id)" not in leaves_fn

    live_start = source.index("function recomputeMonitorLiveCount(")
    live_fn = source[live_start : live_start + 800]
    assert "monitorKeyForBinding(binding)" in live_fn
    assert "monitorLatestByNode.get(binding.node_id)" not in live_fn


def test_edge_setup_page_duplicate_mac_bindings_fall_back_to_node_id():
    client = TestClient(edge_app_module.app)

    source = client.get("/maintenance").text
    key_fn_start = source.index("function monitorKeyForBinding(")
    key_fn = source[key_fn_start : key_fn_start + 700]

    assert "monitorBindingTargetCount" in source
    assert "monitorBindingTargetCount(binding.ble_target) === 1" in key_fn
    assert "return binding.node_id;" in key_fn

    operator_source = client.get("/").text
    operator_key_fn_start = operator_source.index("function telemetryForBinding(")
    operator_key_fn = operator_source[
        operator_key_fn_start : operator_key_fn_start + 700
    ]
    assert "bindingTargetCount" in operator_source
    assert "bindingTargetCount(binding.ble_target) === 1" in operator_key_fn
    assert "telemetryByNodeId.get(binding.node_id)" in operator_key_fn


class FakePairingAntennaRunner:
    """Fakes AntennaCommandRunner for pairing-session integration tests:
    "scan" answers with canned device sightings per port, everything else
    just acks so reconnect_configured_devices' disconnect/connect/report
    sequence doesn't blow up."""

    def __init__(self, scan_results_by_port=None):
        self.scan_results_by_port = scan_results_by_port or {}
        self.calls = []

    def run(self, request):
        self.calls.append(request)
        if request.command == "scan":
            parsed = self.scan_results_by_port.get(request.port, [])
            return {"port": request.port, "command": "scan", "parsed": parsed}
        return {
            "port": request.port,
            "command": request.command,
            "parsed": [{"type": "ok", "command": request.command.upper()}],
        }


def _install_fresh_pairing_session(
    monkeypatch, tmp_path, runner, *, power_manager=None
):
    from edge_node.usecases.pairing_session import PairingSession

    def restore(config):
        return edge_app_module.reconnect_configured_devices(
            config,
            runner,
            disconnect_first=True,
            timeout_sec=5.0,
            report_interval_ms=250,
        )

    manager = power_manager or edge_app_module.service_restart_manager

    def restart():
        return edge_app_module.asdict(manager.restart_service())

    session = PairingSession(
        command_runner=runner,
        event_log=edge_app_module.edge_event_log,
        load_config=edge_app_module.load_edge_config,
        save_config=edge_app_module.save_edge_config,
        restore_configured_devices=restore,
        restart_service=restart,
        flag_path=tmp_path / "pairing.flag",
    )
    monkeypatch.setattr(edge_app_module, "pairing_session", session)
    return session


def test_pairing_session_start_status_confirm_happy_path(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "antenna_channels": [
                    {"id": "uart-1", "port": "/dev/ttyAMA0"},
                    {"id": "uart-2", "port": "/dev/ttyAMA4"},
                ],
                "equipment_bindings": [],
                "max_ftms_connections": 10,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    runner = FakePairingAntennaRunner(
        scan_results_by_port={
            "/dev/ttyAMA0": [
                {
                    "type": "device",
                    "address": "AA:BB:CC:DD:EE:05",
                    "rssi": -40,
                    "name": "Bike A",
                }
            ],
        }
    )
    _install_fresh_pairing_session(monkeypatch, tmp_path, runner)
    client = TestClient(edge_app_module.app)

    start_response = client.post("/api/pairing/start", json={})
    assert start_response.status_code == 200
    start_payload = start_response.json()
    assert start_payload["candidates"][0]["mac"] == "AA:BB:CC:DD:EE:05"
    assert start_payload["capacity"]["per_channel"] == {"uart-1": 3, "uart-2": 3}

    status_response = client.get("/api/pairing/status")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["state"] == "observing"
    assert status_payload["candidates"][0]["mac"] == "AA:BB:CC:DD:EE:05"

    confirm_response = client.post(
        "/api/pairing/confirm",
        json={
            "mac": "AA:BB:CC:DD:EE:05",
            "equipment_type": "fan_bike",
            "display_name": "Bike One",
        },
    )
    assert confirm_response.status_code == 200
    confirm_payload = confirm_response.json()
    assert confirm_payload["binding"]["equipment_id"] == "Bike One"
    assert confirm_payload["binding"]["ble_target"] == "AA:BB:CC:DD:EE:05"
    assert confirm_payload["binding"]["antenna_channel"] == "uart-1"
    assert "channels" in confirm_payload["reconnect"]
    assert "dry_run" in confirm_payload["restart"]

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert any(
        binding["equipment_id"] == "Bike One" for binding in saved["equipment_bindings"]
    )
    # session must be idle again, ready for a fresh pairing attempt
    assert client.get("/api/pairing/status").json() == {"state": "idle"}


def test_pairing_confirm_surfaces_dry_run_power_manager_result(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "antenna_channels": [{"id": "uart-1", "port": "/dev/ttyAMA0"}],
                "equipment_bindings": [],
                "max_ftms_connections": 10,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    runner = FakePairingAntennaRunner(
        scan_results_by_port={
            "/dev/ttyAMA0": [
                {
                    "type": "device",
                    "address": "AA:BB:CC:DD:EE:07",
                    "rssi": -35,
                    "name": "Rower Z",
                }
            ],
        }
    )
    from fitrace_common.power_manager import PowerManager

    dry_run_power_manager = PowerManager(
        target="edge", service_name="fitracestudio-edge.service", dry_run=True
    )
    _install_fresh_pairing_session(
        monkeypatch, tmp_path, runner, power_manager=dry_run_power_manager
    )
    client = TestClient(edge_app_module.app)

    client.post("/api/pairing/start", json={})
    confirm_response = client.post(
        "/api/pairing/confirm",
        json={"mac": "AA:BB:CC:DD:EE:07", "equipment_type": "rowing_machine"},
    )

    assert confirm_response.status_code == 200
    payload = confirm_response.json()
    assert payload["restart"]["dry_run"] is True
    assert payload["restart"]["executed"] is False
