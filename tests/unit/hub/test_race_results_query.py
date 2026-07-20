import hashlib

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
):
    return {
        "node_id": node_id,
        "athlete_name": athlete_name,
        "station_number": station_number,
        "team_name": None,
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


def _distance_snapshot():
    return {
        "state": "STOPPED",
        "config": {
            "race_type": "distance",
            "competition_mode": "individual",
            "team_scoring_policy": None,
            "target_value": 100,
            "duration_sec": 0,
        },
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {
            "node-01": _row("node-01", "Alice", station_number=1, distance_m=100, finished_time_ms=5000),
            "node-02": _row("node-02", "Bob", station_number=2, distance_m=80),
            "node-03": _row("node-03", "Cara", station_number=3, distance_m=90),
            "node-04": _row("node-04", "", station_number=4, distance_m=0),
        },
        "team_leaderboard": None,
    }


def _time_snapshot():
    return {
        "state": "STOPPED",
        "config": {
            "race_type": "time",
            "competition_mode": "individual",
            "team_scoring_policy": None,
            "target_value": 0,
            "duration_sec": 120,
        },
        "start_time_epoch_ms": 3000,
        "end_time_epoch_ms": 4000,
        "leaderboard": {
            "node-01": _row("node-01", "Dave", station_number=1, distance_m=500),
            "node-02": _row("node-02", "Erin", station_number=2, distance_m=700),
            "node-03": _row("node-03", "Frank", station_number=3, distance_m=700),
        },
        "team_leaderboard": None,
    }


def _max_power_snapshot():
    return {
        "state": "STOPPED",
        "config": {
            "race_type": "max_power",
            "competition_mode": "individual",
            "team_scoring_policy": None,
            "target_value": 0,
            "duration_sec": 60,
        },
        "start_time_epoch_ms": 5000,
        "end_time_epoch_ms": 6000,
        "leaderboard": {
            "node-01": _row("node-01", "Gina", station_number=1, max_power_watts=300),
            "node-02": _row("node-02", "Hank", station_number=2, max_power_watts=450),
        },
        "team_leaderboard": None,
    }


def _build_store(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(_distance_snapshot())
    store.save_finished_snapshot(_time_snapshot())
    store.save_finished_snapshot(_max_power_snapshot())
    return store


def test_list_races_returns_newest_first_summaries(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    races = query.list_races(limit=20)

    assert [r["result_id"] for r in races] == [
        "5000-6000-max_power",
        "3000-4000-time",
        "1000-2000-distance",
    ]
    distance_summary = races[-1]
    assert distance_summary["race_type"] == "distance"
    assert distance_summary["competition_mode"] == "individual"
    assert distance_summary["start_time_epoch_ms"] == 1000
    assert distance_summary["end_time_epoch_ms"] == 2000
    # empty-athlete-name row excluded from athlete_count
    assert distance_summary["athlete_count"] == 3


def test_list_races_respects_limit(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    races = query.list_races(limit=2)

    assert len(races) == 2
    assert races[0]["result_id"] == "5000-6000-max_power"


def test_get_race_distance_ranks_finishers_then_progress_desc(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    race = query.get_race("1000-2000-distance")

    assert race is not None
    names_ranks = [(r["athlete_name"], r["rank"]) for r in race["results"]]
    assert names_ranks == [("Alice", 1), ("Cara", 2), ("Bob", 3)]
    # empty-athlete-name row excluded entirely
    assert all(r["athlete_name"] for r in race["results"])
    assert len(race["results"]) == 3
    assert race["team_leaderboard"] is None
    assert race["race_type"] == "distance"


def test_get_race_time_ranks_by_distance_desc_with_stable_ties(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    race = query.get_race("3000-4000-time")

    names_ranks = [(r["athlete_name"], r["rank"]) for r in race["results"]]
    # Erin and Frank tie at 700m; Erin appears first in the leaderboard dict
    # so stable ordering keeps her ranked ahead of Frank.
    assert names_ranks == [("Erin", 1), ("Frank", 2), ("Dave", 3)]


def test_get_race_max_power_ranks_by_max_power_desc(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    race = query.get_race("5000-6000-max_power")

    names_ranks = [(r["athlete_name"], r["rank"]) for r in race["results"]]
    assert names_ranks == [("Hank", 1), ("Gina", 2)]


def test_get_race_unknown_result_id_returns_none(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    assert query.get_race("does-not-exist") is None


def test_token_is_stable_and_unique_per_node(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    race_first = query.get_race("1000-2000-distance")
    race_second = query.get_race("1000-2000-distance")

    tokens_first = {r["athlete_name"]: r["token"] for r in race_first["results"]}
    tokens_second = {r["athlete_name"]: r["token"] for r in race_second["results"]}
    assert tokens_first == tokens_second
    assert len(set(tokens_first.values())) == len(tokens_first)

    expected_alice_token = hashlib.sha1(b"1000-2000-distance:node-01").hexdigest()[:12]
    alice_token = next(r["token"] for r in race_first["results"] if r["athlete_name"] == "Alice")
    assert alice_token == expected_alice_token


def test_get_athlete_result_found(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)
    token = hashlib.sha1(b"1000-2000-distance:node-01").hexdigest()[:12]

    result = query.get_athlete_result(token)

    assert result is not None
    assert result["race"]["result_id"] == "1000-2000-distance"
    assert result["athlete"]["athlete_name"] == "Alice"
    assert result["athlete"]["rank"] == 1
    assert result["total_athletes"] == 3


def test_get_athlete_result_not_found_returns_none(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    assert query.get_athlete_result("0" * 12) is None


def _distance_snapshot_variant(
    target_value, start_ms, end_ms, race_type="distance", rows=None, duration_sec=0
):
    return {
        "state": "STOPPED",
        "config": {
            "race_type": race_type,
            "competition_mode": "individual",
            "team_scoring_policy": None,
            "target_value": target_value,
            "duration_sec": duration_sec,
        },
        "start_time_epoch_ms": start_ms,
        "end_time_epoch_ms": end_ms,
        "leaderboard": rows or {},
        "team_leaderboard": None,
    }


def test_get_records_groups_distance_by_target_and_ranks_top_three(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _distance_snapshot_variant(
            1000,
            1000,
            2000,
            rows={
                "node-01": _row("node-01", "Alice", distance_m=1000, finished_time_ms=60000),
                "node-02": _row("node-02", "Bob", distance_m=1000, finished_time_ms=50000),
                "node-03": _row("node-03", "Cara", distance_m=1000, finished_time_ms=70000),
                "node-04": _row("node-04", "Dan", distance_m=1000, finished_time_ms=40000),
            },
        )
    )
    # A different target_value must land in its own category.
    store.save_finished_snapshot(
        _distance_snapshot_variant(
            500,
            3000,
            4000,
            rows={"node-01": _row("node-01", "Erin", distance_m=500, finished_time_ms=30000)},
        )
    )
    query = RaceResultsQuery(store)

    records = query.get_records()["records"]

    assert len(records) == 2
    thousand_m = next(r for r in records if r["label"] == "1000 m")
    assert thousand_m["race_type"] == "distance"
    # Ascending by finished_time_ms (fastest first), top 3 only -- Cara (slowest) cut.
    assert [e["athlete_name"] for e in thousand_m["entries"]] == ["Dan", "Bob", "Alice"]
    assert len(thousand_m["entries"]) == 3
    assert thousand_m["entries"][0]["value"] == 40000
    assert thousand_m["entries"][0]["end_time_epoch_ms"] == 2000

    five_hundred_m = next(r for r in records if r["label"] == "500 m")
    assert [e["athlete_name"] for e in five_hundred_m["entries"]] == ["Erin"]


def test_get_records_excludes_non_finishers_from_distance_records(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _distance_snapshot_variant(
            1000,
            1000,
            2000,
            rows={
                "node-01": _row("node-01", "Alice", distance_m=1000, finished_time_ms=60000),
                "node-02": _row("node-02", "Bob", distance_m=900),  # never finished
                "node-03": _row("node-03", "", distance_m=1000, finished_time_ms=1000),  # no name
            },
        )
    )
    query = RaceResultsQuery(store)

    records = query.get_records()["records"]

    assert len(records) == 1
    assert [e["athlete_name"] for e in records[0]["entries"]] == ["Alice"]


def test_get_records_time_boxed_races_ordered_by_distance_desc(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _distance_snapshot_variant(
            0,
            1000,
            2000,
            race_type="time",
            duration_sec=600,
            rows={
                "node-01": _row("node-01", "Alice", distance_m=500),
                "node-02": _row("node-02", "Bob", distance_m=700),
            },
        )
    )
    query = RaceResultsQuery(store)

    records = query.get_records()["records"]

    assert len(records) == 1
    assert records[0]["race_type"] == "time"
    assert records[0]["label"] == "10 min"
    assert [e["athlete_name"] for e in records[0]["entries"]] == ["Bob", "Alice"]


def test_get_records_max_power_ordered_descending(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    snapshot = _distance_snapshot_variant(
        0,
        5000,
        6000,
        race_type="max_power",
        duration_sec=60,
        rows={
            "node-01": _row("node-01", "Gina", max_power_watts=300),
            "node-02": _row("node-02", "Hank", max_power_watts=450),
        },
    )
    store.save_finished_snapshot(snapshot)
    query = RaceResultsQuery(store)

    records = query.get_records()["records"]

    assert len(records) == 1
    assert records[0]["label"] == "1 min"
    assert [e["athlete_name"] for e in records[0]["entries"]] == ["Hank", "Gina"]


def test_get_records_orders_categories_most_recently_contested_first(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _distance_snapshot_variant(
            1000, 1000, 2000,
            rows={"node-01": _row("node-01", "Alice", distance_m=1000, finished_time_ms=1000)},
        )
    )
    store.save_finished_snapshot(
        _distance_snapshot_variant(
            500, 3000, 4000,
            rows={"node-01": _row("node-01", "Bob", distance_m=500, finished_time_ms=1000)},
        )
    )
    # Re-contest the 1000m category more recently than the 500m one.
    store.save_finished_snapshot(
        _distance_snapshot_variant(
            1000, 5000, 6000,
            rows={"node-01": _row("node-01", "Cara", distance_m=1000, finished_time_ms=900)},
        )
    )
    query = RaceResultsQuery(store)

    records = query.get_records()["records"]

    assert [r["label"] for r in records] == ["1000 m", "500 m"]


def test_get_records_returns_empty_list_with_no_stored_results(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    query = RaceResultsQuery(store)

    assert query.get_records() == {"records": []}


def test_malformed_jsonl_line_is_skipped(tmp_path):
    store = _build_store(tmp_path)
    # Append a structurally-valid-JSON but semantically malformed line directly,
    # bypassing the store's write path (as legacy/corrupt data might look).
    with store.path.open("a", encoding="utf-8") as f:
        f.write('{"result_id": "broken", "snapshot": "not-a-dict"}\n')
        f.write("not even json\n")

    query = RaceResultsQuery(store)

    races = query.list_races(limit=20)
    result_ids = [r["result_id"] for r in races]
    assert "broken" not in result_ids
    assert len(result_ids) == 3
    assert query.get_race("broken") is None
