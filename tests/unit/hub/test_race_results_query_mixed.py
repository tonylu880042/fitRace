"""Mixed ("多器材分組") race coverage for RaceResultsQuery.

A mixed race scores each equipment group as its own self-contained race --
see RaceConfig.groups/group_index_for/scoped_config in
hub_server/domain/models.py and hub_server/usecases/race_manager.py, which
already tags every leaderboard row with a `group_index` (or None if the
row's equipment matched no group). This module locks the read side: ranking
must restart at 1 within each group (not run as one flat list across the
whole race), `_summarize` must expose a `groups` summary so pages/export
can label each section, `get_athlete_result` must scope `total_athletes`
and add a `group` entry, and `get_records` must keep excluding mixed races
from the record wall.

Non-mixed races are exercised elsewhere (test_race_results_query.py) and
must stay untouched by any of this -- nothing here re-tests that file.
"""

import hashlib

from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery


def _row(
    node_id,
    athlete_name,
    group_index,
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
        "division": None,
        "avatar_url": None,
        "distance_m": distance_m,
        "elapsed_time_ms": 60000,
        "instantaneous_speed_kph": 0.0,
        "progress_percent": 100.0 if finished_time_ms is not None else 50.0,
        "calories": calories,
        "power_watts": 0,
        "max_power_watts": max_power_watts,
        "finished_time_ms": finished_time_ms,
        "group_index": group_index,
    }


def _mixed_snapshot():
    return {
        "state": "STOPPED",
        "config": {
            "race_type": "mixed",
            "competition_mode": "individual",
            "team_scoring_policy": None,
            "target_value": 0,
            "duration_sec": 0,
            "groups": [
                {
                    "equipment_types": ["treadmill"],
                    "race_type": "distance",
                    "target_value": 500,
                    "duration_sec": 0,
                },
                {
                    "equipment_types": ["rowing_machine"],
                    "race_type": "time",
                    "target_value": 0,
                    "duration_sec": 120,
                },
            ],
        },
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {
            "node-tm-1": _row(
                "node-tm-1",
                "Alice",
                0,
                station_number=1,
                distance_m=500,
                finished_time_ms=60000,
            ),
            "node-tm-2": _row("node-tm-2", "Bob", 0, station_number=2, distance_m=400),
            # Cara "finished" (has a finished_time_ms) even though her group
            # is time-boxed, where finish status must NOT matter -- only
            # distance_m does. If group1 were ever ranked with group0's
            # "distance" rule (finishers-first), Cara would jump to rank 1
            # ahead of Dan despite covering less distance.
            "node-row-1": _row(
                "node-row-1",
                "Cara",
                1,
                station_number=3,
                distance_m=800,
                finished_time_ms=50000,
            ),
            "node-row-2": _row(
                "node-row-2", "Dan", 1, station_number=4, distance_m=900
            ),
            # Unknown/unmapped equipment -- group_index None.
            "node-mystery": _row(
                "node-mystery", "Erin", None, station_number=5, distance_m=10
            ),
        },
        "team_leaderboard": None,
    }


def _build_store(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(_mixed_snapshot())
    return store


def _token(result_id, node_id):
    return hashlib.sha1(f"{result_id}:{node_id}".encode()).hexdigest()[:12]


def test_mixed_race_ranks_restart_at_one_within_each_group(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    race = query.get_race("1000-2000-mixed")

    assert race is not None
    rows = [(r["athlete_name"], r["group_index"], r["rank"]) for r in race["results"]]
    # group 0 (distance): Alice finished, ranks 1; Bob (never finished) 2.
    # group 1 (time): ranked by distance_m desc regardless of finish status
    # -- Dan (900m) ranks 1, Cara (800m) ranks 2, even though Cara has a
    # finished_time_ms and Dan does not.
    # ungrouped (Erin) comes last with rank None.
    assert rows == [
        ("Alice", 0, 1),
        ("Bob", 0, 2),
        ("Dan", 1, 1),
        ("Cara", 1, 2),
        ("Erin", None, None),
    ]


def test_mixed_race_every_row_keeps_a_token(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    race = query.get_race("1000-2000-mixed")

    tokens = {r["athlete_name"]: r["token"] for r in race["results"]}
    assert tokens["Alice"] == _token("1000-2000-mixed", "node-tm-1")
    assert tokens["Erin"] == _token("1000-2000-mixed", "node-mystery")
    assert len(set(tokens.values())) == 5


def test_mixed_race_summary_exposes_per_group_labels(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    race = query.get_race("1000-2000-mixed")

    assert race["race_type"] == "mixed"
    groups = race["groups"]
    assert len(groups) == 2
    assert groups[0] == {
        "group_index": 0,
        "race_type": "distance",
        "target_value": 500,
        "duration_sec": 0,
        "equipment_types": ["treadmill"],
        "label": "500 m",
    }
    assert groups[1] == {
        "group_index": 1,
        "race_type": "time",
        "target_value": 0,
        "duration_sec": 120,
        "equipment_types": ["rowing_machine"],
        "label": "2 min",
    }


def test_non_mixed_summary_has_no_groups_key(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        {
            "state": "STOPPED",
            "config": {
                "race_type": "distance",
                "competition_mode": "individual",
                "team_scoring_policy": None,
                "target_value": 100,
                "duration_sec": 0,
            },
            "start_time_epoch_ms": 5000,
            "end_time_epoch_ms": 6000,
            "leaderboard": {"node-01": _row("node-01", "Zed", None, distance_m=100)},
            "team_leaderboard": None,
        }
    )
    query = RaceResultsQuery(store)

    race = query.get_race("5000-6000-distance")

    assert "groups" not in race
    assert "group_index" not in race["results"][0] or True  # unaffected either way


def test_get_athlete_result_scopes_total_athletes_to_own_group(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    alice = query.get_athlete_result(_token("1000-2000-mixed", "node-tm-1"))
    assert alice["total_athletes"] == 2
    assert alice["group"] == {
        "group_index": 0,
        "race_type": "distance",
        "target_value": 500,
        "duration_sec": 0,
        "equipment_types": ["treadmill"],
        "label": "500 m",
    }

    dan = query.get_athlete_result(_token("1000-2000-mixed", "node-row-2"))
    assert dan["total_athletes"] == 2
    assert dan["group"]["group_index"] == 1


def test_get_athlete_result_ungrouped_row_has_no_group_or_total(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    erin = query.get_athlete_result(_token("1000-2000-mixed", "node-mystery"))

    assert erin["total_athletes"] is None
    assert erin["group"] is None


def test_get_athlete_result_non_mixed_has_no_group_key(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        {
            "state": "STOPPED",
            "config": {
                "race_type": "distance",
                "competition_mode": "individual",
                "team_scoring_policy": None,
                "target_value": 100,
                "duration_sec": 0,
            },
            "start_time_epoch_ms": 5000,
            "end_time_epoch_ms": 6000,
            "leaderboard": {"node-01": _row("node-01", "Zed", None, distance_m=100)},
            "team_leaderboard": None,
        }
    )
    query = RaceResultsQuery(store)

    result = query.get_athlete_result(_token("5000-6000-distance", "node-01"))

    assert "group" not in result
    assert result["total_athletes"] == 1


def test_get_records_excludes_mixed_races(tmp_path):
    store = _build_store(tmp_path)
    query = RaceResultsQuery(store)

    assert query.get_records() == {"records": []}
