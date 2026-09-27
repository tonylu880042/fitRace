"""HTTP surface for the roster/heat feature: GET/POST /api/roster*.

Every roster endpoint requires admin auth (test_roster_endpoints_require_
admin_token_when_configured). The interesting behaviour is next-heat: it
must leave the race READY with the SAME saved config after a STOPPED race,
clear old registrations, and register the newly loaded heat onto the
currently assigned stations in ascending order -- see /api/roster/next-heat
in hub_server/infrastructure/fastapi/app.py, which reuses configure_race's
and reset_race's own logic (apply_race_config/reset_race_state) rather than
duplicating it.
"""

from fastapi.testclient import TestClient

from hub_server.domain.models import RaceConfig, RaceState
from hub_server.infrastructure.fastapi import app as app_module

client = TestClient(app_module.app)


def _reset_all():
    client.post("/api/race/reset")
    app_module.roster_manager.clear()
    app_module.race_manager.assign_station(1, None)
    app_module.race_manager.assign_station(2, None)


def _assign_two_stations():
    app_module.race_manager.assign_station(1, "node-1")
    app_module.race_manager.assign_station(2, "node-2")


def setup_function(_):
    _reset_all()


def teardown_function(_):
    _reset_all()


def test_get_roster_empty_by_default():
    res = client.get("/api/roster")
    assert res.status_code == 200
    body = res.json()
    assert body["entries"] == []
    assert body["counts"] == {"pending": 0, "loaded": 0, "done": 0, "absent": 0}


def test_import_roster_success():
    res = client.post(
        "/api/roster/import", json={"csv": "name,division\nAlice,men\nBob,women\n"}
    )
    assert res.status_code == 200
    body = res.json()
    assert [e["name"] for e in body["entries"]] == ["Alice", "Bob"]


def test_import_roster_row_error_returns_422_with_errors():
    res = client.post(
        "/api/roster/import", json={"csv": "name,division\nAlice,bogus\n"}
    )
    assert res.status_code == 422
    assert res.json()["detail"] == [
        {
            "row": 2,
            "message": "Invalid division: bogus",
            "code": "invalid_division",
            "value": "bogus",
        }
    ]
    assert client.get("/api/roster").json()["entries"] == []


