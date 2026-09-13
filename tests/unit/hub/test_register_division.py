"""RegisterAthletePayload gains an optional men/women division (mirroring
the existing team_name field): a blank string normalizes to None just like
athlete_name, an invalid value is rejected with 422, /api/stations reflects
it, and /api/race/reset clears it -- see hub_server/infrastructure/fastapi/
app.py RegisterAthletePayload and hub_server/usecases/race_manager.py
register_athlete."""

from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi.app import app


def test_register_athlete_with_division_shows_on_stations(tmp_path, monkeypatch):
    client = TestClient(app)
    client.post("/api/race/reset")

    res = client.post(
        "/api/race/register",
        json={
            "station_number": 1,
            "athlete_name": "Tony",
            "division": "women",
        },
    )
    assert res.status_code == 200
    assert res.json()["stations"]["1"]["division"] == "women"

    stations_res = client.get("/api/stations")
    assert stations_res.json()["stations"]["1"]["division"] == "women"

    client.post("/api/race/reset")


def test_register_athlete_blank_division_normalizes_to_none():
    client = TestClient(app)
    client.post("/api/race/reset")

    res = client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Tony", "division": ""},
    )
    assert res.status_code == 200
    assert res.json()["stations"]["1"]["division"] is None

    client.post("/api/race/reset")


def test_register_athlete_invalid_division_returns_422():
    client = TestClient(app)
    client.post("/api/race/reset")

    res = client.post(
        "/api/race/register",
        json={
            "station_number": 1,
            "athlete_name": "Tony",
            "division": "nonbinary-of-doom",
        },
    )
    assert res.status_code == 422

    client.post("/api/race/reset")


def test_reset_race_clears_division_via_api():
    client = TestClient(app)
    client.post("/api/race/reset")

    client.post(
        "/api/race/register",
        json={"station_number": 1, "athlete_name": "Tony", "division": "men"},
    )
    client.post("/api/race/reset")

    stations_res = client.get("/api/stations").json()["stations"]
    assert stations_res.get("1", {}).get("division") is None
