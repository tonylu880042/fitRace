from hub_server.usecases.node_registry import NodeRegistry


def test_node_registry_updates_edge_status_and_marks_offline_after_timeout():
    now_ms = 1_000_000
    registry = NodeRegistry(now_ms=lambda: now_ms, offline_timeout_ms=10_000)

    registry.update_status(
        {
            "edge_node_id": "fitrace-edge-01",
            "hostname": "fitrace-edge-01",
            "ip": "192.168.26.101",
            "status": "online",
            "last_seen_epoch_ms": now_ms,
            "max_ftms_connections": 5,
            "available_channels": 2,
            "equipment_streams": [
                {
                    "node_id": "fitrace-edge-01-bike-01",
                    "equipment_id": "BIKE_01",
                    "equipment_type": "fan_bike",
                    "ble_target": "AA:BB:CC:DD:EE:01",
                    "mac_address": "AA:BB:CC:DD:EE:01",
                    "status": "configured",
                    "antenna_channel": "uart-1",
                }
            ],
        }
    )

    nodes = registry.list_nodes()
    assert nodes[0]["edge_node_id"] == "fitrace-edge-01"
    assert nodes[0]["status"] == "online"
    assert nodes[0]["equipment_streams"][0]["node_id"] == "fitrace-edge-01-bike-01"
    assert nodes[0]["equipment_streams"][0]["mac_address"] == "AA:BB:CC:DD:EE:01"

    now_ms += 10_001
    stale_nodes = registry.list_nodes()
    assert stale_nodes[0]["status"] == "offline"


def test_node_registry_uses_topic_edge_id_when_payload_omits_identity():
    registry = NodeRegistry(now_ms=lambda: 2_000_000)

    registry.update_status(
        {
            "status": "online",
            "last_seen_epoch_ms": 2_000_000,
            "equipment_streams": [],
        },
        edge_node_id="fitrace-edge-02",
    )

    nodes = registry.list_nodes()
    assert nodes[0]["edge_node_id"] == "fitrace-edge-02"


def test_node_registry_updates_stream_health_from_telemetry():
    registry = NodeRegistry(now_ms=lambda: 3_000_000)
    registry.update_status(
        {
            "edge_node_id": "fitrace-edge-01",
            "status": "online",
            "last_seen_epoch_ms": 2_999_000,
            "equipment_streams": [
                {
                    "node_id": "fitrace-edge-01-01",
                    "equipment_id": "TREAD_01",
                    "equipment_type": "treadmill",
                    "status": "configured",
                }
            ],
        }
    )

    registry.update_telemetry(
        {
            "edge_node_id": "fitrace-edge-01",
            "node_id": "fitrace-edge-01-01",
            "equipment_id": "TREAD_01",
            "equipment_type": "treadmill",
            "mac_address": "AA:BB:CC:DD:EE:01",
            "rssi": -71,
            "timestamp_epoch_ms": 3_000_000,
        }
    )

    stream = registry.list_nodes()[0]["equipment_streams"][0]
    assert stream["status"] == "online"
    assert stream["last_telemetry_epoch_ms"] == 3_000_000
    assert stream["mac_address"] == "AA:BB:CC:DD:EE:01"
    assert stream["rssi"] == -71


def test_heartbeat_preserves_live_stream_telemetry_fields():
    # A config-only heartbeat arriving after telemetry must not blank the
    # stream's last_telemetry_epoch_ms/rssi — otherwise the console flickers
    # between "connected" and "no data" on every heartbeat.
    clock = {"now": 3_000_000}
    registry = NodeRegistry(now_ms=lambda: clock["now"])
    heartbeat = {
        "edge_node_id": "fitrace-edge-01",
        "status": "online",
        "equipment_streams": [
            {
                "node_id": "fitrace-edge-01-01",
                "equipment_id": "TREAD_01",
                "equipment_type": "treadmill",
                "status": "configured",
            }
        ],
    }
    registry.update_status(heartbeat)
    registry.update_telemetry(
        {
            "edge_node_id": "fitrace-edge-01",
            "node_id": "fitrace-edge-01-01",
            "equipment_id": "TREAD_01",
            "equipment_type": "treadmill",
            "rssi": -70,
            "timestamp_epoch_ms": 3_000_000,
        }
    )

    # Second heartbeat (same config, no telemetry fields) arrives later.
    clock["now"] = 3_001_000
    registry.update_status(heartbeat)

    stream = registry.list_nodes()[0]["equipment_streams"][0]
    assert stream["last_telemetry_epoch_ms"] == 3_000_000
    assert stream["rssi"] == -70
