"""RaceResultsQuery.get_standings() / GET /api/results/standings.

An "overall standings" ranking answers a format `get_records()` cannot:
several heats on the same 2 machines (e.g. 3 relay heats x 2 teams), each
saved as its own race in the results jsonl, need ONE combined ranking
across every heat -- not just the top 3 records for the most-recently-
contested category.

Scope is (race_type, label, relay_legs) of the MOST RECENTLY stored race
ONLY -- never a race further back in history (division is deliberately
NOT part of the key, so men's and women's heats of the same event share
one scope and are then split back out into per-division sections). If
that newest race is "mixed" (or otherwise not a well-formed category),
there is no scope and get_standings() returns the empty shape rather than
reaching past it to an older category -- the dashboard then falls back to
the existing mixed per-group slides. Every stored race that DOES match
the scope contributes its rows; ranking reuses
RaceResultsQuery._order_by_race_type (finishers by finished_time_ms
ascending then non-finishers by progress descending for distance/calories;
distance_m desc for time/watts; max_power_watts desc for max_power). A
named athlete keeps only their single best row across all contributing
races, with a finisher always beating a non-finisher -- same dedupe rule
as get_records()/_top_three, applied to the combined, already best-first
ordered list so "first occurrence wins" is enough.
"""

from fastapi.testclient import TestClient

from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery


def _row(
    node_id,
    athlete_name,
    station_number=1,
    distance_m=0,
    calories=0,
    max_power_watts=0,
    finished_time_ms=None,
    division=None,
    team_name=None,
    relay_members=None,
):
    row = {
        "node_id": node_id,
        "athlete_name": athlete_name,
        "station_number": station_number,
        "team_name": team_name,
        "division": division,
        "avatar_url": None,
        "distance_m": distance_m,
        "elapsed_time_ms": 60000,
        "instantaneous_speed_kph": 0.0,
        "progress_percent": 100.0 if finished_time_ms is not None else 50.0,
        "calories": calories,
        "power_watts": 0,
        "max_power_watts": max_power_watts,
        "finished_time_ms": finished_time_ms,
    }
    if relay_members is not None:
        row["relay_members"] = relay_members
    return row


def _snapshot(
    target_value,
    start_ms,
    end_ms,
    race_type="distance",
    rows=None,
    duration_sec=0,
    competition_mode="individual",
    relay_legs=None,
):
    return {
        "state": "STOPPED",
        "config": {
            "race_type": race_type,
            "competition_mode": competition_mode,
            "team_scoring_policy": None,
            "target_value": target_value,
            "duration_sec": duration_sec,
            "relay_legs": relay_legs,
        },
        "start_time_epoch_ms": start_ms,
        "end_time_epoch_ms": end_ms,
        "leaderboard": rows or {},
        "team_leaderboard": None,
    }


