import base64
import os
from fastapi.testclient import TestClient
from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager
from hub_server.infrastructure.fastapi.app import app

# A tiny 1x1 valid base64 webp image (transparent pixel)
TINY_WEBP_BASE64 = "data:image/webp;base64,UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4H"


AVATAR_A = "a" * 32


def legacy_avatar_file(station_number: int) -> str:
    return f"hub_server/static/avatars/station_{station_number}.webp"


def test_race_manager_stores_team_and_avatar():
    manager = RaceManager()

    # Register an athlete with a team name
    manager.register_athlete(1, "Tony", team_name="RD", avatar_id=AVATAR_A)
    status = manager.get_stations_status()

    # Assert get_stations_status contains team_name and has_avatar
    assert status["stations"][1]["athlete_name"] == "Tony"
    assert status["stations"][1]["team_name"] == "RD"
    assert status["stations"][1]["has_avatar"] is True

    # Register another athlete without a team or avatar
    manager.register_athlete(2, "Alice", team_name=None, avatar_id=None)
    status = manager.get_stations_status()
    assert status["stations"][2]["athlete_name"] == "Alice"
    assert status["stations"][2]["team_name"] is None
    assert status["stations"][2]["has_avatar"] is False


def test_race_manager_telemetry_includes_team_and_avatar_url():
    manager = RaceManager()

    # Register athlete 1
    manager.update_active_node("node-01", "fan_bike")
    manager.assign_station(1, "node-01")
    manager.register_athlete(1, "Tony", team_name="RD", avatar_id=AVATAR_A)

    # Register athlete 2
    manager.update_active_node("node-02", "fan_bike")
    manager.assign_station(2, "node-02")
    manager.register_athlete(2, "Alice", team_name=None, avatar_id=None)

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
    assert node_progress["avatar_url"] == f"/api/avatars/{AVATAR_A}.webp"

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


def test_api_avatar_upload_stores_under_stable_id_and_serves_it():
    client = TestClient(app)
    client.post("/api/race/reset")

    res = client.post(
        "/api/race/register",
        json={
            "station_number": 1,
            "athlete_name": "Tony",
            "team_name": "RD",
            "avatar_base64": TINY_WEBP_BASE64,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["stations"]["1"]["athlete_name"] == "Tony"
    assert data["stations"]["1"]["has_avatar"] is True

    # Nothing is written into the program directory any more.
    assert not os.path.exists(legacy_avatar_file(1))

    # The registration's avatar is reachable from its own stable URL, and the
    # state broadcast exposes that same URL (no cache-busting query string).
    client.post(
        "/api/stations/assign", json={"station_number": 1, "node_id": "avatar-n1"}
    )
    client.post(
        "/api/race/register",
        json={
            "station_number": 1,
            "athlete_name": "Tony",
            "avatar_base64": TINY_WEBP_BASE64,
        },
    )
    client.post("/api/race/configure", json={"race_type": "time", "duration_sec": 60})
    rows = client.get("/api/race/state").json()["leaderboard"]
    url = next(r["avatar_url"] for r in rows.values() if r["station_number"] == 1)
    assert url.startswith("/api/avatars/") and url.endswith(".webp")
    assert "?" not in url
    served = client.get(url)
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/webp"
    assert served.content == base64.b64decode(TINY_WEBP_BASE64.split(",", 1)[1])
    client.post("/api/race/reset")


def test_new_registration_on_same_station_gets_a_different_avatar_url():
    client = TestClient(app)
    client.post("/api/race/reset")
    client.post(
        "/api/stations/assign", json={"station_number": 1, "node_id": "avatar-n1"}
    )

    def register_and_get_url(name):
        client.post(
            "/api/race/register",
            json={
                "station_number": 1,
                "athlete_name": name,
                "avatar_base64": TINY_WEBP_BASE64,
            },
        )
        client.post(
            "/api/race/configure", json={"race_type": "time", "duration_sec": 60}
        )
        rows = client.get("/api/race/state").json()["leaderboard"]
        return next(r["avatar_url"] for r in rows.values() if r["station_number"] == 1)

    first = register_and_get_url("First")
    client.post("/api/race/reset")
    client.post(
        "/api/stations/assign", json={"station_number": 1, "node_id": "avatar-n1"}
    )
    second = register_and_get_url("Second")
    client.post("/api/race/reset")

    assert first != second
    # The earlier athlete's photo is still served (history keeps working).
    assert client.get(first).status_code == 200


def test_avatar_endpoint_rejects_non_hex_and_unknown_ids():
    client = TestClient(app)
    assert client.get("/api/avatars/not-hex.webp").status_code == 404
    assert client.get("/api/avatars/" + "A" * 32 + ".webp").status_code == 404
    assert client.get("/api/avatars/" + "a" * 31 + ".webp").status_code == 404
    assert client.get("/api/avatars/..%2f..%2fetc%2fpasswd.webp").status_code == 404
    # Well-formed but nothing stored under it.
    assert client.get("/api/avatars/" + "0" * 32 + ".webp").status_code == 404


def test_api_avatar_upload_rejects_invalid_base64():
    client = TestClient(app)
    client.post("/api/race/reset")

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


def test_api_avatar_upload_rejects_wrong_mime_type():
    client = TestClient(app)
    client.post("/api/race/reset")

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


def test_api_avatar_upload_rejects_non_webp_bytes():
    client = TestClient(app)
    client.post("/api/race/reset")
    png_like_data = (
        "data:image/webp;base64," + base64.b64encode(b"\x89PNG\r\n").decode()
    )

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


def test_api_avatar_upload_rejects_oversized_payload():
    client = TestClient(app)
    client.post("/api/race/reset")
    oversized_webp = (
        b"RIFF" + (300_000).to_bytes(4, "little") + b"WEBP" + (b"0" * 300_000)
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


def test_avatar_url_is_identical_across_telemetry_ticks_and_race_phases(monkeypatch):
    """The old URL carried ?t=<seconds>, so the browser re-fetched the photo
    every second. One registration must keep one URL for the whole race."""
    import time

    manager = RaceManager()
    manager.update_active_node("node-01", "treadmill")
    manager.assign_station(1, "node-01")
    manager.register_athlete(1, "Tony", avatar_id=AVATAR_A)
    manager.configure(RaceConfig(race_type="time", duration_sec=180))
    expected = f"/api/avatars/{AVATAR_A}.webp"

    monkeypatch.setattr(time, "time", lambda: 1_000.0)
    manager.start_race()
    assert manager.get_leaderboard_progress()["node-01"]["avatar_url"] == expected

    urls = []
    for now, dist in [(1_001.0, 5.0), (1_002.0, 10.0), (1_050.5, 40.0)]:
        monkeypatch.setattr(time, "time", lambda now=now: now)
        progress = manager.update_telemetry(
            {"node_id": "node-01", "distance_m": dist, "elapsed_time_ms": 1000}
        )
        urls.append(progress["node-01"]["avatar_url"])

    assert urls == [expected] * 3


def test_avatar_url_reaches_the_state_snapshot_used_for_saved_results():
    manager = RaceManager()
    manager.update_active_node("node-01", "treadmill")
    manager.assign_station(1, "node-01")
    manager.register_athlete(1, "Tony", avatar_id=AVATAR_A)
    manager.configure(RaceConfig(race_type="time", duration_sec=180))
    manager.start_race()
    manager.update_telemetry({"node_id": "node-01", "distance_m": 5.0})
    manager.stop_race()

    row = manager.get_state_snapshot()["leaderboard"]["node-01"]
    assert row["avatar_url"] == f"/api/avatars/{AVATAR_A}.webp"
