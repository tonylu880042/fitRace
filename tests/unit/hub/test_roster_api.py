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
    # must land on the stations.
    app_module.race_manager.start_race()
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
