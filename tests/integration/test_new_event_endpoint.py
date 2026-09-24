"""POST /api/race/new-event lets Game Admin draw the boundary for "current
event" overall standings (see RaceManager.start_new_event /
RaceResultsQuery.get_standings). Blocked with 409 while a race is RUNNING,
mirroring exactly how POST /api/results/clear is blocked.
"""

import time

from fastapi.testclient import TestClient
from hub_server.infrastructure.fastapi import app as app_module
from hub_server.infrastructure.fastapi.app import app
from hub_server.usecases.race_manager import RaceManager

client = TestClient(app)


def _prepare_ready_race(monkeypatch):
    from hub_server.infrastructure.fastapi.app import node_registry

    node_registry.update_status(
        {
            "edge_node_id": "edge-01",
            "status": "online",
            "last_seen_epoch_ms": int(time.time() * 1000),
            "equipment_streams": [
                {
                    "node_id": "node-01",
                    "equipment_id": "BIKE_01",
                    "equipment_type": "fan_bike",
                    "status": "configured",
                    "last_telemetry_epoch_ms": int(time.time() * 1000),
                }
            ],
        }
    )
    client.post(
        "/api/stations/assign", json={"station_number": 1, "node_id": "node-01"}
    )
    client.post(
        "/api/race/register", json={"station_number": 1, "athlete_name": "Runner A"}
    )
    client.post(
        "/api/race/configure",
        json={"race_type": "time", "target_value": 0, "duration_sec": 120},
    )


def test_new_event_sets_and_returns_boundary(monkeypatch):
    monkeypatch.setattr(app_module, "race_manager", RaceManager())

    response = client.post("/api/race/new-event")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["event_start_epoch_ms"], int)
    assert (
        app_module.race_manager.get_event_start_epoch_ms()
        == body["event_start_epoch_ms"]
    )


def test_new_event_blocked_while_race_running(monkeypatch):
    monkeypatch.setattr(app_module, "race_manager", RaceManager())

    client.post("/api/race/reset")
    _prepare_ready_race(monkeypatch)
    start_resp = client.post("/api/race/start")
    assert start_resp.status_code == 200
    assert start_resp.json()["state"] == "RUNNING"

    response = client.post("/api/race/new-event")
    assert response.status_code == 409
    assert app_module.race_manager.get_event_start_epoch_ms() is None

    client.post("/api/race/stop")
    client.post("/api/race/reset")


def test_standings_endpoint_scoped_to_the_boundary(monkeypatch):
    from hub_server.usecases.race_result_store import RaceResultStore

    import hub_server.infrastructure.fastapi.app as hub_app

    store = RaceResultStore("/tmp/does-not-matter.jsonl")
    monkeypatch.setattr(app_module, "race_manager", RaceManager())
    monkeypatch.setattr(hub_app, "race_result_store", store)
    monkeypatch.setattr(
        hub_app,
        "race_results_query",
        __import__(
            "hub_server.usecases.race_results_query", fromlist=["RaceResultsQuery"]
        ).RaceResultsQuery(store),
    )

    old_snapshot = {
        "state": "STOPPED",
        "config": {
            "race_type": "distance",
            "competition_mode": "individual",
            "target_value": 150,
            "duration_sec": 0,
            "relay_legs": None,
        },
        "start_time_epoch_ms": 1_000,
        "end_time_epoch_ms": 2_000,
        "leaderboard": {
            "node-01": {
                "node_id": "node-01",
                "athlete_name": "陳大文",
                "station_number": 1,
                "finished_time_ms": 1300,
            }
        },
    }
    store.save_finished_snapshot(old_snapshot)

    before = client.get("/api/results/standings").json()
    assert before["race_count"] == 1

    boundary_resp = client.post("/api/race/new-event")
    assert boundary_resp.status_code == 200

    after = client.get("/api/results/standings").json()
    assert after["race_count"] == 0
