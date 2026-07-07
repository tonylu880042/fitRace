from edge_node.domain.models import (
    AntennaChannelConfig,
    EdgeNodeConfig,
    EquipmentBinding,
)
from edge_node.main import _build_node_status, _is_authorized_node_command


def test_build_node_status_payload_from_edge_config():
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        mqtt_host="fitrace-hub.local",
        max_ftms_connections=5,
        available_channels=2,
        antenna_protocol_version="uart-ftms-json-v1.1",
        antenna_channels=[
            AntennaChannelConfig(
                id="uart-1",
                port="/dev/ttyAMA0",
                uart="UART0",
                tx_gpio="GPIO14",
                rx_gpio="GPIO15",
                baudrate=115200,
            ),
            AntennaChannelConfig(
                id="uart-2",
                port="/dev/ttyAMA4",
                uart="UART4",
                tx_gpio="GPIO12",
                rx_gpio="GPIO13",
                baudrate=115200,
                dtoverlay="uart4-pi5",
            ),
        ],
        equipment_bindings=[
            EquipmentBinding(
                node_id="fitrace-edge-01-bike-01",
                equipment_id="BIKE_01",
                equipment_type="fan_bike",
                ble_target="AA:BB:CC:DD:EE:01",
                antenna_channel="uart-1",
            )
        ],
    )

    status = _build_node_status(
        config,
        now_ms=lambda: 1_234_567,
        hostname="fitrace-edge-01",
        ip_address="192.168.26.101",
    )

    assert status["edge_node_id"] == "fitrace-edge-01"
    assert status["hostname"] == "fitrace-edge-01"
    assert status["ip"] == "192.168.26.101"
    assert status["status"] == "online"
    assert status["last_seen_epoch_ms"] == 1_234_567
    assert status["max_ftms_connections"] == 5
    assert status["available_channels"] == 2
    assert status["antenna_protocol_version"] == "uart-ftms-json-v1.1"
    assert status["antenna_auto_connect"] is True
    assert status["antenna_channels"] == [
        {
            "id": "uart-1",
            "port": "/dev/ttyAMA0",
            "uart": "UART0",
            "tx_gpio": "GPIO14",
            "rx_gpio": "GPIO15",
            "baudrate": 115200,
            "rtscts": False,
            "dtoverlay": None,
        },
        {
            "id": "uart-2",
            "port": "/dev/ttyAMA4",
            "uart": "UART4",
            "tx_gpio": "GPIO12",
            "rx_gpio": "GPIO13",
            "baudrate": 115200,
            "rtscts": False,
            "dtoverlay": "uart4-pi5",
        },
    ]
    assert status["equipment_streams"] == [
        {
            "node_id": "fitrace-edge-01-bike-01",
            "equipment_id": "BIKE_01",
            "equipment_type": "fan_bike",
            "ble_target": "AA:BB:CC:DD:EE:01",
            "mac_address": "AA:BB:CC:DD:EE:01",
            "status": "configured",
            "antenna_channel": "uart-1",
            "rssi": None,
            "last_telemetry_epoch_ms": None,
            "error_code": None,
        }
    ]


def test_node_command_requires_configured_token(monkeypatch):
    monkeypatch.delenv("FITRACE_NODE_COMMAND_TOKEN", raising=False)
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)

    assert _is_authorized_node_command({"action": "shutdown"}) is False


def test_node_command_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("FITRACE_NODE_COMMAND_TOKEN", "node-secret")

    assert (
        _is_authorized_node_command(
            {"action": "shutdown", "token": "wrong-secret"}
        )
        is False
    )


def test_node_command_accepts_expected_token(monkeypatch):
    monkeypatch.setenv("FITRACE_NODE_COMMAND_TOKEN", "node-secret")

    assert (
        _is_authorized_node_command(
            {"action": "shutdown", "token": "node-secret"}
        )
        is True
    )
