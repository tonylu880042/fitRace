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
