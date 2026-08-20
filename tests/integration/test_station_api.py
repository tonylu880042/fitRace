from fastapi.testclient import TestClient
import time

from hub_server.infrastructure.fastapi.app import app, node_registry

client = TestClient(app)


def test_station_api_workflow():
    # 1. Reset race to clean state
    client.post("/api/race/reset")
    node_registry.clear()
    for station_number in (1, 2, 3):
        client.post(
            "/api/stations/assign",
            json={"station_number": station_number, "node_id": None},
        )

    # 2. Query empty stations status
    res = client.get("/api/stations")
    assert res.status_code == 200
    data = res.json()
    assert len(data["stations"]) == 0
    assert len(data["unassigned_nodes"]) == 0

    # 3. Discover the node. Station assignment does not require telemetry
    # first -- a technician assigns a station, and the node shows up as a
    # live stream once its edge reports it (node_registry.update_status
    # below), which is what the rest of this test exercises.
    res = client.post(
        "/api/stations/assign", json={"station_number": 1, "node_id": "bike-01"}
    )
    assert res.status_code == 200
    node_registry.update_status(
        {
            "edge_node_id": "edge-01",
            "status": "online",
            "equipment_streams": [
                {
                    "node_id": "bike-01",
                    "equipment_id": "BIKE_01",
                    "equipment_type": "fan_bike",
                    "status": "configured",
                    "last_telemetry_epoch_ms": int(time.time() * 1000),
                }
            ],
        }
    )

    res = client.get("/api/stations")
    assert res.json()["stations"]["1"]["node_id"] == "bike-01"

    # 4. Register an athlete to Station 1
    res = client.post(
        "/api/race/register", json={"station_number": 1, "athlete_name": "Tony"}
    )
    assert res.status_code == 200
    assert res.json()["stations"]["1"]["athlete_name"] == "Tony"

    # 5. Overwrite the registration
    res = client.post(
        "/api/race/register", json={"station_number": 1, "athlete_name": "Tony Lu"}
    )
    assert res.status_code == 200
    assert res.json()["stations"]["1"]["athlete_name"] == "Tony Lu"

    # 6. Test that registering fails if race is running
    # Configure and start
    client.post(
        "/api/race/configure", json={"race_type": "distance", "target_value": 500}
    )
    client.post("/api/race/start")

    res = client.post(
        "/api/race/register", json={"station_number": 1, "athlete_name": "Another"}
    )
    assert res.status_code == 400
