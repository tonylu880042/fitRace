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


def test_edge_operator_device_cards_show_the_current_uart_channel():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    assert 'class="binding-card-channel" data-field="channel"' in source
    assert "function formatAntennaChannel(value)" in source
    leaves_start = source.index("function updateBindingCardLeaves()")
    leaves_fn = source[leaves_start : leaves_start + 1400]
    assert "payload?.antenna_channel || binding.antenna_channel" in leaves_fn


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


def test_edge_operator_page_requires_verified_central_hub_before_pairing():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    assert 'id="central-hub-title"' in source
    assert 'id="central-hub-host"' in source
    assert 'id="central-hub-port"' in source
    assert 'id="central-hub-save-btn"' in source
    assert 'id="central-hub-message"' in source
    assert 'adminFetch("/api/central-hub")' in source
    assert "let centralHubReady = false;" in source
    assert "full || !centralHubReady" in source
    assert (
        '"hub.required": "Connect to the Central Hub before adding equipment."'
        in source
    )
    assert '"hub.required": "請先連線 Central Hub，才能新增設備。"' in source


def test_edge_operator_hub_form_preserves_unsaved_values_and_save_errors():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text
    save_start = source.index("async function saveCentralHub()")
    save_fn = source[save_start : save_start + 2600]
    home_start = source.index("async function goHome()")
    home_fn = source[home_start : home_start + 180]

    assert "let centralHubFormDirty = false;" in source
    assert "centralHubFormDirty = true;" in source
    assert "await refreshCentralHubStatus({ updateMessage: false });" in save_fn
    assert "centralHubFormDirty = true;" in save_fn
    assert "full || !centralHubReady || centralHubFormDirty" in source
    assert "async function loadConfig({ populateHubFields = false } = {})" in source
    assert "await loadConfig();" in home_fn


def test_edge_operator_hub_status_polls_without_flashing_red_or_overwriting_messages():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text
    status_start = source.index("async function refreshCentralHubStatus(")
    status_fn = source[status_start : status_start + 1800]

    assert 'status === "checking"' in source
    assert ".chip-dot.pending" in source
    assert "updateMessage = true" in status_fn
    assert "if (updateMessage)" in status_fn
    assert (
        "setInterval(() => refreshCentralHubStatus({ updateMessage: false }), 5000);"
        in source
    )


def test_edge_operator_page_exposes_ping_and_status_field_diagnostics():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    assert 'id="field-diagnostics-title"' in source
    assert 'id="diagnostic-ping-btn"' in source
    assert 'id="diagnostic-status-btn"' in source
    assert 'id="diagnostic-results"' in source
    assert 'runAntennaDiagnostic("ping")' in source
    assert 'runAntennaDiagnostic("status")' in source
    assert 'adminFetch("/api/antenna/command", {' in source
    assert '"diagnostics.ping": "PING"' in source
    assert '"diagnostics.status": "STATUS"' in source
    assert '"diagnostics.title": "現場診斷"' in source


def test_edge_operator_diagnostics_hide_unrelated_live_telemetry_from_results():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text
    helper_start = source.index("function diagnosticReplies(")
    helper_end = source.index("async function runAntennaDiagnostic(", helper_start)
    helper_source = source[helper_start:helper_end]

    assert 'command === "status"' in helper_source
    assert 'new Set(["status", "error"])' in helper_source
    assert 'new Set(["boot", "ok", "error"])' in helper_source
    assert "result.parsed" in helper_source
    # the board never sends PING:OK (see docs/Dual_Central_Board_Manager_設計文件.md);
    # that dead branch must stay removed.
    assert 'entry.command === "PING"' not in helper_source
    assert 'entry.type !== "telemetry"' not in helper_source
    assert ".slice(0, 3)" in helper_source
    assert "diagnosticReplies(result, command)" in source


def test_edge_operator_workspace_is_two_columns_and_stacks_on_narrow_screens():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    assert "grid-template-columns: minmax(300px, 360px) minmax(0, 1fr)" in source
    assert "max-height: calc(100vh - 144px)" in source
    assert "@media (max-width: 820px)" in source
    assert "grid-template-columns: 1fr" in source


def test_edge_operator_narrow_screens_put_the_work_area_before_the_sidebar():
    # On a phone/tablet the operator wants diagnostics/add-device/equipment
    # cards/the pairing worklist first, not buried below the Wi-Fi/Central
    # Hub/UART monitor sidebar that source order puts first in the DOM.
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    mobile_start = source.index("@media (max-width: 820px)")
    mobile_end = source.index("</style>", mobile_start)
    mobile_source = source[mobile_start:mobile_end]

    assert ".edge-column {" in mobile_source
    assert ".wifi-column {" in mobile_source
    edge_column_rule_index = mobile_source.index(".edge-column {")
    wifi_column_rule_index = mobile_source.index(".wifi-column {")

    edge_column_rule = mobile_source[
        edge_column_rule_index : mobile_source.index("}", edge_column_rule_index)
    ]
    wifi_column_rule = mobile_source[
        wifi_column_rule_index : mobile_source.index("}", wifi_column_rule_index)
    ]
    assert "order: 1;" in edge_column_rule
    assert "order: 2;" in wifi_column_rule

    # wifi-column and edge-column are direct children of #edge-workspace, so
    # this order swap alone is enough -- confirm that DOM relationship still
    # holds (a nesting change would silently break the CSS `order` swap).
    workspace_index = source.index('id="edge-workspace"')
    wifi_column_dom_index = source.index('id="wifi-column"', workspace_index)
    edge_column_dom_index = source.index('id="edge-column"', workspace_index)
    between = source[wifi_column_dom_index:edge_column_dom_index]
    # no nested "workspace"/other wrapping container id between the two
    assert 'id="edge-workspace"' not in between


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
    assert 'showView("wifi")' not in source
    assert "body: JSON.stringify({ ssid: net.ssid, password })" in source
    assert 'id="wifi-password"' in source
    assert (
        '${net.active ? t("wifi.connected") : t("wifi.connect")} ${net.ssid}' in source
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
    assert "event.parsed && event.parsed.raw != null" in render_source
    assert 'parsed.type === "telemetry"' not in render_source
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


def test_monkeypatched_config_path_still_takes_effect_after_config_store_move(
    monkeypatch, tmp_path
):
    """Regression for the config_store extraction: save_edge_config/
    load_edge_config now delegate to infrastructure/config_store.py, whose
    own CONFIG_PATH must NOT be what gets used here. If app.py's wrapper
    functions captured a copied path binding instead of re-reading the
    module attribute each call, monkeypatching edge_app_module.CONFIG_PATH
    would silently become a no-op and this test would write to the real
    edge_node/config.json instead of tmp_path.
    """
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)

    config = edge_app_module.load_edge_config()
    assert not config_path.exists()

    updated = config.model_copy(update={"node_id": "monkeypatch-check"}, deep=True)
    edge_app_module.save_edge_config(updated)

    assert config_path.exists()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["node_id"] == "monkeypatch-check"

    reloaded = edge_app_module.load_edge_config()
    assert reloaded.node_id == "monkeypatch-check"


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


