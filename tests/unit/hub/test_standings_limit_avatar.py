"""A4: /api/results/standings?limit=N keeps only the top N rows of each
section, and every row carries its registration's avatar_url."""

from fastapi.testclient import TestClient

import hub_server.infrastructure.fastapi.app as hub_app
from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery


def _row(n, name, distance, division=None, avatar_url=None):
    return {
        "node_id": f"node-{n:02d}",
        "athlete_name": name,
        "is_registered_name": True,
        "station_number": n,
        "team_name": None,
        "division": division,
        "avatar_url": avatar_url,
        "distance_m": distance,
        "elapsed_time_ms": 180000,
        "progress_percent": 100.0,
        "calories": 0,
        "max_power_watts": 0,
        "finished_time_ms": None,
    }


def _store_with(tmp_path, rows):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        {
            "state": "STOPPED",
            "config": {
                "race_type": "time",
                "competition_mode": "individual",
                "team_scoring_policy": None,
                "target_value": 0,
                "duration_sec": 180,
                "relay_legs": None,
            },
            "start_time_epoch_ms": 1000,
            "end_time_epoch_ms": 181000,
            "leaderboard": {r["node_id"]: r for r in rows},
            "team_leaderboard": None,
        }
    )
    return store


def test_limit_keeps_only_the_top_n_rows_of_each_section(tmp_path):
    rows = [_row(i, f"Runner {i}", 1000 - i * 10) for i in range(1, 13)]
    query = RaceResultsQuery(_store_with(tmp_path, rows))

    standings = query.get_standings(limit=10)

    section = standings["sections"][0]
    assert [r["rank"] for r in section["rows"]] == list(range(1, 11))
    assert section["rows"][0]["athlete_name"] == "Runner 1"
    assert section["rows"][-1]["athlete_name"] == "Runner 10"


def test_limit_applies_per_division_section(tmp_path):
    rows = [_row(i, f"M{i}", 900 - i, division="men") for i in range(1, 5)] + [
        _row(i + 10, f"W{i}", 800 - i, division="women") for i in range(1, 5)
    ]
    query = RaceResultsQuery(_store_with(tmp_path, rows))

    sections = query.get_standings(limit=2)["sections"]

    assert [(s["division"], len(s["rows"])) for s in sections] == [
        ("men", 2),
        ("women", 2),
    ]


def test_no_limit_returns_every_row(tmp_path):
    rows = [_row(i, f"Runner {i}", 1000 - i) for i in range(1, 13)]
    query = RaceResultsQuery(_store_with(tmp_path, rows))

    assert len(query.get_standings()["sections"][0]["rows"]) == 12


def test_rows_carry_avatar_url_or_null(tmp_path):
    rows = [
        _row(1, "Pic", 500, avatar_url="/api/avatars/" + "a" * 32 + ".webp"),
        _row(2, "NoPic", 400),
    ]
    query = RaceResultsQuery(_store_with(tmp_path, rows))

    got = {r["athlete_name"]: r for r in query.get_standings()["sections"][0]["rows"]}

    assert got["Pic"]["avatar_url"] == "/api/avatars/" + "a" * 32 + ".webp"
    assert got["NoPic"]["avatar_url"] is None


def test_standings_endpoint_accepts_limit(tmp_path, monkeypatch):
    rows = [_row(i, f"Runner {i}", 1000 - i) for i in range(1, 13)]
    store = _store_with(tmp_path, rows)
    monkeypatch.setattr(hub_app, "race_results_query", RaceResultsQuery(store))
    client = TestClient(hub_app.app)

    limited = client.get("/api/results/standings?limit=10").json()
    full = client.get("/api/results/standings").json()

    assert len(limited["sections"][0]["rows"]) == 10
    assert len(full["sections"][0]["rows"]) == 12
    assert client.get("/api/results/standings?limit=0").status_code == 422
