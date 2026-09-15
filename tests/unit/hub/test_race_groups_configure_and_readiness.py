"""HTTP surface for mixed-race groups (Batch 1a, commit 3):
POST /api/race/configure accepting an optional `groups` list, and
GET /api/race/readiness gating a mixed race start on every assigned
participant station actually belonging to a group.

Follows the TestClient + direct race_manager setup pattern established in
tests/unit/hub/test_roster_api.py. Station "online" health (node_registry)
is irrelevant to the group checks under test here except in the one
fully-ready scenario, where it's faked the same way
tests/unit/hub/test_station_stream_health_display_fallback.py does.
"""

import time

from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi import app as app_module
from hub_server.infrastructure.fastapi.app import node_registry

client = TestClient(app_module.app)

MIXED_GROUPS_PAYLOAD = [
    {"equipment_types": ["treadmill"], "race_type": "distance", "target_value": 800.0},
    {"equipment_types": ["rower"], "race_type": "distance", "target_value": 500.0},
]


def _reset_all():
    client.post("/api/race/reset")
    for sn in (1, 2, 3):
        app_module.race_manager.assign_station(sn, None)


def setup_function(_):
    _reset_all()


def teardown_function(_):
    _reset_all()


# ---------------------------------------------------------------------------
# POST /api/race/configure
# ---------------------------------------------------------------------------


def test_configure_accepts_mixed_race_with_groups():
    res = client.post(
        "/api/race/configure",
        json={"race_type": "mixed", "groups": MIXED_GROUPS_PAYLOAD},
    )
    assert res.status_code == 200
    config = app_module.race_manager.get_config()
    assert config.race_type == "mixed"
    assert len(config.groups) == 2
    assert config.groups[0].equipment_types == ["treadmill"]
    assert config.groups[1].target_value == 500.0


def test_configure_rejects_overlapping_equipment_types_with_400():
    res = client.post(
        "/api/race/configure",
        json={
            "race_type": "mixed",
            "groups": [
                {
                    "equipment_types": ["treadmill"],
                    "race_type": "distance",
                    "target_value": 800.0,
                },
                {
                    "equipment_types": ["treadmill"],
                    "race_type": "distance",
                    "target_value": 500.0,
                },
            ],
        },
    )
    assert res.status_code == 400


def test_configure_rejects_mixed_race_with_fewer_than_two_groups():
    res = client.post(
        "/api/race/configure",
        json={
            "race_type": "mixed",
            "groups": [
                {
                    "equipment_types": ["treadmill"],
                    "race_type": "distance",
                    "target_value": 800.0,
                }
            ],
        },
    )
    assert res.status_code == 400


def test_configure_plain_race_without_groups_is_unaffected():
    res = client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 1000.0}
    )
    assert res.status_code == 200
    config = app_module.race_manager.get_config()
    assert config.race_type == "distance"
    assert config.groups == []


# ---------------------------------------------------------------------------
# GET /api/race/readiness
# ---------------------------------------------------------------------------


def test_readiness_blocks_station_whose_equipment_type_is_not_in_any_group():
    app_module.race_manager.assign_station(1, "node-1")
    app_module.race_manager.update_active_node("node-1", "fan_bike")
    client.post(
        "/api/race/configure",
        json={"race_type": "mixed", "groups": MIXED_GROUPS_PAYLOAD},
    )

    readiness = client.get("/api/race/readiness").json()
    assert readiness["ready"] is False
    assert any(
        "fan_bike" in issue and "not in any race group" in issue
        for issue in readiness["blocking_issues"]
    )
    assert readiness["checks"]["groups"]["status"] == "block"


def test_readiness_blocks_station_with_unknown_equipment_type():
    app_module.race_manager.assign_station(1, "node-1")
    # No update_active_node call -- equipment_type defaults to "unknown".
    client.post(
        "/api/race/configure",
        json={"race_type": "mixed", "groups": MIXED_GROUPS_PAYLOAD},
    )

    readiness = client.get("/api/race/readiness").json()
    assert readiness["ready"] is False
    assert any("unknown" in issue.lower() for issue in readiness["blocking_issues"])
    assert readiness["checks"]["groups"]["status"] == "block"


def test_readiness_warns_but_does_not_block_when_a_group_has_no_station():
    app_module.race_manager.assign_station(1, "node-1")
    app_module.race_manager.update_active_node("node-1", "treadmill")
    # No station assigned to the rower group at all.
    client.post(
        "/api/race/configure",
        json={"race_type": "mixed", "groups": MIXED_GROUPS_PAYLOAD},
    )

    readiness = client.get("/api/race/readiness").json()
    assert readiness["checks"]["groups"]["status"] == "warn"
    assert not any(
        "not in any race group" in issue for issue in readiness["blocking_issues"]
    )
    assert any(
        "no assigned station" in warning.lower() for warning in readiness["warnings"]
    )


def test_readiness_ok_when_every_station_matches_and_every_group_has_a_station(
    monkeypatch,
):
    app_module.race_manager.assign_station(1, "node-1")
    app_module.race_manager.assign_station(2, "node-2")
    app_module.race_manager.update_active_node("node-1", "treadmill")
    app_module.race_manager.update_active_node("node-2", "rower")

    now_ms = int(time.time() * 1000)
    raw_nodes = [
        {
            "edge_node_id": "edge-01",
            "status": "online",
            "equipment_streams": [
                {
                    "node_id": "node-1",
                    "equipment_id": "TM1",
                    "last_telemetry_epoch_ms": now_ms,
                },
                {
                    "node_id": "node-2",
                    "equipment_id": "RW1",
                    "last_telemetry_epoch_ms": now_ms,
                },
            ],
        }
    ]
    monkeypatch.setattr(node_registry, "list_nodes", lambda: raw_nodes)

    client.post(
        "/api/race/configure",
        json={"race_type": "mixed", "groups": MIXED_GROUPS_PAYLOAD},
    )
    readiness = client.get("/api/race/readiness").json()

    assert readiness["checks"]["groups"]["status"] == "ok"
    assert readiness["ready"] is True


def test_non_mixed_readiness_has_no_groups_key():
    app_module.race_manager.assign_station(1, "node-1")
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 100.0}
    )
    readiness = client.get("/api/race/readiness").json()
    assert "groups" not in readiness["checks"]