def test_import_roster_dry_run_does_not_save_and_reports_existing_count():
    client.post("/api/roster/import", json={"csv": "name\nAlice\n"})

    res = client.post(
        "/api/roster/import",
        json={"csv": "name,division\nBob,men\nCara,women\n", "dry_run": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert [e["name"] for e in body["entries"]] == ["Bob", "Cara"]
    assert body["errors"] == []
    assert body["existing_count"] == 1

    # Nothing was saved -- the roster is untouched.
    assert [e["name"] for e in client.get("/api/roster").json()["entries"]] == ["Alice"]


def test_import_roster_dry_run_reports_errors_with_200_and_saves_nothing():
    client.post("/api/roster/import", json={"csv": "name\nAlice\n"})

    res = client.post(
        "/api/roster/import",
        json={"csv": "name,division\nBob,bogus\n", "dry_run": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["entries"] == []
    assert body["errors"] == [
        {
            "row": 2,
            "message": "Invalid division: bogus",
            "code": "invalid_division",
            "value": "bogus",
        }
    ]
    assert [e["name"] for e in client.get("/api/roster").json()["entries"]] == ["Alice"]


def test_import_roster_dry_run_relay_mode_flags_team_size_warnings():
    client.post(
        "/api/race/configure",
        json={
            "race_type": "distance",
            "target_value": 500,
            "competition_mode": "relay",
            "relay_legs": 2,
        },
    )
    res = client.post(
        "/api/roster/import",
        json={"csv": "name,team\nA,Red\nB,Red\nC,Blue\n", "dry_run": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["team_warnings"] == [{"team": "Blue", "count": 1, "expected": 2}]
    assert client.get("/api/roster").json()["entries"] == []


def test_import_roster_dry_run_relay_mode_flags_oversized_team():
    client.post(
        "/api/race/configure",
        json={
            "race_type": "distance",
            "target_value": 500,
            "competition_mode": "relay",
            "relay_legs": 2,
        },
    )
    res = client.post(
        "/api/roster/import",
        json={"csv": "name,team\nA,Red\nB,Red\nC,Red\n", "dry_run": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["team_warnings"] == [{"team": "Red", "count": 3, "expected": 2}]
    assert client.get("/api/roster").json()["entries"] == []


def test_import_roster_dry_run_relay_mode_reports_teamless_entries_separately():
    client.post(
        "/api/race/configure",
        json={
            "race_type": "distance",
            "target_value": 500,
            "competition_mode": "relay",
            "relay_legs": 2,
        },
    )
    res = client.post(
        "/api/roster/import",
        json={"csv": "name,team\nA,Red\nB,Red\nC,\nD,\n", "dry_run": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["teamless_count"] == 2
    assert "" not in body["team_member_counts"]
    assert all(w["team"] != "" for w in body["team_warnings"])
    assert client.get("/api/roster").json()["entries"] == []


def test_walk_in_and_absent_and_requeue():
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\n"})
    res = client.post(
        "/api/roster/entries",
        json={"name": "Zoe", "division": "women", "team": "Green"},
    )
    assert res.status_code == 200
    entries = res.json()["entries"]
    zoe = next(e for e in entries if e["name"] == "Zoe")
    assert zoe["status"] == "pending"

    alice = next(e for e in entries if e["name"] == "Alice")
    res = client.post(f"/api/roster/entries/{alice['id']}/absent")
    assert res.status_code == 200
    assert res.json()["counts"]["absent"] == 1

    res = client.post(f"/api/roster/entries/{alice['id']}/requeue")
    assert res.status_code == 200
    assert res.json()["counts"]["pending"] == 3


def test_next_heat_requires_saved_config():
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\n"})
    _assign_two_stations()
    res = client.post("/api/roster/next-heat")
    assert res.status_code == 409
    assert res.json()["detail"] == "save race settings first"


def test_next_heat_after_stopped_race_leaves_ready_with_same_config_and_right_names():
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name,division\nAlice,men\nBob,women\nCarol,women\nDave,men\n"},
    )

    configure_res = client.post(
        "/api/race/configure",
        json={"race_type": "distance", "target_value": 500},
    )
    assert configure_res.status_code == 200

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 200

    state = client.get("/api/race/state").json()
    assert state["state"] == "READY"
    assert state["config"]["race_type"] == "distance"
    assert state["config"]["target_value"] == 500

    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["athlete_name"] == "Alice"
    assert stations["1"]["division"] == "men"
    assert stations["2"]["athlete_name"] == "Bob"
    assert stations["2"]["division"] == "women"

    # Run the heat through STOPPED (bypassing the HTTP readiness gate --
    # there's no real edge node/telemetry in this test -- the same way
    # other hub tests drive RaceManager's state machine directly), then
    # load the next heat: config must survive unchanged and the new names
    # must land on the stations. mark_current_heat_started() is normally
    # called by the /api/race/start and /api/race/countdown-start
    # endpoints right after race_manager.start_race() succeeds -- since
    # this test drives start_race() directly, it must call it too.
    app_module.race_manager.start_race()
    app_module.roster_manager.mark_current_heat_started()
    app_module.race_manager.stop_race()

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 200

    state = client.get("/api/race/state").json()
    assert state["state"] == "READY"
    assert state["config"]["race_type"] == "distance"
    assert state["config"]["target_value"] == 500

    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["athlete_name"] == "Carol"
    assert stations["1"]["division"] == "women"
    assert stations["2"]["athlete_name"] == "Dave"
    assert stations["2"]["division"] == "men"


def test_next_heat_from_idle_with_saved_config_applies_config_and_registers():
    """Reproduces a hub restart: RaceManager._load_settings() (see
    hub_server/usecases/race_manager.py) populates self._config straight
    from the persisted settings file without ever going through
    configure(), so the race comes back IDLE with a saved config and no
    registrations. The old guard on next-heat's reset/apply branch only
    fired for RaceState.STOPPED or an existing registration, so this
    restart state fell through both and next-heat registered the heat
    without ever moving the race to READY -- Start Race was then refused.
    Setting _config directly (bypassing the HTTP configure endpoint, which
    would itself move state to READY) is what faithfully reproduces the
    restart state instead of a state next-heat already handled.
    """
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name,division\nAlice,men\nBob,women\n"},
    )
    app_module.race_manager._config = RaceConfig(race_type="distance", target_value=500)
    assert app_module.race_manager.get_state() == RaceState.IDLE
    assert (
        not app_module.race_manager.get_stations_status()["stations"]
        .get(1, {})
        .get("registered")
    )

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 200

    state = client.get("/api/race/state").json()
    assert state["state"] == "READY"
    assert state["config"]["race_type"] == "distance"
    assert state["config"]["target_value"] == 500

    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["athlete_name"] == "Alice"
    assert stations["1"]["division"] == "men"
    assert stations["1"]["registered"] is True
    assert stations["2"]["athlete_name"] == "Bob"
    assert stations["2"]["division"] == "women"

    readiness = client.get("/api/race/readiness").json()
    assert not any(
        "Race state must be READY" in issue for issue in readiness["blocking_issues"]
    )


def test_next_heat_returns_409_while_running():
    _assign_two_stations()
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\nCarol\nDave\n"})
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/roster/next-heat")
    app_module.race_manager.start_race()

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 409

    res = client.post("/api/roster/import", json={"csv": "name\nEve\n"})
    assert res.status_code == 409

    app_module.race_manager.stop_race()


def test_next_heat_double_press_without_force_is_blocked_and_changes_nothing():
    """A loaded heat that has NOT raced yet (race still READY/IDLE) must
    not be silently skipped by a second next-heat press -- see the
    "current_heat_not_raced" 409 in POST /api/roster/next-heat."""
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name\nAlice\nBob\nCarol\nDave\n"},
    )
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 200
    roster_before = client.get("/api/roster").json()
    stations_before = client.get("/api/stations").json()
    state_before = client.get("/api/race/state").json()

    # Race is still READY -- heat 1 (Alice, Bob) has not raced. A second
    # press without force must be rejected and change nothing.
    res = client.post("/api/roster/next-heat")
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["reason"] == "current_heat_not_raced"
    names = {entry["name"] for entry in detail["current_heat"]}
    assert names == {"Alice", "Bob"}

    assert client.get("/api/roster").json() == roster_before
    assert client.get("/api/stations").json() == stations_before
    assert client.get("/api/race/state").json() == state_before


def test_next_heat_with_force_skips_the_unraced_heat():
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name\nAlice\nBob\nCarol\nDave\n"},
    )
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/roster/next-heat")  # heat 1: Alice, Bob (unraced)

    res = client.post("/api/roster/next-heat", json={"force": True})
    assert res.status_code == 200

    roster = client.get("/api/roster").json()
    statuses = {entry["name"]: entry["status"] for entry in roster["entries"]}
    assert statuses["Alice"] == "done"
    assert statuses["Bob"] == "done"
    assert statuses["Carol"] == "loaded"
    assert statuses["Dave"] == "loaded"

    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["athlete_name"] == "Carol"
    assert stations["2"]["athlete_name"] == "Dave"


def test_next_heat_exhausted_returns_409_and_leaves_race_and_registrations_intact():
    _assign_two_stations()
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\n"})
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/roster/next-heat")  # heat 1: Alice, Bob loaded

    # Heat 1 actually races through to STOPPED -- the normal path. See the
    # comment above test_next_heat_after_stopped_race_... for why
    # mark_current_heat_started() must be driven alongside start_race()
    # here.
    app_module.race_manager.start_race()
    app_module.roster_manager.mark_current_heat_started()
    app_module.race_manager.stop_race()

    stations_before = client.get("/api/stations").json()

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 409
    assert res.json()["detail"] == "roster exhausted"

    # Nothing was reset or cleared: race is still STOPPED and the finished
    # heat's registrations (the dashboard's podium data) are untouched.
    state = client.get("/api/race/state").json()
    assert state["state"] == "STOPPED"
    assert client.get("/api/stations").json() == stations_before

    roster = client.get("/api/roster").json()
    statuses = {entry["name"]: entry["status"] for entry in roster["entries"]}
    assert statuses == {"Alice": "loaded", "Bob": "loaded"}


def _set_station_online(station_number: int, node_id: str):
    """Mirrors set_online_station in tests/integration/test_api.py: makes a
    station genuinely "online" (an edge node registered with a matching
    equipment stream), which enforce_race_readiness needs to let
    /api/race/countdown-start actually reach RUNNING."""
    import time

    app_module.node_registry.update_status(
        {
            "edge_node_id": f"edge-{station_number:02d}",
            "status": "online",
            "last_seen_epoch_ms": int(time.time() * 1000),
            "equipment_streams": [
                {
                    "node_id": node_id,
                    "equipment_id": f"BIKE_{station_number:02d}",
                    "equipment_type": "fan_bike",
                    "status": "configured",
                    "last_telemetry_epoch_ms": int(time.time() * 1000),
                }
            ],
        }
    )
    app_module.race_manager.assign_station(station_number, node_id)


def test_next_heat_after_full_reset_and_reconfigure_succeeds_without_force_when_heat_had_started():
    """The "started" flag lives on the roster entry, not on RaceManager's
    transient state, so it survives even an explicit Reset Race (which
    wipes race state back to IDLE and clears the saved config) -- unlike
    the old race-state-based guard (IDLE/READY), a reset-and-reconfigure
    cycle must not make an already-raced heat look unraced again."""
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name\nAlice\nBob\nCarol\nDave\n"},
    )
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/roster/next-heat")  # heat 1: Alice, Bob loaded

    app_module.race_manager.start_race()
    app_module.roster_manager.mark_current_heat_started()
    app_module.race_manager.stop_race()

    res = client.post("/api/race/reset")
    assert res.status_code == 200
    assert res.json()["state"] == "IDLE"

    # Reset Race always clears the saved config (unrelated to the roster
    # feature) -- the operator re-saves the same settings before loading
    # the next heat, same as they would for any race.
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 200

    roster = client.get("/api/roster").json()
    statuses = {entry["name"]: entry["status"] for entry in roster["entries"]}
    assert statuses["Alice"] == "done"
    assert statuses["Bob"] == "done"
    assert statuses["Carol"] == "loaded"
    assert statuses["Dave"] == "loaded"


def test_countdown_start_marks_current_heat_started(monkeypatch):
    monkeypatch.setattr(app_module, "RACE_START_COUNTDOWN_DURATION_MS", 10)
    _set_station_online(1, "node-1")
    _set_station_online(2, "node-2")
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\n"})
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/roster/next-heat")  # heat 1: Alice, Bob loaded

    res = client.post("/api/race/countdown-start")
    assert res.status_code == 200
    assert res.json()["state"] == "RUNNING"

    roster = client.get("/api/roster").json()
    entries = {e["name"]: e for e in roster["entries"]}
    assert entries["Alice"]["started"] is True
    assert entries["Bob"]["started"] is True

    app_module.race_manager.stop_race()
    app_module.node_registry.clear()


def test_plain_start_marks_current_heat_started():
    """POST /api/race/start (the non-countdown path) must also drive
    roster_manager.mark_current_heat_started() -- only the countdown path
    (test_countdown_start_marks_current_heat_started above) was covered
    before, so deleting the call from the plain /api/race/start handler
    left the suite green."""
    _set_station_online(1, "node-1")
    _set_station_online(2, "node-2")
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\nCarol\nDave\n"})
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/roster/next-heat")  # heat 1: Alice, Bob loaded

    res = client.post("/api/race/start")
    assert res.status_code == 200
    assert res.json()["state"] == "RUNNING"

    app_module.race_manager.stop_race()

    # If the loaded heat was never marked started, this next-heat call is
    # blocked with 409 "current_heat_not_raced" instead of succeeding.
    res = client.post("/api/roster/next-heat")
    assert res.status_code == 200

    roster = client.get("/api/roster").json()
    statuses = {entry["name"]: entry["status"] for entry in roster["entries"]}
    assert statuses["Alice"] == "done"
    assert statuses["Bob"] == "done"
    assert statuses["Carol"] == "loaded"
    assert statuses["Dave"] == "loaded"

    app_module.node_registry.clear()


def test_get_roster_reports_individual_mode_by_default():
    res = client.get("/api/roster")
    assert res.status_code == 200
    assert res.json()["mode"] == "individual"


def test_next_heat_relay_mode_loads_teams_and_registers_relay_members():
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name,team\nAlice,Volt\nBob,Volt\nCara,Surge\nDan,Surge\n"},
    )
    configure_res = client.post(
        "/api/race/configure",
        json={
            "race_type": "distance",
            "competition_mode": "relay",
            "relay_legs": 2,
            "target_value": 500,
        },
    )
    assert configure_res.status_code == 200

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "relay"
    assert body["current_heat_teams"] == [
        {"team": "Volt", "station_number": 1, "members": ["Alice", "Bob"]},
        {"team": "Surge", "station_number": 2, "members": ["Cara", "Dan"]},
    ]

    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["team_name"] == "Volt"
    assert stations["1"]["relay_members"] == ["Alice", "Bob"]
    assert stations["2"]["team_name"] == "Surge"
    assert stations["2"]["relay_members"] == ["Cara", "Dan"]

    # Readiness is blocked until stations are actually online -- once they
    # are, the relay team registrations above make it ready.
    readiness = client.get("/api/race/readiness").json()
    assert readiness["ready"] is False

    _set_station_online(1, "node-1")
    _set_station_online(2, "node-2")
    readiness = client.get("/api/race/readiness").json()
    assert readiness["ready"] is True

    app_module.node_registry.clear()


def test_next_heat_relay_mode_from_idle_with_saved_config_applies_config_and_registers():
    """Relay-mode counterpart of
    test_next_heat_from_idle_with_saved_config_applies_config_and_registers:
    same post-restart state (config set directly, race IDLE, nothing
    registered), but with a relay config and a team roster."""
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name,team\nAlice,Volt\nBob,Volt\nCara,Surge\nDan,Surge\n"},
    )
    app_module.race_manager._config = RaceConfig(
        race_type="distance",
        competition_mode="relay",
        relay_legs=2,
        target_value=500,
    )
    assert app_module.race_manager.get_state() == RaceState.IDLE

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] == "relay"
    assert body["current_heat_teams"] == [
        {"team": "Volt", "station_number": 1, "members": ["Alice", "Bob"]},
        {"team": "Surge", "station_number": 2, "members": ["Cara", "Dan"]},
    ]

    state = client.get("/api/race/state").json()
    assert state["state"] == "READY"
    assert state["config"]["competition_mode"] == "relay"
    assert state["config"]["relay_legs"] == 2

    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["team_name"] == "Volt"
    assert stations["1"]["relay_members"] == ["Alice", "Bob"]
    assert stations["1"]["registered"] is True
    assert stations["2"]["team_name"] == "Surge"
    assert stations["2"]["relay_members"] == ["Cara", "Dan"]

    readiness = client.get("/api/race/readiness").json()
    assert not any(
        "Race state must be READY" in issue for issue in readiness["blocking_issues"]
    )


def test_next_heat_relay_mode_rejects_teamless_pending_entry_and_changes_nothing():
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name,team\nAlice,Volt\nBob,Volt\nEve,\n"},
    )
    client.post(
        "/api/race/configure",
        json={
            "race_type": "distance",
            "competition_mode": "relay",
            "relay_legs": 2,
            "target_value": 500,
        },
    )
    roster_before = client.get("/api/roster").json()
    stations_before = client.get("/api/stations").json()
    state_before = client.get("/api/race/state").json()

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 409
    assert "Eve" in res.json()["detail"]

    assert client.get("/api/roster").json() == roster_before
    assert client.get("/api/stations").json() == stations_before
    assert client.get("/api/race/state").json() == state_before


def test_next_heat_relay_mode_rejects_wrong_sized_team_and_changes_nothing():
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name,team\nAlice,Volt\n"},
    )
    client.post(
        "/api/race/configure",
        json={
            "race_type": "distance",
            "competition_mode": "relay",
            "relay_legs": 2,
            "target_value": 500,
        },
    )
    roster_before = client.get("/api/roster").json()
    stations_before = client.get("/api/stations").json()
    state_before = client.get("/api/race/state").json()

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert "Volt" in detail
    assert "2" in detail

    assert client.get("/api/roster").json() == roster_before
    assert client.get("/api/stations").json() == stations_before
    assert client.get("/api/race/state").json() == state_before