def test_get_standings_combines_relay_heats_ranked_by_time_across_heats(tmp_path):
    # 3 relay heats x 2 teams -> one combined ranking of all 6 rows.
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1000,
            1000,
            2000,
            competition_mode="relay",
            relay_legs=2,
            rows={
                "node-01": _row(
                    "node-01",
                    "Alpha",
                    distance_m=1000,
                    finished_time_ms=60000,
                    relay_members=["A1", "A2"],
                ),
                "node-02": _row(
                    "node-02",
                    "Bravo",
                    distance_m=1000,
                    finished_time_ms=50000,
                    relay_members=["B1", "B2"],
                ),
            },
        )
    )
    store.save_finished_snapshot(
        _snapshot(
            1000,
            3000,
            4000,
            competition_mode="relay",
            relay_legs=2,
            rows={
                "node-01": _row(
                    "node-01",
                    "Charlie",
                    distance_m=1000,
                    finished_time_ms=45000,
                    relay_members=["C1", "C2"],
                ),
                "node-02": _row(
                    "node-02",
                    "Delta",
                    distance_m=1000,
                    finished_time_ms=70000,
                    relay_members=["D1", "D2"],
                ),
            },
        )
    )
    store.save_finished_snapshot(
        _snapshot(
            1000,
            5000,
            6000,
            competition_mode="relay",
            relay_legs=2,
            rows={
                "node-01": _row(
                    "node-01",
                    "Echo",
                    distance_m=1000,
                    finished_time_ms=40000,
                    relay_members=["E1", "E2"],
                ),
                "node-02": _row(
                    "node-02",
                    "Foxtrot",
                    distance_m=1000,
                    finished_time_ms=55000,
                    relay_members=["F1", "F2"],
                ),
            },
        )
    )
    query = RaceResultsQuery(store)

    standings = query.get_standings()

    assert standings["race_type"] == "distance"
    assert standings["label"] == "1000 m"
    assert standings["relay_legs"] == 2
    assert standings["race_count"] == 3
    assert len(standings["sections"]) == 1
    rows = standings["sections"][0]["rows"]
    assert [r["athlete_name"] for r in rows] == [
        "Echo",
        "Charlie",
        "Bravo",
        "Foxtrot",
        "Alpha",
        "Delta",
    ]
    assert [r["rank"] for r in rows] == [1, 2, 3, 4, 5, 6]
    assert rows[0]["value"] == 40000
    assert rows[0]["relay_members"] == ["E1", "E2"]
    assert all(r["finished"] is True for r in rows)


def test_get_standings_ranks_dnf_rows_after_finishers_by_progress(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1000,
            1000,
            2000,
            rows={
                "node-01": _row(
                    "node-01", "Alice", distance_m=1000, finished_time_ms=60000
                ),
                "node-02": _row("node-02", "Bob", distance_m=800),  # DNF, 800m
                "node-03": _row("node-03", "Cara", distance_m=900),  # DNF, 900m
            },
        )
    )
    query = RaceResultsQuery(store)

    rows = query.get_standings()["sections"][0]["rows"]

    assert [r["athlete_name"] for r in rows] == ["Alice", "Cara", "Bob"]
    assert rows[0]["finished"] is True
    assert rows[1]["finished"] is False
    assert rows[1]["value"] == 900
    assert rows[2]["finished"] is False
    assert rows[2]["value"] == 800


def test_get_standings_returns_empty_when_the_newest_race_is_mixed(tmp_path):
    # Scope is decided ONLY by the newest stored race -- a mixed race never
    # reaches PAST itself to an older non-mixed category. Standings must go
    # empty (so the dashboard falls back to the mixed per-group slides)
    # rather than silently showing a stale 1000 m ranking.
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1000,
            1000,
            2000,
            rows={
                "node-01": _row(
                    "node-01", "Alice", distance_m=1000, finished_time_ms=60000
                ),
            },
        )
    )
    # Newer, and "mixed" -- must blank the scope entirely, not be skipped.
    store.save_finished_snapshot(
        _snapshot(
            0,
            3000,
            4000,
            race_type="mixed",
            duration_sec=300,
            rows={
                "node-01": _row("node-01", "Ghost", distance_m=1234),
            },
        )
    )
    query = RaceResultsQuery(store)

    assert query.get_standings() == {"race_type": None, "sections": [], "race_count": 0}


