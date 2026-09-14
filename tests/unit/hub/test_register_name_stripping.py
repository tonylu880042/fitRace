"""RegisterAthletePayload strips surrounding whitespace from athlete_name
and team_name; a whitespace-only value normalizes to None for both (mirrors
the existing division normalization) -- see hub_server/infrastructure/
fastapi/app.py RegisterAthletePayload."""

from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi.app import app


def test_register_athlete_strips_surrounding_whitespace_from_names():
    client = TestClient(app)
    client.post("/api/race/reset")

    res = client.post(
        "/api/race/register",
        json={
            "station_number": 1,
            "athlete_name": " Alice ",
            "team_name": "  ",
        },
    )
    assert res.status_code == 200
    assert res.json()["stations"]["1"]["athlete_name"] == "Alice"
    assert res.json()["stations"]["1"]["team_name"] is None

    stations_res = client.get("/api/stations")
    assert stations_res.json()["stations"]["1"]["athlete_name"] == "Alice"
    assert stations_res.json()["stations"]["1"]["team_name"] is None

    client.post("/api/race/reset")


def test_register_athlete_strips_surrounding_whitespace_from_team_name():
    client = TestClient(app)
    client.post("/api/race/reset")

    res = client.post(
        "/api/race/register",
        json={
            "station_number": 1,
            "athlete_name": "Bob",
            "team_name": "  Red Team  ",
        },
    )
    assert res.status_code == 200
    assert res.json()["stations"]["1"]["team_name"] == "Red Team"

    client.post("/api/race/reset")