def test_roster_endpoints_require_admin_token_when_configured(monkeypatch):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "admin-secret")

    assert client.get("/api/roster").status_code == 401
    assert (
        client.post("/api/roster/import", json={"csv": "name\nA\n"}).status_code == 401
    )
    assert client.post("/api/roster/next-heat").status_code == 401
    assert client.delete("/api/roster").status_code == 401
    assert client.post("/api/roster/entries", json={"name": "A"}).status_code == 401
    assert client.post("/api/roster/entries/x/absent").status_code == 401
    assert client.post("/api/roster/entries/x/requeue").status_code == 401
    assert client.post("/api/roster/current-heat/cancel").status_code == 401

    ok_headers = {"X-FitRace-Admin-Token": "admin-secret"}
    assert client.get("/api/roster", headers=ok_headers).status_code == 200


# ---------------------------------------------------------------------------
# POST /api/roster/current-heat/cancel -- venue incident: a loaded-but-
# unraced heat got orphaned by a race config change/reset, and the operator
# needs a way back to a loadable roster without duplicating results.
# ---------------------------------------------------------------------------


def test_cancel_current_heat_clears_registrations_and_next_heat_works_without_force():
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name\nAlice\nBob\nCarol\nDave\n"},
    )
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    res = client.post("/api/roster/next-heat")  # Alice, Bob loaded
    assert res.status_code == 200

    res = client.post("/api/roster/current-heat/cancel")
    assert res.status_code == 200
    statuses = {e["name"]: e["status"] for e in res.json()["entries"]}
    assert statuses["Alice"] == "pending"
    assert statuses["Bob"] == "pending"

    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["registered"] is False
    assert stations["2"]["registered"] is False

    state = client.get("/api/race/state").json()
    assert state["state"] == "READY"

    # Loading the next heat again -- without force -- picks the SAME two
    # people back onto the stations, since a cancelled heat is not "loaded".
    res = client.post("/api/roster/next-heat")
    assert res.status_code == 200
    roster = client.get("/api/roster").json()
    statuses = {e["name"]: e["status"] for e in roster["entries"]}
    assert statuses["Alice"] == "loaded"
    assert statuses["Bob"] == "loaded"
    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["athlete_name"] == "Alice"
    assert stations["2"]["athlete_name"] == "Bob"


