from fastapi.testclient import TestClient
from hub_server.infrastructure.fastapi.app import app

client = TestClient(app)


def test_health_check_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_race_workflow_via_api():
    # Initial state should be IDLE
    res = client.get("/api/race/state")
    assert res.status_code == 200
    assert res.json()["state"] == "IDLE"

    # Configure race
    config_payload = {"race_type": "time", "target_value": 0, "duration_sec": 120}
    res = client.post("/api/race/configure", json=config_payload)
    assert res.status_code == 200
    assert res.json()["state"] == "READY"
    assert res.json()["config"]["duration_sec"] == 120

    # Start race
    res = client.post("/api/race/start")
    assert res.status_code == 200
    assert res.json()["state"] == "RUNNING"

    # Stop race
    res = client.post("/api/race/stop")
    assert res.status_code == 200
    assert res.json()["state"] == "STOPPED"

    # Reset race
    res = client.post("/api/race/reset")
    assert res.status_code == 200
    assert res.json()["state"] == "IDLE"


def test_websocket_dashboard_broadcast():
    config_payload = {"race_type": "distance", "target_value": 1000, "duration_sec": 0}
    # Configure and start first
    client.post("/api/race/configure", json=config_payload)
    client.post("/api/race/start")

    # Connect via WebSocket
    with client.websocket_connect("/ws/dashboard") as websocket:
        # Simulate pushing MQTT telemetry from background.
        # We can trigger it by sending a POST request to update telemetry (for API design simplicity).
        # We'll expose a POST /api/test/telemetry endpoint in development mode for easy API test triggering.
        telemetry_payload = {
            "node_id": "rower-01",
            "equipment_id": "ROW_01",
            "equipment_type": "rowing_machine",
            "instantaneous_speed_kph": 10.0,
            "cadence_rpm": 30,
            "power_watts": 180,
            "heart_rate_bpm": 135,
            "distance_m": 50.0,
            "elapsed_time_ms": 10000,
            "timestamp_epoch_ms": 1600000000000,
        }
        res = client.post("/api/test/telemetry", json=telemetry_payload)
        assert res.status_code == 200

        # Receive payload via websocket
        data = websocket.receive_json()
        assert data["rower-01"]["distance_m"] == 50.0
        assert data["rower-01"]["progress_percent"] == 5.0  # 50 / 1000 * 100
