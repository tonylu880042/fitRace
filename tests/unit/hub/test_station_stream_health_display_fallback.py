"""station_stream_health's node_display_name must fall back through
display_name -> equipment_id -> node_id, matching the same three-rung
chain used by hub_server/usecases/node_display_names.py.

NodeRegistry._add_display_names always composes a display_name for every
stream it returns (falling back to equipment_id or node_id internally), so
in normal operation station_stream_health never actually sees a stream
missing display_name. This test bypasses that by monkeypatching
node_registry.list_nodes to return a raw stream the way it could arrive
from any other collaborator (or a future refactor of NodeRegistry) that
does not run the composition pass -- proving station_stream_health's own
fallback chain is genuine and not just borrowed from NodeRegistry.
"""

from hub_server.infrastructure.fastapi.app import node_registry, station_stream_health


def test_node_display_name_falls_back_to_equipment_id_before_node_id(monkeypatch):
    raw_nodes = [
        {
            "edge_node_id": "fitrace-edge-01",
            "status": "online",
            "equipment_streams": [
                {
                    "node_id": "fitrace-edge-01-01",
                    "equipment_id": "Vmax53932",
                    "last_telemetry_epoch_ms": 10_000_000_000_000,
                }
            ],
        }
    ]
    monkeypatch.setattr(node_registry, "list_nodes", lambda: raw_nodes)

    health = station_stream_health("fitrace-edge-01-01")

    assert health["node_display_name"] == "Vmax53932"
