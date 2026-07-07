import pytest
from fastapi.testclient import TestClient
from hub_server.infrastructure.fastapi.app import app, hyrox_manager


def test_hyrox_endpoints_404_when_disabled(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "0")
    client = TestClient(app)

    response = client.get("/api/hyrox/state")
    assert response.status_code == 404

    response = client.post("/api/hyrox/configure", json={"competition_mode": "individual", "session_type": "training"})
    assert response.status_code == 404


def test_hyrox_endpoints_flow_when_enabled(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    client = TestClient(app)

    # 1. Check initial state
    response = client.get("/api/hyrox/state")
    assert response.status_code == 200
    state = response.json()
    assert state["is_active"] is False
    assert len(state["athletes"]) == 0

    # 2. Configure Hyrox
    response = client.post(
        "/api/hyrox/configure",
        json={"competition_mode": "doubles", "session_type": "competition"}
    )
    assert response.status_code == 200
    assert response.json()["competition_mode"] == "doubles"

    # 3. Register Athlete
    response = client.post(
        "/api/hyrox/register",
        json={
            "athlete_name": "Tony",
            "team_name": "Alpha Team",
            "rfid_tag_id": "EPC_TONY_123",
            "station_number": 1
        }
    )
    assert response.status_code == 200

    # 4. Start Hyrox Race
    response = client.post("/api/hyrox/start")
    assert response.status_code == 200

    # 5. Check state after start
    response = client.get("/api/hyrox/state")
    assert response.status_code == 200
    state = response.json()
    assert state["is_active"] is True
    assert "EPC_TONY_123" in state["athletes"]
    assert state["athletes"]["EPC_TONY_123"]["current_stage"] == "run_1"


def test_hyrox_api_auto_assigns_station_number(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.setenv("TESTING", "1")
    client = TestClient(app)

    # Re-initialize or clear previous data by configuration reset
    client.post("/api/hyrox/configure", json={"competition_mode": "individual", "session_type": "training"})

    # Register Athlete 1 without station number, with custom division
    response = client.post(
        "/api/hyrox/register",
        json={
            "athlete_name": "Tony",
            "rfid_tag_id": "EPC_TONY_AUTO",
            "division": "doubles"
        }
    )
    assert response.status_code == 200

    # Register Athlete 2 without station number, defaulting division
    response = client.post(
        "/api/hyrox/register",
        json={
            "athlete_name": "Bob",
            "rfid_tag_id": "EPC_BOB_AUTO"
        }
    )
    assert response.status_code == 200

    # Verify state lists Tony as station 0 (unassigned) and Bob as station 0 (unassigned)
    response = client.get("/api/hyrox/state")
    state = response.json()
    assert state["athletes"]["EPC_TONY_AUTO"]["station_number"] == 0
    assert state["athletes"]["EPC_TONY_AUTO"]["division"] == "doubles"
    assert state["athletes"]["EPC_BOB_AUTO"]["station_number"] == 0
    assert state["athletes"]["EPC_BOB_AUTO"]["division"] == "individual"

    # Start the race so RFID events are processed
    client.post("/api/hyrox/start")

    # Simulate RFID tag crossing at mat L3 dynamically assigning station_number=3
    hyrox_manager.register_tag_crossing(
        tag_id="EPC_TONY_AUTO",
        location="start_line",
        rssi=-42.0,
        timestamp_ms=1780000000000,
        station_number=3
    )

    # Verify Tony is now dynamically assigned to Station 3
    response = client.get("/api/hyrox/state")
    state = response.json()
    assert state["athletes"]["EPC_TONY_AUTO"]["station_number"] == 3

    # Force complete stage via HTTP API
    response = client.post(
        "/api/hyrox/complete-stage",
        json={"rfid_tag_id": "EPC_TONY_AUTO"}
    )
    assert response.status_code == 200

    # Verify Tony transitioned to run_4
    response = client.get("/api/hyrox/state")
    state = response.json()
    assert state["athletes"]["EPC_TONY_AUTO"]["current_stage"] == "run_4"


def test_hyrox_admin_endpoints_require_token_when_configured(monkeypatch):
    monkeypatch.setenv("FITRACE_ENABLE_HYROX", "1")
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "secret")
    client = TestClient(app)

    config_body = {"competition_mode": "individual", "session_type": "training"}

    # Without token -> 401
    assert client.post("/api/hyrox/configure", json=config_body).status_code == 401
    assert client.post("/api/hyrox/start").status_code == 401
    assert client.post("/api/hyrox/complete-stage", json={"rfid_tag_id": "X"}).status_code == 401

    # With token -> allowed
    headers = {"X-FitRace-Admin-Token": "secret"}
    assert client.post("/api/hyrox/configure", json=config_body, headers=headers).status_code == 200

    # Registration stays open (self-service signup page)
    response = client.post(
        "/api/hyrox/register",
        json={"athlete_name": "Tony", "rfid_tag_id": "EPC_AUTH_TEST"},
    )
    assert response.status_code == 200

    # Unknown tag on complete-stage is a 409, not a silent ok
    client.post("/api/hyrox/start", headers=headers)
    response = client.post(
        "/api/hyrox/complete-stage", json={"rfid_tag_id": "NO_SUCH_TAG"}, headers=headers
    )
    assert response.status_code == 409