def test_cancel_current_heat_returns_409_while_running_and_changes_nothing():
    _assign_two_stations()
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\n"})
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/roster/next-heat")
    app_module.race_manager.start_race()

    roster_before = client.get("/api/roster").json()
    stations_before = client.get("/api/stations").json()

    res = client.post("/api/roster/current-heat/cancel")
    assert res.status_code == 409

    assert client.get("/api/roster").json() == roster_before
    assert client.get("/api/stations").json() == stations_before

    app_module.race_manager.stop_race()


def test_cancel_current_heat_with_no_current_heat_returns_409():
    _assign_two_stations()
    client.post("/api/roster/import", json={"csv": "name\nAlice\n"})
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )

    res = client.post("/api/roster/current-heat/cancel")
    assert res.status_code == 409
    assert res.json()["detail"] == "no current heat"


def test_cancel_current_heat_already_raced_returns_409_and_changes_nothing():
    _assign_two_stations()
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\n"})
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/roster/next-heat")
    app_module.race_manager.start_race()
    app_module.roster_manager.mark_current_heat_started()
    app_module.race_manager.stop_race()

    roster_before = client.get("/api/roster").json()

    res = client.post("/api/roster/current-heat/cancel")
    assert res.status_code == 409
    assert res.json()["detail"] == "current heat already raced"

    assert client.get("/api/roster").json() == roster_before


