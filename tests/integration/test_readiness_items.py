"""GET /api/race/readiness carries structured, localizable items next to the
(unchanged) English strings: blocking_items / warning_items run parallel to
blocking_issues / warnings, and every check has a code + params."""

import time

import pytest
from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi import app as app_module

client = TestClient(app_module.app)


def _reset_all():
    client.post("/api/race/reset")
    app_module.node_registry.clear()
    app_module.race_manager.set_start_countdown_sound_enabled(True)
    for sn in (1, 2, 3):
        app_module.race_manager.assign_station(sn, None)


@pytest.fixture(autouse=True)
def clean_race():
    _reset_all()
    yield
    _reset_all()


def _edge_online(station: int, node_id: str, *, offset_ms: int = 0, kind="treadmill"):
    now_ms = int(time.time() * 1000)
    app_module.node_registry.update_status(
        {
            "edge_node_id": f"edge-{station:02d}",
            "status": "online",
            "last_seen_epoch_ms": now_ms - offset_ms,
            "equipment_streams": [
                {
                    "node_id": node_id,
                    "equipment_id": f"M_{station:02d}",
                    "equipment_type": kind,
                    "status": "configured",
                    "last_telemetry_epoch_ms": now_ms,
                }
            ],
        }
    )
    app_module.race_manager.assign_station(station, node_id)


def _configure(**body):
    body.setdefault("race_type", "distance")
    body.setdefault("target_value", 500)
    assert client.post("/api/race/configure", json=body).status_code == 200


def _readiness() -> dict:
    res = client.get("/api/race/readiness")
    assert res.status_code == 200
    return res.json()


def _pairs(items):
    return [(i["code"], i["params"]) for i in items]


def _assert_parallel(payload):
    assert len(payload["blocking_items"]) == len(payload["blocking_issues"])
    assert len(payload["warning_items"]) == len(payload["warnings"])
    for item in payload["blocking_items"] + payload["warning_items"]:
        assert isinstance(item["code"], str) and item["code"]
        assert isinstance(item["params"], dict)
    for check in payload["checks"].values():
        assert isinstance(check["code"], str) and check["code"]
        assert isinstance(check["params"], dict)


def test_idle_race_without_config_blocks_with_state_and_no_config_codes():
    payload = _readiness()
    _assert_parallel(payload)
    assert payload["blocking_issues"][0] == (
        "Race state must be READY; current state is IDLE."
    )
    assert _pairs(payload["blocking_items"])[:2] == [
        ("state_not_ready", {"state": "IDLE"}),
        ("no_config", {}),
    ]
    assert payload["checks"]["state"]["code"] == "no_config"
    assert payload["checks"]["target"]["code"] == "target_missing"


def test_ready_individual_race_has_ok_check_codes_and_no_items():
    _edge_online(1, "node-1")
    _configure()
    payload = _readiness()
    _assert_parallel(payload)
    assert payload["ready"] is True
    assert payload["blocking_items"] == [] and payload["warning_items"] == []
    codes = {name: check["code"] for name, check in payload["checks"].items()}
    assert codes["state"] == "state_ok"
    assert codes["target"] == "target_ok"
    assert codes["registrations"] == "registrations_anonymous"
    assert codes["teams"] == "teams_individual"
    assert codes["sound"] == "sound_ok"
    assert codes["clock"] == "clock_ok"


def test_individual_race_without_stations_blocks():
    _configure()
    payload = _readiness()
    _assert_parallel(payload)
    assert ("no_stations_assigned", {}) in _pairs(payload["blocking_items"])
    assert payload["checks"]["stations"]["code"] == "stations_none_assigned"


def test_station_reasons_reuse_the_reason_codes_with_the_station_number():
    _configure()
    app_module.race_manager.assign_station(2, "ghost-node")
    payload = _readiness()
    _assert_parallel(payload)
    assert payload["blocking_issues"][-1].startswith("Station 2: assigned device")
    assert _pairs(payload["blocking_items"])[-1] == ("device_missing", {"station": 2})
    assert payload["checks"]["stations"]["code"] == "stations_none_online"


@pytest.mark.parametrize(
    "reason,code",
    [
        ("unassigned", "unassigned"),
        ("device_missing", "device_missing"),
        ("edge_offline", "edge_offline"),
        ("no_data", "no_data"),
        ("stale", "stale"),
        (None, "generic"),
        ("something_new", "generic"),
    ],
)
def test_station_block_item_codes_cover_every_reason(reason, code):
    item = app_module.station_block_item(4, reason)
    assert (item.code, item.params) == (code, {"station": 4})
    assert item.message == app_module.station_block_message(4, reason)


def test_offline_station_in_individual_race_is_a_warning_item():
    _edge_online(1, "node-1")
    app_module.race_manager.assign_station(2, "ghost-node")
    _configure()
    payload = _readiness()
    _assert_parallel(payload)
    assert payload["ready"] is True
    assert _pairs(payload["warning_items"]) == [("device_missing", {"station": 2})]
    check = payload["checks"]["stations"]
    assert (check["code"], check["params"]) == (
        "stations_partial",
        {"online": 1, "offline": 1},
    )


