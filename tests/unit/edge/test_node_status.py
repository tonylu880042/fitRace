from edge_node.domain.models import EdgeNodeConfig, EquipmentBinding
from edge_node.main import _build_node_status


def test_build_node_status_payload_from_edge_config():
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        mqtt_host="fitrace-hub.local",
        max_ftms_connections=5,
        available_channels=2,
        antenna_protocol_version="pending-hardware",
        equipment_bindings=[
            EquipmentBinding(
                node_id="fitrace-edge-01-bike-01",
                equipment_id="BIKE_01",
                equipment_type="fan_bike",
                ble_target="mock",
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
    assert status["antenna_protocol_version"] == "pending-hardware"
    assert status["equipment_streams"] == [
        {
            "node_id": "fitrace-edge-01-bike-01",
            "equipment_id": "BIKE_01",
            "equipment_type": "fan_bike",
            "status": "configured",
            "antenna_channel": "uart-1",
            "rssi": None,
            "last_telemetry_epoch_ms": None,
            "error_code": None,
        }
    ]