def test_cancel_current_heat_when_race_is_stopped_resets_and_reapplies_config():
    """Exercises the STOPPED branch of POST /api/roster/current-heat/cancel,
    mirroring how /api/roster/next-heat handles a STOPPED race: capture the
    saved config, reset_race_state(), then re-apply that same config so the
    race ends up READY again -- before clearing station registrations. The
    race is forced into STOPPED directly (the same technique
    test_next_heat_from_idle_with_saved_config_applies_config_and_registers
    above uses to force IDLE via _config) rather than through a full
    start/stop cycle: a heat that actually raced to STOPPED would already
    be "started" and rejected by cancel_current_heat() itself, so this
    isolates the endpoint's STOPPED-state plumbing instead."""
    _assign_two_stations()
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\n"})
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/roster/next-heat")  # Alice, Bob loaded, READY

    app_module.race_manager._state = RaceState.STOPPED

    res = client.post("/api/roster/current-heat/cancel")
    assert res.status_code == 200

    state = client.get("/api/race/state").json()
    assert state["state"] == "READY"
    assert state["config"]["race_type"] == "distance"
    assert state["config"]["target_value"] == 500

    roster = client.get("/api/roster").json()
    statuses = {e["name"]: e["status"] for e in roster["entries"]}
    assert statuses["Alice"] == "pending"
    assert statuses["Bob"] == "pending"

    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["registered"] is False
    assert stations["2"]["registered"] is False