def _write_multi_binding_config(config_path):
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "mqtt_host": "192.168.0.130",
                "max_ftms_connections": 3,
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
                        "antenna_channel": "uart-1",
                    },
                    {
                        "node_id": "fitrace-edge-test-03",
                        "equipment_id": "ROWER_01",
                        "equipment_type": "rowing_machine",
                        "ble_target": "AA:BB:CC:DD:EE:03",
                        "antenna_channel": "uart-2",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_remove_single_equipment_binding_disconnects_only_that_mac_on_its_channel(
    monkeypatch, tmp_path
):
    # The operator's only way out of a full antenna board used to be
    # DELETE /api/equipment-bindings, which wipes every binding on every
    # channel. This endpoint must free exactly one slot: one disconnect, on
    # the removed binding's own channel and MAC, touching nothing else --
    # every other bound device (including the other one on the SAME
    # channel) keeps its config entry and never gets a command sent to it.
    config_path = tmp_path / "config.json"
    _write_multi_binding_config(config_path)

    recorded_commands = []

    class FakeAntennaCommandRunner:
        def run(self, request):
            recorded_commands.append(
                (request.port, request.command, tuple(request.macs))
            )
            return {
                "port": request.port,
                "command": request.command,
                "rx": ["OK:DISCONNECT"],
            }

    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        edge_app_module, "antenna_command_runner", FakeAntennaCommandRunner()
    )
    client = TestClient(edge_app_module.app)

    response = client.delete("/api/equipment-bindings/fitrace-edge-test-02")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "removed"
    assert payload["binding"]["node_id"] == "fitrace-edge-test-02"
    assert payload["binding"]["ble_target"] == "AA:BB:CC:DD:EE:02"
    assert payload["warnings"] == []

    # Assert on the complete recorded command list -- exactly one command,
    # ever: a per-MAC disconnect on the removed binding's own channel. No
    # disconnect_all, no connect, nothing on uart-2 either.
    assert recorded_commands == [("/dev/ttyAMA0", "disconnect", ("AA:BB:CC:DD:EE:02",))]

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    saved_node_ids = [b["node_id"] for b in saved["equipment_bindings"]]
    assert saved_node_ids == ["fitrace-edge-test-01", "fitrace-edge-test-03"]


def test_remove_single_equipment_binding_uart_failure_still_removes_and_warns(
    monkeypatch, tmp_path
):
    # The operator's intent is to free the slot; a board that did not
    # answer must not leave them stuck re-clicking a control that keeps
    # failing. The binding is removed from config regardless, and the
    # failure surfaces as a warning, not an HTTP error.
    config_path = tmp_path / "config.json"
    _write_multi_binding_config(config_path)

    class FailingAntennaCommandRunner:
        def run(self, request):
            raise RuntimeError("UART port busy")

    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        edge_app_module, "antenna_command_runner", FailingAntennaCommandRunner()
    )
    client = TestClient(edge_app_module.app)

    response = client.delete("/api/equipment-bindings/fitrace-edge-test-01")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "removed_with_warnings"
    assert any("UART port busy" in warning for warning in payload["warnings"])
    assert payload["binding"]["node_id"] == "fitrace-edge-test-01"

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    saved_node_ids = [b["node_id"] for b in saved["equipment_bindings"]]
    assert "fitrace-edge-test-01" not in saved_node_ids
    assert set(saved_node_ids) == {"fitrace-edge-test-02", "fitrace-edge-test-03"}


def test_remove_single_equipment_binding_unknown_node_id_is_404(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    _write_multi_binding_config(config_path)
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    client = TestClient(edge_app_module.app)

    response = client.delete("/api/equipment-bindings/does-not-exist")

    assert response.status_code == 404
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert len(saved["equipment_bindings"]) == 3


def test_remove_single_equipment_binding_requires_admin_token(monkeypatch):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "admin-secret")
    client = TestClient(edge_app_module.app)

    response = client.delete("/api/equipment-bindings/fitrace-edge-test-01")

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
    monkeypatch.setattr(
        edge_app_module,
        "central_hub_reachable",
        lambda host, port, timeout_sec=2.0: True,
    )
    client = TestClient(edge_app_module.app)

    assert client.get("/api/ble/scan").status_code == 401
    assert client.get("/api/wifi/status").status_code == 401
    assert client.get("/api/central-hub").status_code == 401
    assert (
        client.post(
            "/api/central-hub",
            json={"host": "localhost", "port": 1883},
        ).status_code
        == 401
    )

    headers = {"X-FitRace-Admin-Token": "admin-secret"}
    assert client.get("/api/ble/scan", headers=headers).status_code == 200
    assert client.get("/api/wifi/status", headers=headers).status_code == 200
    assert client.get("/api/central-hub", headers=headers).status_code == 200


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


def test_central_hub_status_reports_configured_host_and_reachability(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "mqtt_host": "fitrace-hub.local",
                "mqtt_port": 1883,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        edge_app_module,
        "central_hub_reachable",
        lambda host, port, timeout_sec=2.0: host == "fitrace-hub.local"
        and port == 1883,
    )
    client = TestClient(edge_app_module.app)

    response = client.get("/api/central-hub")

    assert response.status_code == 200
    assert response.json() == {
        "host": "fitrace-hub.local",
        "port": 1883,
        "status": "connected",
        "reachable": True,
    }


