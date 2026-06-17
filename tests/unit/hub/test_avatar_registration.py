import os
import base64
import pytest
from fastapi.testclient import TestClient
from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager
from hub_server.infrastructure.fastapi.app import app

# A tiny 1x1 valid base64 webp image (transparent pixel)
TINY_WEBP_BASE64 = (
    "data:image/webp;base64,UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4H"
)


def remove_station_avatar(station_number: int):
    file_path = f"hub_server/static/avatars/station_{station_number}.webp"
    if os.path.exists(file_path):
        os.remove(file_path)


def test_race_manager_stores_team_and_avatar():
    manager = RaceManager()
    
    # Register an athlete with a team name
    manager.register_athlete(1, "Tony", team_name="RD", has_avatar=True)
    status = manager.get_stations_status()
    
    # Assert get_stations_status contains team_name and has_avatar
    assert status["stations"][1]["athlete_name"] == "Tony"
    assert status["stations"][1]["team_name"] == "RD"
    assert status["stations"][1]["has_avatar"] is True

    # Register another athlete without a team or avatar
    manager.register_athlete(2, "Alice", team_name=None, has_avatar=False)
    status = manager.get_stations_status()
    assert status["stations"][2]["athlete_name"] == "Alice"
    assert status["stations"][2]["team_name"] is None
    assert status["stations"][2]["has_avatar"] is False


def test_race_manager_telemetry_includes_team_and_avatar_url():
    manager = RaceManager()
    
    # Register athlete 1
    manager.update_active_node("node-01", "fan_bike")
    manager.assign_station(1, "node-01")
    manager.register_athlete(1, "Tony", team_name="RD", has_avatar=True)

    # Register athlete 2
    manager.update_active_node("node-02", "fan_bike")
    manager.assign_station(2, "node-02")
    manager.register_athlete(2, "Alice", team_name=None, has_avatar=False)

    config = RaceConfig(race_type="distance", target_value=1000.0)
    manager.configure(config)
    manager.start_race()

    telemetry_payload = {
        "node_id": "node-01",
        "distance_m": 150.0,
        "elapsed_time_ms": 10000,
        "instantaneous_speed_kph": 15.0,
    }

    progress = manager.update_telemetry(telemetry_payload)
    node_progress = progress["node-01"]
    assert node_progress["athlete_name"] == "Tony"
    assert node_progress["team_name"] == "RD"
    assert node_progress["avatar_url"] is not None
    assert "station_1.webp" in node_progress["avatar_url"]
    assert "?t=" in node_progress["avatar_url"]

    # Re-initialize progress or update telemetry for node-02
    telemetry_payload_2 = {
        "node_id": "node-02",
        "distance_m": 50.0,
        "elapsed_time_ms": 10000,
        "instantaneous_speed_kph": 5.0,
    }
    progress = manager.update_telemetry(telemetry_payload_2)
    node_progress_2 = progress["node-02"]
    assert node_progress_2["athlete_name"] == "Alice"
    assert node_progress_2["team_name"] is None
    assert node_progress_2["avatar_url"] is None


def test_api_avatar_upload_and_removal():
    client = TestClient(app)
    client.post("/api/race/reset")

    avatar_dir = "hub_server/static/avatars"
    target_file = os.path.join(avatar_dir, "station_1.webp")
    
    # Ensure any residual file is deleted
    if os.path.exists(target_file):
        os.remove(target_file)

    # 1. Register with team name and avatar
    register_payload = {
        "station_number": 1,
        "athlete_name": "Tony",
        "team_name": "RD",
        "avatar_base64": TINY_WEBP_BASE64
    }
    
    res = client.post("/api/race/register", json=register_payload)
    assert res.status_code == 200
    
    # Check status response
    data = res.json()
    assert data["stations"]["1"]["athlete_name"] == "Tony"
    assert data["stations"]["1"]["team_name"] == "RD"
    
    # Check that avatar file was created
    assert os.path.exists(target_file)
    with open(target_file, "rb") as f:
        content = f.read()
        # Verify it has some binary content
        assert len(content) > 0

    # 2. Register without avatar (should delete existing file)
    register_payload_no_avatar = {
        "station_number": 1,
        "athlete_name": "Tony",
        "team_name": "RD",
        "avatar_base64": None
    }
    res = client.post("/api/race/register", json=register_payload_no_avatar)
    assert res.status_code == 200
    assert not os.path.exists(target_file)


def test_api_avatar_upload_rejects_invalid_base64():
    client = TestClient(app)
    client.post("/api/race/reset")
    remove_station_avatar(7)

    res = client.post(
        "/api/race/register",
        json={
            "station_number": 7,
            "athlete_name": "Invalid Base64",
            "avatar_base64": "data:image/webp;base64,not valid base64!",
        },
    )

    assert res.status_code == 400
    assert "Invalid avatar image" in res.json()["detail"]
    assert not os.path.exists("hub_server/static/avatars/station_7.webp")


def test_api_avatar_upload_rejects_wrong_mime_type():
    client = TestClient(app)
    client.post("/api/race/reset")
    remove_station_avatar(8)

    res = client.post(
        "/api/race/register",
        json={
            "station_number": 8,
            "athlete_name": "Wrong Mime",
            "avatar_base64": TINY_WEBP_BASE64.replace("image/webp", "image/png"),
        },
    )

    assert res.status_code == 400
    assert "WebP" in res.json()["detail"]
    assert not os.path.exists("hub_server/static/avatars/station_8.webp")


def test_api_avatar_upload_rejects_non_webp_bytes():
    client = TestClient(app)
    client.post("/api/race/reset")
    remove_station_avatar(9)
    png_like_data = "data:image/webp;base64," + base64.b64encode(b"\x89PNG\r\n").decode()

    res = client.post(
        "/api/race/register",
        json={
            "station_number": 9,
            "athlete_name": "Wrong Bytes",
            "avatar_base64": png_like_data,
        },
    )

    assert res.status_code == 400
    assert "WebP" in res.json()["detail"]
    assert not os.path.exists("hub_server/static/avatars/station_9.webp")


def test_api_avatar_upload_rejects_oversized_payload():
    client = TestClient(app)
    client.post("/api/race/reset")
    remove_station_avatar(10)
    oversized_webp = (
        b"RIFF"
        + (300_000).to_bytes(4, "little")
        + b"WEBP"
        + (b"0" * 300_000)
    )

    res = client.post(
        "/api/race/register",
        json={
            "station_number": 10,
            "athlete_name": "Too Large",
            "avatar_base64": "data:image/webp;base64,"
            + base64.b64encode(oversized_webp).decode(),
        },
    )

    assert res.status_code == 400
    assert "too large" in res.json()["detail"].lower()
    assert not os.path.exists("hub_server/static/avatars/station_10.webp")