def test_clock_skew_warning_item_carries_edge_label_and_offset():
    _edge_online(1, "node-1", offset_ms=400)
    _configure()
    payload = _readiness()
    _assert_parallel(payload)
    (item,) = payload["warning_items"]
    assert item["code"] == "clock_skew"
    assert item["params"]["edge"] in payload["warnings"][0]
    assert item["params"]["offset_ms"] == int(
        payload["warnings"][0].split("clock offset ")[1].split("ms")[0]
    )
    assert abs(item["params"]["offset_ms"]) > 250
    assert payload["checks"]["clock"]["code"] == "clock_skew_summary"
    assert payload["checks"]["clock"]["params"] == {"count": 1}


def test_silent_start_is_a_warning_item():
    _edge_online(1, "node-1")
    _configure()
    app_module.race_manager.set_start_countdown_sound_enabled(False)
    payload = _readiness()
    _assert_parallel(payload)
    assert _pairs(payload["warning_items"]) == [("sound_disabled", {})]
    assert payload["checks"]["sound"]["code"] == "sound_silent"


def test_team_race_needs_two_teams_and_team_names():
    _edge_online(1, "node-1")
    _configure(competition_mode="team")
    app_module.race_manager.register_athlete(1, "Ann", team_name=None)
    payload = _readiness()
    _assert_parallel(payload)
    assert _pairs(payload["blocking_items"]) == [
        ("team_needs_two", {}),
        ("team_member_needs_name", {}),
    ]
    assert payload["checks"]["teams"]["code"] == "teams_review"


def test_relay_issues_carry_station_legs_and_member_count():
    _edge_online(1, "node-1")
    _edge_online(2, "node-2")
    _configure(competition_mode="relay", relay_legs=3)
    app_module.race_manager.register_athlete(
        1, None, team_name="Volt", relay_members=["a", "b"]
    )
    app_module.race_manager.register_athlete(
        2, None, team_name="", relay_members=["a", "b", "c"]
    )
    payload = _readiness()
    _assert_parallel(payload)
    assert _pairs(payload["blocking_items"]) == [
        ("relay_wrong_size", {"station": 1, "legs": 3, "count": 2}),
        ("relay_needs_name", {"station": 2}),
    ]
    assert payload["checks"]["teams"]["code"] == "relay_teams_review"


def test_relay_without_registrations_blocks_with_no_athletes_code():
    _edge_online(1, "node-1")
    _configure(competition_mode="relay", relay_legs=2)
    payload = _readiness()
    _assert_parallel(payload)
    assert _pairs(payload["blocking_items"]) == [("no_registered_athletes", {})]
    assert payload["checks"]["registrations"]["code"] == "registrations_none"


def test_ready_relay_check_counts_are_params():
    _edge_online(1, "node-1")
    _configure(competition_mode="relay", relay_legs=2)
    app_module.race_manager.register_athlete(
        1, None, team_name="Volt", relay_members=["a", "b"]
    )
    payload = _readiness()
    _assert_parallel(payload)
    assert payload["ready"] is True
    assert payload["checks"]["teams"]["params"] == {"count": 1}
    assert payload["checks"]["teams"]["code"] == "relay_teams_ready"
    assert payload["checks"]["registrations"]["params"] == {"count": 1}


MIXED = {
    "race_type": "mixed",
    "groups": [
        {
            "equipment_types": ["treadmill"],
            "race_type": "distance",
            "target_value": 800,
        },
        {"equipment_types": ["rower"], "race_type": "distance", "target_value": 500},
    ],
}


def test_mixed_race_group_items():
    _edge_online(1, "node-1", kind="treadmill")
    _edge_online(2, "node-2", kind="fan_bike")
    _configure(**MIXED)
    app_module.race_manager.update_active_node("node-1", "treadmill")
    app_module.race_manager.update_active_node("node-2", "fan_bike")
    app_module.race_manager.assign_station(3, "node-3")
    payload = _readiness()
    _assert_parallel(payload)
    assert _pairs(payload["blocking_items"])[:2] == [
        ("group_equipment_unmatched", {"station": 2, "equipment": "fan_bike"}),
        ("group_equipment_unknown", {"station": 3}),
    ]
    assert _pairs(payload["warning_items"]) == [
        ("group_empty", {"group": 2, "types": "rower"}),
        ("device_missing", {"station": 3}),
    ]
    assert payload["checks"]["groups"]["code"] == "groups_unmatched"
    assert payload["checks"]["groups"]["params"] == {"count": 2}


def test_invalid_target_and_duration_items():
    _edge_online(1, "node-1")
    _configure()
    app_module.race_manager.get_config().target_value = 0
    payload = _readiness()
    _assert_parallel(payload)
    assert ("target_invalid", {}) in _pairs(payload["blocking_items"])
    assert payload["checks"]["target"]["code"] == "target_invalid"

    _configure(race_type="time", target_value=0, duration_sec=60)
    app_module.race_manager.get_config().duration_sec = 0
    payload = _readiness()
    _assert_parallel(payload)
    assert ("duration_invalid", {}) in _pairs(payload["blocking_items"])
    assert payload["checks"]["target"]["code"] == "duration_invalid"
