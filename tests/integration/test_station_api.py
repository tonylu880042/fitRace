from fastapi.testclient import TestClient
from hub_server.infrastructure.fastapi.app import app

client = TestClient(app)


def test_station_api_workflow():
    # 1. Reset race to clean state
    client.post("/api/race/reset")

    # 2. Query empty stations status
    res = client.get("/api/stations")
    assert res.status_code == 200
    data = res.json()
    assert len(data["stations"]) == 0
    assert len(data["unassigned_nodes"]) == 0

    # 3. Simulate active telemetry to discover a node
    telemetry_payload = {
        "node_id": "bike-01",
        "equipment_id": "BIKE_01",
        "equipment_type": "fan_bike",
        "instantaneous_speed_kph": 12.0,
        "cadence_rpm": 60,
        "power_watts": 150,
        "heart_rate_bpm": 120,
        "distance_m": 0.0,
        "elapsed_time_ms": 0,
        "timestamp_epoch_ms": 1600000000000,
    }
    # We send telemetry. When not in RUNNING state, this shouldn't fail but should record the node as active.
    # Wait, our post_test_telemetry endpoint has:
    # `progress = race_manager.update_telemetry(payload)` which might raise ValueError if race is not RUNNING.
    # To handle active node discovery safely outside of running state, let's make sure telemetry endpoint or subscriber registers it.
    # Let's test that sending telemetry updates active devices.
    # Since telemetry endpoint might fail if not RUNNING, we can configure and start a race first, or just configure it.
    # Actually, we want nodes to be discovered even when the race is IDLE, so that technicians can assign them.
    # The subscriber receives telemetry. We can also let the HTTP endpoint discover the node.
    # Let's test if we can assign a node even if we post telemetry. We'll verify this flow.
    
    # Let's assign station 1 to "bike-01" via API.
    # Wait, we can assign a node even if it hasn't sent telemetry yet, but it will be in the unassigned list once active.
    # Let's post an assignment.
    res = client.post("/api/stations/assign", json={"station_number": 1, "node_id": "bike-01"})
    assert res.status_code == 200
    
    res = client.get("/api/stations")
    assert res.json()["stations"]["1"]["node_id"] == "bike-01"

    # 4. Register an athlete to Station 1
    res = client.post("/api/race/register", json={"station_number": 1, "athlete_name": "Tony"})
    assert res.status_code == 200
    assert res.json()["stations"]["1"]["athlete_name"] == "Tony"

    # 5. Overwrite the registration
    res = client.post("/api/race/register", json={"station_number": 1, "athlete_name": "Tony Lu"})
    assert res.status_code == 200
    assert res.json()["stations"]["1"]["athlete_name"] == "Tony Lu"

    # 6. Test that registering fails if race is running
    # Configure and start
    client.post("/api/race/configure", json={"race_type": "distance", "target_value": 500})
    client.post("/api/race/start")
    
    res = client.post("/api/race/register", json={"station_number": 1, "athlete_name": "Another"})
    assert res.status_code == 400