def test_get_standings_excludes_a_different_category(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    # Older (appended first), different target -- must never contribute
    # rows to the 1000 m scope, which is keyed off the MOST RECENTLY
    # stored (i.e. most recently appended) race.
    store.save_finished_snapshot(
        _snapshot(
            500,
            1000,
            2000,
            rows={
                "node-01": _row(
                    "node-01", "Zed", distance_m=500, finished_time_ms=10000
                ),
            },
        )
    )
    store.save_finished_snapshot(
        _snapshot(
            1000,
            3000,
            4000,
            rows={
                "node-01": _row(
                    "node-01", "Alice", distance_m=1000, finished_time_ms=60000
                ),
            },
        )
    )
    query = RaceResultsQuery(store)

    standings = query.get_standings()

    assert standings["label"] == "1000 m"
    assert standings["race_count"] == 1
    rows = standings["sections"][0]["rows"]
    assert [r["athlete_name"] for r in rows] == ["Alice"]


def test_get_standings_keeps_only_each_athletes_best_row_finisher_beats_dnf(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    # Heat 1: Alice does not finish (progress only).
    store.save_finished_snapshot(
        _snapshot(
            1000,
            1000,
            2000,
            rows={
                "node-01": _row("node-01", "Alice", distance_m=999),
            },
        )
    )
    # Heat 2 (more recent): Alice finishes -- her finisher row must win even
    # though a bare numeric comparison of "value" would not obviously favor it.
    store.save_finished_snapshot(
        _snapshot(
            1000,
            3000,
            4000,
            rows={
                "node-01": _row(
                    "node-01", "Alice", distance_m=1000, finished_time_ms=90000
                ),
                "node-02": _row(
                    "node-02", "Bob", distance_m=1000, finished_time_ms=50000
                ),
            },
        )
    )
    query = RaceResultsQuery(store)

    rows = query.get_standings()["sections"][0]["rows"]

    assert [r["athlete_name"] for r in rows].count("Alice") == 1
    alice = next(r for r in rows if r["athlete_name"] == "Alice")
    assert alice["finished"] is True
    assert alice["value"] == 90000


def test_get_standings_splits_into_division_sections_ordered_null_men_women(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1000,
            1000,
            2000,
            rows={
                "node-01": _row(
                    "node-01",
                    "Wendy",
                    distance_m=1000,
                    finished_time_ms=60000,
                    division="women",
                ),
                "node-02": _row(
                    "node-02",
                    "Marco",
                    distance_m=1000,
                    finished_time_ms=55000,
                    division="men",
                ),
                "node-03": _row(
                    "node-03", "Noel", distance_m=1000, finished_time_ms=58000
                ),
            },
        )
    )
    query = RaceResultsQuery(store)

    sections = query.get_standings()["sections"]

    assert [s["division"] for s in sections] == [None, "men", "women"]
    assert [r["athlete_name"] for r in sections[0]["rows"]] == ["Noel"]
    assert [r["athlete_name"] for r in sections[1]["rows"]] == ["Marco"]
    assert [r["athlete_name"] for r in sections[2]["rows"]] == ["Wendy"]
    # Ranks restart at 1 within each section.
    assert sections[1]["rows"][0]["rank"] == 1
    assert sections[2]["rows"][0]["rank"] == 1


def test_get_standings_empty_store_returns_empty_shape(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    query = RaceResultsQuery(store)

    assert query.get_standings() == {"race_type": None, "sections": [], "race_count": 0}


def test_get_standings_only_mixed_races_returns_empty_shape(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            0,
            1000,
            2000,
            race_type="mixed",
            duration_sec=300,
            rows={"node-01": _row("node-01", "Ghost", distance_m=1234)},
        )
    )
    query = RaceResultsQuery(store)

    assert query.get_standings() == {"race_type": None, "sections": [], "race_count": 0}


def test_get_standings_api_endpoint_returns_combined_ranking(tmp_path, monkeypatch):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            100,
            1000,
            2000,
            rows={
                "node-01": _row(
                    "node-01", "Alice", distance_m=100, finished_time_ms=20000
                ),
            },
        )
    )
    query = RaceResultsQuery(store)

    from hub_server.infrastructure.fastapi import app as app_module

    monkeypatch.setattr(app_module, "race_results_query", query)
    client = TestClient(app_module.app)

    response = client.get("/api/results/standings")

    assert response.status_code == 200
    body = response.json()
    assert body["race_type"] == "distance"
    assert body["label"] == "100 m"
    assert body["race_count"] == 1
    assert body["sections"][0]["rows"][0]["athlete_name"] == "Alice"