def test_cancel_current_heat_relay_mode_clears_team_registrations():
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name,team\nAlice,Volt\nBob,Volt\nCara,Surge\nDan,Surge\n"},
    )
    client.post(
        "/api/race/configure",
        json={
            "race_type": "distance",
            "competition_mode": "relay",
            "relay_legs": 2,
            "target_value": 500,
        },
    )
    client.post("/api/roster/next-heat")  # Volt on 1, Surge on 2

    res = client.post("/api/roster/current-heat/cancel")
    assert res.status_code == 200
    statuses = {e["name"]: e["status"] for e in res.json()["entries"]}
    assert all(status == "pending" for status in statuses.values())

    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["registered"] is False
    assert stations["2"]["registered"] is False

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 200
    body = res.json()
    assert body["current_heat_teams"] == [
        {"team": "Volt", "station_number": 1, "members": ["Alice", "Bob"]},
        {"team": "Surge", "station_number": 2, "members": ["Cara", "Dan"]},
    ]


def test_venue_incident_cancel_current_heat_unblocks_next_heat():
    """Reproduces the real venue incident end to end: an operator loads a
    heat, then the race config changes/gets reset (station_number stays on
    the entry, started stays False) -- next-heat is blocked ("save race
    settings first") and there is no way back except Cancel Current Heat.
    Cancelling must free the SAME first people so re-saving the config and
    pressing next-heat lands them right back on the same stations."""
    _assign_two_stations()
    client.post(
        "/api/roster/import",
        json={"csv": "name\nAlice\nBob\nCarol\nDave\n"},
    )
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    res = client.post("/api/roster/next-heat")  # Alice, Bob loaded
    assert res.status_code == 200

    res = client.post("/api/race/reset")
    assert res.status_code == 200
    assert res.json()["state"] == "IDLE"

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 409
    assert res.json()["detail"] == "save race settings first"

    res = client.post("/api/roster/current-heat/cancel")
    assert res.status_code == 200
    roster = client.get("/api/roster").json()
    statuses = {e["name"]: e["status"] for e in roster["entries"]}
    assert statuses["Alice"] == "pending"
    assert statuses["Bob"] == "pending"

    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )

    res = client.post("/api/roster/next-heat")
    assert res.status_code == 200
    roster = client.get("/api/roster").json()
    statuses = {e["name"]: e["status"] for e in roster["entries"]}
    assert statuses["Alice"] == "loaded"
    assert statuses["Bob"] == "loaded"
    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["athlete_name"] == "Alice"
    assert stations["2"]["athlete_name"] == "Bob"


