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
    assert res.json()["detail"] == [{"row": 2, "message": "Invalid division: bogus"}]
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

    ok_headers = {"X-FitRace-Admin-Token": "admin-secret"}
    assert client.get("/api/roster", headers=ok_headers).status_code == 200