def test_configure_central_hub_verifies_then_saves_and_restarts_runtime(
    monkeypatch, tmp_path
):
    from fitrace_common.power_manager import PowerActionResult

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "mqtt_host": "localhost",
                "mqtt_port": 1883,
                "equipment_bindings": [],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        edge_app_module,
        "central_hub_reachable",
        lambda host, port, timeout_sec=2.0: calls.append(("probe", host, port)) or True,
    )

    class FakeServiceRestartManager:
        def restart_service(self):
            calls.append(("restart",))
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

    monkeypatch.setattr(
        edge_app_module, "service_restart_manager", FakeServiceRestartManager()
    )
    client = TestClient(edge_app_module.app)

    response = client.post(
        "/api/central-hub",
        json={"host": "192.168.0.10", "port": 1883},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "saved"
    assert payload["reachable"] is True
    assert payload["config"]["mqtt_host"] == "192.168.0.10"
    assert payload["config"]["mqtt_port"] == 1883
    assert payload["restart"]["executed"] is True
    assert calls == [("probe", "192.168.0.10", 1883), ("restart",)]
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["mqtt_host"] == "192.168.0.10"
    assert saved["mqtt_port"] == 1883


def test_configure_central_hub_rolls_back_when_runtime_restart_is_not_executed(
    monkeypatch, tmp_path
):
    from fitrace_common.power_manager import PowerActionResult

    original = {
        "node_id": "fitrace-edge-test",
        "mqtt_host": "localhost",
        "mqtt_port": 1883,
        "equipment_bindings": [],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        edge_app_module,
        "central_hub_reachable",
        lambda host, port, timeout_sec=2.0: True,
    )

    class FakeServiceRestartManager:
        def restart_service(self):
            return PowerActionResult(
                action="restart-service",
                target="edge",
                dry_run=True,
                command=["sudo", "systemctl", "restart", "fitracestudio-edge.service"],
                executed=False,
            )

    monkeypatch.setattr(
        edge_app_module, "service_restart_manager", FakeServiceRestartManager()
    )
    client = TestClient(edge_app_module.app)

    response = client.post(
        "/api/central-hub",
        json={"host": "192.168.0.130", "port": 1883},
    )

    assert response.status_code == 503
    assert "restart" in response.json()["detail"].lower()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["mqtt_host"] == original["mqtt_host"]
    assert saved["mqtt_port"] == original["mqtt_port"]


def test_configure_central_hub_rejects_unreachable_address_without_saving(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "config.json"
    original = {
        "node_id": "fitrace-edge-test",
        "mqtt_host": "localhost",
        "mqtt_port": 1883,
    }
    config_path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(
        edge_app_module,
        "central_hub_reachable",
        lambda host, port, timeout_sec=2.0: False,
    )
    client = TestClient(edge_app_module.app)

    response = client.post(
        "/api/central-hub",
        json={"host": "192.168.0.99", "port": 1883},
    )

    assert response.status_code == 503
    assert "192.168.0.99:1883" in response.json()["detail"]
    assert json.loads(config_path.read_text(encoding="utf-8")) == original


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


def test_edge_monitor_events_kind_telemetry_survives_a_scan_noise_flood(
    monkeypatch, tmp_path
):
    # Regression for the scan-flood risk noted while fixing the card-flips-
    # to-waiting bug: a pairing scan writes many "uart"/"rx" device-
    # discovery lines in a burst (a real scan on this hardware produced 40
    # devices). Without server-side filtering, those noise lines alone can
    # fill the trailing `limit` window and evict a still-connected
    # machine's telemetry event even though it is recent. kind=telemetry
    # must keep only telemetry-relevant events (MQTT publishes + UART
    # telemetry rows) in the window, immune to that flood.
    event_log = edge_app_module.EdgeEventLog(tmp_path / "edge_monitor.jsonl")
    event_log.record(
        "uart",
        "rx",
        channel="uart-1",
        message="FTMS:AA:BB:CC:DD:EE:01,BIKE,{}",
        parsed={"type": "telemetry", "address": "AA:BB:CC:DD:EE:01"},
    )
    event_log.record(
        "mqtt",
        "publish",
        topic="gym/telemetry/node-01",
        payload={"node_id": "node-01", "mac_address": "AA:BB:CC:DD:EE:02"},
    )
    for index in range(10):
        event_log.record(
            "uart",
            "rx",
            channel="uart-1",
            message=f"DEVICE:AA:BB:CC:DD:EE:{index:02d},-40,Bike,BIKE",
            parsed={"type": "device", "address": f"AA:BB:CC:DD:EE:{index:02d}"},
        )
    monkeypatch.setattr(edge_app_module, "edge_event_log", event_log)
    client = TestClient(edge_app_module.app)

    response = client.get("/api/monitor/events?kind=telemetry&limit=1")

    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1
    assert events[0]["source"] == "mqtt"
    assert events[0]["topic"] == "gym/telemetry/node-01"

    response_all = client.get("/api/monitor/events?limit=200")
    kinds = {
        (event.get("parsed") or {}).get("type")
        for event in response_all.json()["events"]
    }
    assert "device" in kinds


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


def test_edge_operator_refresh_telemetry_harvests_uart_rows_by_mac():
    # Bug: while the web process runs a UART command (pairing scan,
    # connect_add, ...), AntennaCommandRunner holds the cross-process UART
    # lock for the whole session and reads every line, including FTMS
    # telemetry frames from machines that are already connected. Meanwhile
    # the runtime's own read loop loses the lock race and publishes nothing
    # to MQTT for that channel, so the card's MQTT-only liveness check goes
    # stale even though the telemetry is visibly still arriving (as
    # uart/rx events with parsed.type === "telemetry"). refreshTelemetry
    # must harvest those rows too, keyed by normalized MAC, not just MQTT
    # publishes.
    client = TestClient(edge_app_module.app)

    source = client.get("/").text

    harvest_start = source.index("function harvestTelemetryEvents(events)")
    harvest_end = source.index("async function refreshTelemetry()", harvest_start)
    harvest_fn = source[harvest_start:harvest_end]

    assert 'event.source === "uart" && event.direction === "rx"' in harvest_fn
    assert 'parsed.type !== "telemetry"' in harvest_fn
    assert "normalizeMac(parsed.address)" in harvest_fn
    assert "telemetryUartByMac.set(mac," in harvest_fn
    assert "instantaneous_speed_kph: parsed.instantaneous_speed_kph," in harvest_fn
    assert "power_watts: parsed.power_watts," in harvest_fn
    assert "rssi: parsed.rssi," in harvest_fn

    refresh_start = harvest_end
    refresh_end = source.index("// -- view switching", refresh_start)
    refresh_fn = source[refresh_start:refresh_end]
    assert 'adminFetch("/api/monitor/events?kind=telemetry&limit=200")' in refresh_fn
    assert "harvestTelemetryEvents(telemetryEvents);" in refresh_fn


def test_edge_operator_binding_card_liveness_counts_fresh_uart_row_as_live():
    # A previous test in this file (test_edge_operator_uart_monitor_shows_
    # latest_first_and_keeps_only_200_messages) was satisfied by a comment
    # containing the same substring it asserted on and silently stopped
    # protecting the behaviour it named. Assert here on the actual live
    # expression used by updateBindingCardLeaves, which a comment could not
    # contain verbatim, so a regression that drops the UART source from
    # liveness fails this test instead of just drifting the docs.
    client = TestClient(edge_app_module.app)

    source = client.get("/").text
    leaves_start = source.index("function updateBindingCardLeaves(")
    leaves_end = source.index("function formatEventTime(", leaves_start)
    leaves_fn = source[leaves_start:leaves_end]

    assert "telemetryUartByMac.get(normalizeMac(binding.ble_target))" in leaves_fn
    assert (
        "const mqttLive = Boolean(mqttPayload && telemetryAgeMs(mqttPayload) <= MONITOR_LIVE_WINDOW_MS);"
        in leaves_fn
    )
    assert (
        "const uartLive = Boolean(uartPayload && telemetryAgeMs(uartPayload) <= MONITOR_LIVE_WINDOW_MS);"
        in leaves_fn
    )
    assert "const live = mqttLive || uartLive;" in leaves_fn


def test_edge_operator_binding_card_prefers_fresh_mqtt_over_fresh_uart():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text
    leaves_start = source.index("function updateBindingCardLeaves(")
    leaves_end = source.index("function formatEventTime(", leaves_start)
    leaves_fn = source[leaves_start:leaves_end]

    assert (
        "const payload = mqttLive ? mqttPayload : (uartLive ? uartPayload : mqttPayload);"
        in leaves_fn
    )


def test_edge_operator_binding_card_metrics_fall_back_to_uart_row_when_mqtt_stale():
    client = TestClient(edge_app_module.app)

    source = client.get("/").text
    leaves_start = source.index("function updateBindingCardLeaves(")
    leaves_end = source.index("function formatEventTime(", leaves_start)
    leaves_fn = source[leaves_start:leaves_end]

    # The same `payload` variable (mqtt-when-fresh, else uart-when-fresh)
    # feeds every metric field, so a stale MQTT payload with a fresh UART
    # row still updates speed/power/rssi from the UART row.
    assert 'formatMetric(payload?.instantaneous_speed_kph, " kph", 1)' in leaves_fn
    assert 'formatMetric(payload?.power_watts, " W", 0)' in leaves_fn
    assert 'formatMetric(payload?.rssi, " dBm", 0)' in leaves_fn


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


def test_pairing_scan_first_flow_start_bind_connect_finish_happy_path(
    monkeypatch, tmp_path
):
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

    start_response = client.post("/api/pairing/start", json={"temp_connect": False})
    assert start_response.status_code == 200
    start_payload = start_response.json()
    assert start_payload["candidates"][0]["mac"] == "AA:BB:CC:DD:EE:05"

    # scan-first flow never issues a batch CONNECT or DISCONNECT:ALL
    assert all(c.command != "connect" for c in runner.calls)
    assert all(c.command != "disconnect_all" for c in runner.calls)

    bind_response = client.post(
        "/api/pairing/bind",
        json={
            "mac": "AA:BB:CC:DD:EE:05",
            "equipment_type": "fan_bike",
            "display_name": "Bike One",
        },
    )
    assert bind_response.status_code == 200
    bind_payload = bind_response.json()
    assert bind_payload["binding"]["equipment_id"] == "Bike One"
    assert bind_payload["binding"]["antenna_channel"] == "uart-1"
    assert "capacity" in bind_payload

    # session must still be observing after bind() -- not reset
    status_after_bind = client.get("/api/pairing/status").json()
    assert status_after_bind["state"] == "observing"

    connect_response = client.post(
        "/api/pairing/connect", json={"mac": "AA:BB:CC:DD:EE:05"}
    )
    assert connect_response.status_code == 200
    connect_payload = connect_response.json()
    assert connect_payload["outcome"] == "ok"
    assert connect_payload["connected"] is True

    finish_response = client.post("/api/pairing/finish", json={})
    assert finish_response.status_code == 200
    finish_payload = finish_response.json()
    assert finish_payload["status"] == "finished"
    assert "dry_run" in finish_payload["restart"]

    assert all(c.command != "connect" for c in runner.calls)
    assert all(c.command != "disconnect_all" for c in runner.calls)

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert any(
        binding["equipment_id"] == "Bike One" for binding in saved["equipment_bindings"]
    )
    assert client.get("/api/pairing/status").json() == {"state": "idle"}


def test_pairing_bind_endpoint_forwards_explicit_channel_choice(monkeypatch, tmp_path):
    # /api/pairing/bind must pass the operator's channel choice through to
    # PairingSession.bind() -- exercised end to end via the HTTP payload,
    # not just the usecase directly.
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "antenna_channels": [
                    {"id": "uart-1", "port": "/dev/ttyAMA0"},
                    {"id": "uart-2", "port": "/dev/ttyAMA4"},
                ],
                "equipment_bindings": [
                    {
                        "node_id": f"fitrace-edge-test-0{i}",
                        "equipment_id": f"ROW_{i}",
                        "equipment_type": "rowing_machine",
                        "ble_target": f"AA:BB:CC:DD:EE:1{i}",
                        "antenna_channel": "uart-1",
                    }
                    for i in range(3)
                ],
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

    start_response = client.post("/api/pairing/start", json={"temp_connect": False})
    assert start_response.status_code == 200
    candidate = start_response.json()["candidates"][0]
    assert candidate["channel_id"] == "uart-1"  # the only channel that heard it
    assert candidate["rssi_by_channel"] == {"uart-1": -40}

    # uart-1 (the strongest/only-heard channel) is already full -- the
    # operator picks uart-2, which never heard this device but has room.
    bind_response = client.post(
        "/api/pairing/bind",
        json={
            "mac": "AA:BB:CC:DD:EE:05",
            "equipment_type": "fan_bike",
            "display_name": "Bike One",
            "channel": "uart-2",
        },
    )
    assert bind_response.status_code == 200
    assert bind_response.json()["binding"]["antenna_channel"] == "uart-2"

    connect_response = client.post(
        "/api/pairing/connect", json={"mac": "AA:BB:CC:DD:EE:05"}
    )
    assert connect_response.status_code == 200
    assert connect_response.json()["channel_id"] == "uart-2"
    connect_add_calls = [c for c in runner.calls if c.command == "connect_add"]
    assert [c.port for c in connect_add_calls] == ["/dev/ttyAMA4"]  # uart-2's port


def test_pairing_bind_endpoint_rejects_full_channel_choice(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "antenna_channels": [
                    {"id": "uart-1", "port": "/dev/ttyAMA0"},
                    {"id": "uart-2", "port": "/dev/ttyAMA4"},
                ],
                "equipment_bindings": [
                    {
                        "node_id": f"fitrace-edge-test-0{i}",
                        "equipment_id": f"ROW_{i}",
                        "equipment_type": "rowing_machine",
                        "ble_target": f"AA:BB:CC:DD:EE:1{i}",
                        "antenna_channel": "uart-2",
                    }
                    for i in range(3)
                ],
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
    client.post("/api/pairing/start", json={"temp_connect": False})

    bind_response = client.post(
        "/api/pairing/bind",
        json={
            "mac": "AA:BB:CC:DD:EE:05",
            "equipment_type": "fan_bike",
            "channel": "uart-2",
        },
    )

    assert bind_response.status_code == 400
    assert "no free configured binding slot" in bind_response.json()["detail"]


def test_pairing_scan_first_endpoints_require_admin_token(monkeypatch, tmp_path):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "admin-secret")
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
                    "address": "AA:BB:CC:DD:EE:09",
                    "rssi": -40,
                    "name": "Bike A",
                }
            ],
        }
    )
    _install_fresh_pairing_session(monkeypatch, tmp_path, runner)
    client = TestClient(edge_app_module.app)

    assert (
        client.post("/api/pairing/start", json={"temp_connect": False}).status_code
        == 401
    )
    assert (
        client.post(
            "/api/pairing/bind",
            json={"mac": "AA:BB:CC:DD:EE:09", "equipment_type": "fan_bike"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/pairing/connect", json={"mac": "AA:BB:CC:DD:EE:09"}
        ).status_code
        == 401
    )
    assert client.post("/api/pairing/finish", json={}).status_code == 401

    headers = {"X-FitRace-Admin-Token": "admin-secret"}
    start_response = client.post(
        "/api/pairing/start", json={"temp_connect": False}, headers=headers
    )
    assert start_response.status_code == 200

    bind_response = client.post(
        "/api/pairing/bind",
        json={"mac": "AA:BB:CC:DD:EE:09", "equipment_type": "fan_bike"},
        headers=headers,
    )
    assert bind_response.status_code == 200

    connect_response = client.post(
        "/api/pairing/connect",
        json={"mac": "AA:BB:CC:DD:EE:09"},
        headers=headers,
    )
    assert connect_response.status_code == 200

    finish_response = client.post("/api/pairing/finish", json={}, headers=headers)
    assert finish_response.status_code == 200


# -- scan-once operator worklist (frontend) ---------------------------------
#
# commit 216717b added the scan-first bind()/connect()/finish() backend;
# these tests cover operator.html's rebuild on top of it: one scan, then a
# worklist of every candidate that scan found, each bound and connected
# independently instead of the old one-scan-per-device wizard.


def test_edge_pairing_start_uses_scan_only_and_shows_progress_message():
    # The scan itself takes 20-40s on real hardware and used to leave the
    # page showing nothing but a greyed button -- start() must now request a
    # connectionless scan (temp_connect: false) and show a progress message
    # for the whole time the request is in flight.
    client = TestClient(edge_app_module.app)

    source = client.get("/").text
    start_fn_start = source.index("async function startPairing()")
    start_fn_end = source.index("async function cancelPairing()", start_fn_start)
    start_fn = source[start_fn_start:start_fn_end]

    assert 'adminFetch("/api/pairing/start"' in start_fn
    # Assert on the request body itself, not the bare "temp_connect: false"
    # substring -- a nearby comment carries that same text, so the loose
    # form stays green even if the flag is dropped from the POST and the
    # scan silently reverts to the temp-connecting legacy behaviour.
    assert "body: JSON.stringify({ temp_connect: false })" in start_fn
    assert "showPairingScanning(true)" in start_fn
    assert "showPairingScanning(false)" in start_fn
    scanning_show_index = start_fn.index("showPairingScanning(true)")
    fetch_index = start_fn.index('adminFetch("/api/pairing/start"')
    assert scanning_show_index < fetch_index  # message shown before the request fires

    assert 'id="pairing-scanning"' in source
    assert 'data-i18n="pairing.scanning"' in source
    assert '"pairing.scanning": "Scanning every antenna channel' in source
    assert '"pairing.scanning": "正在掃描所有天線通道' in source


def test_edge_pairing_worklist_keeps_every_scanned_candidate_with_per_row_controls():
    # The old page forced one SCAN per device (each confirm() ended the
    # session) and dropped "unranked" (never-connected) candidates into a
    # dimmed, separate group. The scan-once worklist must render every
    # candidate start() returned, each with its own type picker, name input,
    # save&connect, and "which one is this?" control.
    client = TestClient(edge_app_module.app)

    source = client.get("/").text
    assert 'id="pairing-worklist"' in source
    assert "unranked" not in source
    assert "candidate-moving" not in source

    init_start = source.index("function initPairingWorklist(candidates)")
    init_end = source.index("async function bindCandidate(", init_start)
    init_fn = source[init_start:init_end]
    assert "(candidates || []).forEach((candidate) => {" in init_fn

    type_grid_start = source.index("function typeGridHtml(meta)")
    row_end = source.index("function renderPairingWorklist()", type_grid_start)
    row_and_type_grid_fn = source[type_grid_start:row_end]
    assert 'data-role="type-chip"' in row_and_type_grid_fn
    assert 'data-role="display-name"' in row_and_type_grid_fn
    assert 'data-role="save-connect"' in row_and_type_grid_fn
    assert 'data-role="probe"' in row_and_type_grid_fn
    assert 't("pairing.save_connect")' in row_and_type_grid_fn
    assert 't("pairing.which_one")' in row_and_type_grid_fn
    assert "typeGridHtml(meta)" in row_and_type_grid_fn


def test_edge_pairing_save_and_connect_binds_then_connects_and_retry_never_rebinds():
    # bind() persists the MAC into the board's NVS on its own -- a retry that
    # only failed to connect must call connect() again, never bind() again
    # (which would 400 as an already-bound MAC per pairing_session.bind()).
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    save_start = source.index("async function saveAndConnect(mac)")
    save_end = source.index("async function probeCandidate(", save_start)
    save_fn = source[save_start:save_end]
    assert (
        "bindCandidate(mac, meta.equipmentType, meta.displayName, meta.channelId)"
        in save_fn
    )
    assert "await retryConnect(mac);" in save_fn
    bind_call_index = save_fn.index("bindCandidate(")
    retry_call_index = save_fn.index("await retryConnect(mac);")
    assert bind_call_index < retry_call_index

    bind_fn_start = source.index(
        "async function bindCandidate(mac, equipmentType, displayName, channelId)"
    )
    bind_fn_end = source.index("async function connectCandidateOnly(", bind_fn_start)
    bind_fn = source[bind_fn_start:bind_fn_end]
    assert 'adminFetch("/api/pairing/bind"' in bind_fn
    assert "/api/pairing/connect" not in bind_fn

    retry_fn_start = source.index("async function retryConnect(mac)")
    retry_fn_end = source.index("async function saveAndConnect(", retry_fn_start)
    retry_fn = source[retry_fn_start:retry_fn_end]
    assert "connectCandidateOnly(mac)" in retry_fn
    assert "bindCandidate" not in retry_fn
    assert "/api/pairing/bind" not in retry_fn

    connect_only_start = source.index("async function connectCandidateOnly(mac)")
    connect_only_end = source.index("async function retryConnect(", connect_only_start)
    connect_only_fn = source[connect_only_start:connect_only_end]
    assert 'adminFetch("/api/pairing/connect"' in connect_only_fn
    assert "/api/pairing/bind" not in connect_only_fn

    # "which one is this?" must connect only -- it must never bind either.
    probe_start = source.index("async function probeCandidate(mac)")
    probe_end = source.index("function handlePairingWorklistClick(", probe_start)
    probe_fn = source[probe_start:probe_end]
    assert "connectCandidateOnly(mac)" in probe_fn
    assert "bindCandidate" not in probe_fn

    # the retry-connect button (shown after a failed connect) must dispatch
    # to retryConnect only, never back through saveAndConnect/bindCandidate.
    click_start = source.index("function handlePairingWorklistClick(event)")
    click_end = source.index("function handlePairingWorklistInput(event)", click_start)
    click_fn = source[click_start:click_end]
    assert 'role === "retry-connect"' in click_fn
    retry_branch = click_fn[click_fn.index('role === "retry-connect"') :]
    assert "retryConnect(mac)" in retry_branch
    assert "bindCandidate" not in click_fn


def test_edge_pairing_worklist_renders_distinct_outcomes_for_every_connect_result():
    # ok/already_exists (connected), full (board at its 3-link limit),
    # unknown_cmd (firmware too old), and unrecognized/HTTP error (failed)
    # must each render distinctly so the operator knows what to do next.
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    outcome_start = source.index("function outcomeMessageHtml(outcome)")
    outcome_end = source.index("function worklistRowHtml(meta)", outcome_start)
    outcome_fn = source[outcome_start:outcome_end]
    assert 'ok: ["ok", "pairing.outcome_ok"]' in outcome_fn
    assert 'already_exists: ["ok", "pairing.outcome_already_exists"]' in outcome_fn
    assert 'full: ["error", "pairing.outcome_full"]' in outcome_fn
    assert 'unknown_cmd: ["error", "pairing.outcome_unknown_cmd"]' in outcome_fn
    assert 'unrecognized: ["error", "pairing.outcome_failed"]' in outcome_fn
    assert 'error: ["error", "pairing.outcome_failed"]' in outcome_fn

    row_start = source.index("function worklistRowHtml(meta)")
    row_end = source.index("function renderPairingWorklist()", row_start)
    row_fn = source[row_start:row_end]
    assert "outcomeMessageHtml(meta.connectOutcome)" in row_fn
    # a bind()-level failure (e.g. HTTP error before connect() ever runs)
    # renders separately from a connect() outcome, so the operator can tell
    # "never saved" apart from "saved but couldn't connect".
    assert "meta.bindError" in row_fn
    assert '"pairing.bind_failed"' in row_fn

    locales_dir = Path(edge_app_module.__file__).resolve().parent.parent / "locales"
    en = json.loads((locales_dir / "en.json").read_text(encoding="utf-8"))
    distinct_outcome_keys = {
        "pairing.outcome_ok",
        "pairing.outcome_already_exists",
        "pairing.outcome_full",
        "pairing.outcome_unknown_cmd",
        "pairing.outcome_failed",
    }
    strings = {en[key] for key in distinct_outcome_keys}
    assert len(strings) == len(distinct_outcome_keys)
    assert en["pairing.bind_failed"] not in strings


def test_edge_pairing_capacity_comes_from_bind_response_not_channel_accepts_new():
    # channel_accepts_new is computed once at scan time and goes stale as
    # bindings land during the session; bind()'s returned capacity is
    # recomputed after every save, so it -- not the candidate flag -- must
    # drive the per-channel "full" state shown to the operator.
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    assert "channel_accepts_new" not in source

    apply_start = source.index("function applyPairingCapacity(capacity)")
    apply_end = source.index("function channelFreeSlots(channelId)", apply_start)
    apply_fn = source[apply_start:apply_end]
    assert "pairingCapacity = capacity;" in apply_fn

    slots_start = source.index("function channelFreeSlots(channelId)")
    slots_end = source.index("function ftmsHintsForMac(", slots_start)
    slots_fn = source[slots_start:slots_end]
    assert "pairingCapacity && pairingCapacity.per_channel" in slots_fn

    row_start = source.index("function worklistRowHtml(meta)")
    row_end = source.index("function renderPairingWorklist()", row_start)
    row_fn = source[row_start:row_end]
    assert "channelFreeSlots(meta.channelId)" in row_fn

    start_fn_start = source.index("async function startPairing()")
    start_fn_end = source.index("async function cancelPairing()", start_fn_start)
    start_fn = source[start_fn_start:start_fn_end]
    assert "applyPairingCapacity(payload.capacity)" in start_fn

    save_start = source.index("async function saveAndConnect(mac)")
    save_end = source.index("async function probeCandidate(", save_start)
    save_fn = source[save_start:save_end]
    assert "applyPairingCapacity(bindResult.capacity)" in save_fn


def test_edge_pairing_done_button_posts_finish_and_returns_home():
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    assert 'id="pairing-done-btn"' in source
    assert (
        'document.getElementById("pairing-done-btn").addEventListener("click", finishPairing);'
        in source
    )

    finish_start = source.index("async function finishPairing()")
    finish_end = source.index(
        "// -- best-effort session cleanup on navigate-away", finish_start
    )
    finish_fn = source[finish_start:finish_end]
    assert 'adminFetch("/api/pairing/finish"' in finish_fn
    assert "restart: true" in finish_fn
    assert "await goHome();" in finish_fn


def test_edge_pairing_view_confirm_and_its_js_are_gone():
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    assert "view-confirm" not in source
    assert "confirmPairing" not in source
    assert "selectCandidate(" not in source
    assert "renderConfirmView" not in source
    assert "updateConfirmHeroMetric" not in source
    assert "ftmsHintsForCandidate" not in source
    assert "renderTypeGrid()" not in source
    assert "updateConfirmSubmitState" not in source
    assert 'id="confirm-submit-btn"' not in source
    assert 'id="confirm-name-input"' not in source


def test_edge_pairing_worklist_i18n_keys_present_in_both_locales():
    locales_dir = Path(edge_app_module.__file__).resolve().parent.parent / "locales"
    en = json.loads((locales_dir / "en.json").read_text(encoding="utf-8"))
    zh_tw = json.loads((locales_dir / "zh_tw.json").read_text(encoding="utf-8"))

    new_keys = {
        "pairing.scanning",
        "pairing.save_connect",
        "pairing.saving",
        "pairing.connecting",
        "pairing.which_one",
        "pairing.probing",
        "pairing.retry_connect",
        "pairing.bind_failed",
        "pairing.outcome_ok",
        "pairing.outcome_already_exists",
        "pairing.outcome_full",
        "pairing.outcome_unknown_cmd",
        "pairing.outcome_failed",
        "pairing.done",
        "pairing.finish_failed",
    }
    for key in new_keys:
        assert key in en, key
        assert key in zh_tw, key
    assert set(en.keys()) == set(zh_tw.keys())


def test_edge_pairing_worklist_row_explains_why_save_is_disabled():
    # Problem 1 from the field: a candidate with no equipment type tapped yet
    # rendered a disabled save button with nothing on the row explaining why.
    # typeGridHtml only ever auto-selects a type when ftmsHintsForMac returns
    # exactly one hint, and hints come from telemetryByMac -- a freshly
    # scanned, not-yet-connected candidate has no telemetry, so in practice a
    # type is never auto-selected and every row needs a manual tap.
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    row_start = source.index("function worklistRowHtml(meta)")
    row_end = source.index("function renderPairingWorklist()", row_start)
    row_fn = source[row_start:row_end]

    # Assert on the actual elseif condition and the key it renders, not a
    # loose substring a nearby comment could also satisfy.
    assert "} else if (!bound && !meta.equipmentType) {" in row_fn
    missing_type_branch = row_fn[
        row_fn.index("} else if (!bound && !meta.equipmentType) {") :
    ]
    assert 't("confirm.select_type_hint")' in missing_type_branch[:700]

    # Board-full is the harder blocker (needs a device removed; missing type
    # is fixed with one tap), so its branch must be reachable first.
    full_branch_index = row_fn.index("} else if (!bound && channelFull) {")
    missing_type_branch_index = row_fn.index(
        "} else if (!bound && !meta.equipmentType) {"
    )
    assert full_branch_index < missing_type_branch_index


def test_edge_pairing_worklist_full_channel_reason_wins_over_missing_type():
    # A row on a full channel with no type picked yet must show the
    # board-full reason, not the pick-a-type reason -- the operator can't act
    # on the type picker until a slot exists anyway.
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    row_start = source.index("function worklistRowHtml(meta)")
    row_end = source.index("function renderPairingWorklist()", row_start)
    row_fn = source[row_start:row_end]

    assert "const channelFull = freeSlots === 0;" in row_fn
    channel_full_computed_index = row_fn.index("const channelFull = freeSlots === 0;")
    full_branch_index = row_fn.index("} else if (!bound && channelFull) {")
    missing_type_branch_index = row_fn.index(
        "} else if (!bound && !meta.equipmentType) {"
    )
    # channelFull must be computed before either branch can consult it, and
    # the full-channel branch must come first in the elseif chain so it wins
    # whenever both conditions are true for the same row.
    assert channel_full_computed_index < full_branch_index < missing_type_branch_index


def test_edge_pairing_type_grid_has_its_own_required_heading():
    # Give the type grid a heading of its own -- via an i18n key, not a
    # hardcoded string -- so a row with no type selected unmistakably reads
    # as "waiting for you" rather than broken.
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    type_grid_start = source.index("function typeGridHtml(meta)")
    type_grid_end = source.index(
        "function outcomeMessageHtml(outcome)", type_grid_start
    )
    type_grid_fn = source[type_grid_start:type_grid_end]

    assert (
        'class="type-grid-heading" data-i18n="pairing.type_grid_heading"'
        in type_grid_fn
    )
    assert 't("pairing.type_grid_heading")' in type_grid_fn

    locales_dir = Path(edge_app_module.__file__).resolve().parent.parent / "locales"
    en = json.loads((locales_dir / "en.json").read_text(encoding="utf-8"))
    zh_tw = json.loads((locales_dir / "zh_tw.json").read_text(encoding="utf-8"))
    assert "pairing.type_grid_heading" in en
    assert "pairing.type_grid_heading" in zh_tw
    assert set(en.keys()) == set(zh_tw.keys())


def test_edge_pairing_page_load_restores_worklist_when_session_active():
    # A page reload or a tablet waking from sleep used to drop the operator
    # on the home view with an active pairing session still running
    # server-side and no visible way back. On load, after loadConfig(), the
    # page must check /api/pairing/status once and, if scanning/observing,
    # restore the worklist view and resume polling instead.
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    assert (
        "loadConfig({ populateHubFields: true }).then(restorePairingSessionIfActive);"
        in source
    )

    restore_start = source.index("async function restorePairingSessionIfActive()")
    restore_end = source.index("async function startPairing()", restore_start)
    restore_fn = source[restore_start:restore_end]

    assert 'adminFetch("/api/pairing/status")' in restore_fn
    assert (
        'statusPayload.state !== "scanning" && statusPayload.state !== "observing"'
        in restore_fn
    )
    status_check_index = restore_fn.index(
        'statusPayload.state !== "scanning" && statusPayload.state !== "observing"'
    )
    return_index = restore_fn.index("return;", status_check_index)
    assert status_check_index < return_index  # bails out before touching the UI

    # Re-seeds capacity/candidates via start(), which pairing_session.py's
    # start() re-returns unchanged (no re-scan, no re-CONNECT_ADD/CONNECT)
    # whenever a session is already active.
    assert 'adminFetch("/api/pairing/start"' in restore_fn
    assert "body: JSON.stringify({ temp_connect: false })" in restore_fn
    assert "initPairingWorklist(startPayload.candidates || [])" in restore_fn
    assert 'showView("pairing")' in restore_fn
    assert "startPairingPoll()" in restore_fn


def test_edge_home_card_remove_control_requires_confirmation_naming_equipment():
    # A full antenna board used to be recoverable only by wiping every
    # binding on the node (DELETE /api/equipment-bindings). Each card now
    # gets its own destructive remove control that must confirm, showing
    # the equipment name, before it ever fires the request.
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    assert 'data-role="remove-binding"' in source

    remove_start = source.index("async function removeBinding(nodeId)")
    remove_end = source.index("\n    function ", remove_start)
    remove_fn = source[remove_start:remove_end]

    assert "window.confirm(t(" in remove_fn
    confirm_index = remove_fn.index("window.confirm(")
    fetch_index = remove_fn.index("adminFetch(`/api/equipment-bindings/")
    assert confirm_index < fetch_index  # confirmation gates the request

    assert 't("operator.card_remove_confirm", { name })' in remove_fn
    assert (
        'if (!window.confirm(t("operator.card_remove_confirm", { name }))) return;'
        in remove_fn
    )

    # DELETE, not the whole-node clear-all endpoint.
    assert 'method: "DELETE"' in remove_fn
    assert '"/api/equipment-bindings"' not in remove_fn

    click_wire_index = source.index(
        'document.getElementById("binding-cards").addEventListener("click"'
    )
    click_wire_end = source.index("\n    });", click_wire_index)
    click_wire = source[click_wire_index:click_wire_end]
    assert "closest('[data-role=\"remove-binding\"]')" in click_wire
    assert "removeBinding(target.dataset.nodeId)" in click_wire


def test_edge_home_card_remove_success_and_failure_both_refresh_config():
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    remove_start = source.index("async function removeBinding(nodeId)")
    remove_end = source.index("\n    function ", remove_start)
    remove_fn = source[remove_start:remove_end]

    assert "bindingRemoveState.set(nodeId," in remove_fn
    assert "await loadConfig();" in remove_fn
    # loadConfig() must run regardless of whether the DELETE succeeded or
    # threw, so a true request failure still refreshes the UI.
    try_index = remove_fn.index("try {")
    catch_index = remove_fn.index("} catch (error) {")
    load_config_index = remove_fn.rindex("await loadConfig();")
    assert try_index < catch_index < load_config_index


def test_edge_pairing_worklist_i18n_and_card_remove_keys_present_in_both_locales():
    locales_dir = Path(edge_app_module.__file__).resolve().parent.parent / "locales"
    en = json.loads((locales_dir / "en.json").read_text(encoding="utf-8"))
    zh_tw = json.loads((locales_dir / "zh_tw.json").read_text(encoding="utf-8"))

    new_keys = {
        "operator.card_remove",
        "operator.card_remove_confirm",
        "operator.card_remove_working",
        "operator.card_remove_success",
        "operator.card_remove_success_with_warning",
        "operator.card_remove_failed",
    }
    for key in new_keys:
        assert key in en, key
        assert key in zh_tw, key
    assert set(en.keys()) == set(zh_tw.keys())


def test_edge_pairing_channel_picker_offers_every_configured_channel_with_status():
    # A device the scan only heard on one full channel used to leave the
    # operator stuck, even when another configured channel had a free slot
    # -- channelOptionsForMeta must offer every configured channel, not just
    # the ones the (weak, ~8s) scan happened to hear the device on.
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    options_start = source.index("function channelOptionsForMeta(meta)")
    options_end = source.index("function preselectChannelId(meta)", options_start)
    options_fn = source[options_start:options_end]
    assert "edgeConfig?.antenna_channels" in options_fn
    assert "channelFreeSlots(channel.id) === 0" in options_fn
    # every configured channel is offered regardless of whether the scan
    # heard the device there -- "heard" is metadata, not a filter.
    assert "channels.filter" not in options_fn
    assert "heard," in options_fn

    picker_start = source.index("function channelPickerHtml(meta)")
    picker_end = source.index("function ftmsHintsForMac(mac)", picker_start)
    picker_fn = source[picker_start:picker_end]
    assert 'data-role="channel-chip"' in picker_fn
    assert 't("pairing.channel_picker_heading")' in picker_fn
    assert 't("pairing.channel_full_short")' in picker_fn
    assert 't("pairing.channel_not_heard")' in picker_fn
    assert '${option.full ? "disabled" : ""}' in picker_fn


def test_edge_pairing_channel_preselect_prefers_strongest_then_room_then_any_room():
    # Never silently prefer a weaker board over a stronger one that still
    # has room; only fall through to a not-heard channel when nothing heard
    # has room; the strongest-full case is what leaves "all channels full"
    # to take over (see the other test below).
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    preselect_start = source.index("function preselectChannelId(meta)")
    preselect_end = source.index("function effectiveChannelId(meta)", preselect_start)
    preselect_fn = source[preselect_start:preselect_end]

    assert "candidateOption && !candidateOption.full" in preselect_fn
    candidate_check_index = preselect_fn.index(
        "candidateOption && !candidateOption.full"
    )
    heard_sort_index = preselect_fn.index(
        "option.heard && !option.full && option.channelId !== meta.candidateChannelId"
    )
    unheard_index = preselect_fn.index("!option.heard && !option.full")
    assert candidate_check_index < heard_sort_index < unheard_index
    assert ".sort((a, b) => (b.rssi ?? -999) - (a.rssi ?? -999));" in preselect_fn

    effective_start = source.index("function effectiveChannelId(meta)")
    effective_end = source.index("function channelPickerHtml(meta)", effective_start)
    effective_fn = source[effective_start:effective_end]
    # sticky once resolved -- mirrors typeGridHtml's auto-select, so it does
    # not flip-flop as capacity elsewhere in the worklist changes.
    assert "if (meta.channelId) return meta.channelId;" in effective_fn
    assert "meta.channelId = preselectChannelId(meta);" in effective_fn


def test_edge_pairing_worklist_shows_switch_label_and_all_channels_full_message():
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    row_start = source.index("function worklistRowHtml(meta)")
    row_end = source.index("function renderPairingWorklist()", row_start)
    row_fn = source[row_start:row_end]

    assert "effectiveChannelId(meta);" in row_fn
    assert "const channelOptions = channelOptionsForMeta(meta);" in row_fn
    assert (
        "const allChannelsFull = channelOptions.length > 0 && channelOptions.every((option) => option.full);"
        in row_fn
    )
    assert "${channelPickerHtml(meta)}" in row_fn

    # all-channels-full must be checked (and therefore win) before the
    # single-selected-channel-full reason.
    assert "} else if (!bound && allChannelsFull) {" in row_fn
    all_full_index = row_fn.index("} else if (!bound && allChannelsFull) {")
    single_full_index = row_fn.index("} else if (!bound && channelFull) {")
    assert all_full_index < single_full_index
    assert (
        't("pairing.all_channels_full")'
        in row_fn[all_full_index : all_full_index + 600]
    )

    picker_start = source.index("function channelPickerHtml(meta)")
    picker_end = source.index("function ftmsHintsForMac(mac)", picker_start)
    picker_fn = source[picker_start:picker_end]
    assert "selected !== meta.candidateChannelId" in picker_fn
    assert 't("pairing.channel_switched"' in picker_fn


def test_edge_pairing_connect_outcome_flags_a_bind_to_an_unheard_channel():
    # CONNECT_ADD is accepted even for a channel that can't reach the
    # device, so a plain "Connected" outcome would silently look like
    # success when it never actually links. A bind to a channel absent from
    # rssi_by_channel must render distinctly once connect() comes back ok.
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    row_start = source.index("function worklistRowHtml(meta)")
    row_end = source.index("function renderPairingWorklist()", row_start)
    row_fn = source[row_start:row_end]

    assert (
        "Object.prototype.hasOwnProperty.call(meta.rssiByChannel, meta.channelId)"
        in row_fn
    )
    assert "const connectedOk = meta.connectOutcome" in row_fn
    assert "connectedOk && !boundChannelHeard" in row_fn
    assert 't("pairing.outcome_added_unheard"' in row_fn
    # the unheard-channel branch must be checked ahead of the plain outcome
    # renderer for the SAME bound-with-outcome condition, not a separate
    # unreachable branch after it.
    outcome_branch_index = row_fn.index("} else if (bound && meta.connectOutcome) {")
    unheard_check_index = row_fn.index(
        "connectedOk && !boundChannelHeard", outcome_branch_index
    )
    outcome_message_call_index = row_fn.index(
        "outcomeMessageHtml(meta.connectOutcome)", outcome_branch_index
    )
    assert outcome_branch_index < unheard_check_index < outcome_message_call_index


def test_edge_pairing_bind_and_restore_pass_channel_choice_through():
    client = TestClient(edge_app_module.app)
    source = client.get("/").text

    bind_fn_start = source.index(
        "async function bindCandidate(mac, equipmentType, displayName, channelId)"
    )
    bind_fn_end = source.index("async function connectCandidateOnly(", bind_fn_start)
    bind_fn = source[bind_fn_start:bind_fn_end]
    assert "channel: channelId || null" in bind_fn

    init_start = source.index("function initPairingWorklist(candidates)")
    init_end = source.index("async function bindCandidate(", init_start)
    init_fn = source[init_start:init_end]
    assert "candidateChannelId: candidate.channel_id," in init_fn
    assert "rssiByChannel: candidate.rssi_by_channel || {}," in init_fn

    restore_start = source.index("async function restorePairingSessionIfActive()")
    restore_end = source.index("async function startPairing()", restore_start)
    restore_fn = source[restore_start:restore_end]
    assert (
        "rowMeta.channelId = binding.antenna_channel || rowMeta.channelId;"
        in restore_fn
    )


def test_edge_pairing_channel_choice_i18n_keys_present_in_both_locales():
    locales_dir = Path(edge_app_module.__file__).resolve().parent.parent / "locales"
    en = json.loads((locales_dir / "en.json").read_text(encoding="utf-8"))
    zh_tw = json.loads((locales_dir / "zh_tw.json").read_text(encoding="utf-8"))

    new_keys = {
        "pairing.channel_picker_heading",
        "pairing.channel_switched",
        "pairing.channel_full_short",
        "pairing.channel_not_heard",
        "pairing.all_channels_full",
        "pairing.outcome_added_unheard",
    }
    for key in new_keys:
        assert key in en, key
        assert key in zh_tw, key
    assert set(en.keys()) == set(zh_tw.keys())