# ---------------------------------------------------------------------------
# DELETE /api/roster -- must refuse while RUNNING, and must clear station
# registrations left behind by a loaded (but never cleared) heat.
# ---------------------------------------------------------------------------


def test_delete_roster_returns_409_while_running_and_changes_nothing():
    _assign_two_stations()
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\n"})
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/roster/next-heat")
    app_module.race_manager.start_race()

    roster_before = client.get("/api/roster").json()

    res = client.delete("/api/roster")
    assert res.status_code == 409

    assert client.get("/api/roster").json() == roster_before

    app_module.race_manager.stop_race()


def test_delete_roster_with_loaded_heat_clears_station_registrations():
    _assign_two_stations()
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\n"})
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/roster/next-heat")  # Alice, Bob loaded

    res = client.delete("/api/roster")
    assert res.status_code == 200
    assert res.json()["entries"] == []

    stations = client.get("/api/stations").json()["stations"]
    assert stations["1"]["registered"] is False
    assert stations["2"]["registered"] is False


def test_delete_roster_with_no_loaded_heat_does_not_touch_stations():
    _assign_two_stations()
    client.post("/api/roster/import", json={"csv": "name\nAlice\nBob\n"})
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    # Nothing loaded -- registrations are already empty; DELETE must not
    # error out reaching for clear_station_registrations() in a state where
    # it would be disallowed (it isn't here, but this pins the "only when a
    # loaded heat existed" branch instead of an unconditional call).
    stations_before = client.get("/api/stations").json()

    res = client.delete("/api/roster")
    assert res.status_code == 200

    assert client.get("/api/stations").json() == stations_before


# ---------------------------------------------------------------------------
# GET /api/roster/template.csv -- downloadable CSV template that round-trips
# through parse_roster_csv with zero errors.
# ---------------------------------------------------------------------------


def test_template_csv_individual_round_trips_through_import():
    res = client.get("/api/roster/template.csv?mode=individual&lang=zh-TW")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")

    import_res = client.post(
        "/api/roster/import", json={"csv": res.content.decode("utf-8")}
    )
    assert import_res.status_code == 200
    assert len(import_res.json()["entries"]) == 3


def test_template_csv_relay_round_trips_with_requested_legs():
    res = client.get("/api/roster/template.csv?mode=relay&legs=3&lang=en")
    assert res.status_code == 200

    import_res = client.post(
        "/api/roster/import", json={"csv": res.content.decode("utf-8")}
    )
    assert import_res.status_code == 200
    entries = import_res.json()["entries"]
    teams: dict[str, int] = {}
    for entry in entries:
        teams[entry["team"]] = teams.get(entry["team"], 0) + 1
    assert len(teams) == 2
    assert all(count == 3 for count in teams.values())


def test_template_csv_requires_admin_token_when_configured(monkeypatch):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "admin-secret")
    res = client.get("/api/roster/template.csv?mode=individual")
    assert res.status_code == 401

    ok_headers = {"X-FitRace-Admin-Token": "admin-secret"}
    res = client.get("/api/roster/template.csv?mode=individual", headers=ok_headers)
    assert res.status_code == 200
