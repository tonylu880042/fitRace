"""Anonymous participants must not vanish from race results.

`athlete_name` became optional in commit a2a397c ("feat(hub): allow anonymous
participation without an athlete name"). A blank/whitespace name normalizes
to `None` on registration (RegisterAthletePayload), so a real anonymous
finisher's leaderboard row looks like `{"athlete_name": None,
"station_number": 3, ...}`. `race_results_query.py` previously gated
participation on `athlete_name` truthiness alone, so those rows -- and their
contribution to `athlete_count` -- were silently dropped.

The fix's participation predicate must distinguish two different-looking but
both-falsy values of `athlete_name`:
- `""` (empty string) is a pre-existing test-fixture convention (see
  test_race_results_query.py) for "not a real participant" -- production
  code never emits it (RegisterAthletePayload normalizes blank input to
  `None` before it ever reaches a leaderboard row).
- `None` plus a real `station_number` is a *genuine* anonymous participant
  who chose not to give a name.

So the gate is NOT plain `if not name`; it must treat `None`-with-a-station
as "participant" while `""`-with-a-station stays "not a participant". This
module pins that exact distinction, on top of the ordinary "mixed named and
anonymous rows both come back" cases the task asked for.
"""

from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery


def _row(
    node_id,
    athlete_name,
    station_number=1,
    distance_m=0,
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
        "calories": 0,
        "power_watts": 0,
        "max_power_watts": 0,
        "finished_time_ms": finished_time_ms,
    }


def _snapshot(rows, start_ms=1000, end_ms=2000, target_value=100):
    return {
        "state": "STOPPED",
        "config": {
            "race_type": "distance",
            "competition_mode": "individual",
            "team_scoring_policy": None,
            "target_value": target_value,
            "duration_sec": 0,
        },
        "start_time_epoch_ms": start_ms,
        "end_time_epoch_ms": end_ms,
        "leaderboard": rows,
        "team_leaderboard": None,
    }


def test_mixed_named_and_anonymous_rows_both_come_back_ranked_and_counted(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            {
                "node-01": _row(
                    "node-01",
                    "Alice",
                    station_number=1,
                    distance_m=100,
                    finished_time_ms=5000,
                ),
                "node-02": _row(
                    "node-02",
                    None,
                    station_number=2,
                    distance_m=80,
                    finished_time_ms=6000,
                ),
            }
        )
    )
    query = RaceResultsQuery(store)

    race = query.get_race("1000-2000-distance")

    assert race is not None
    assert race["athlete_count"] == 2
    names = [r["athlete_name"] for r in race["results"]]
    stations = [r["station_number"] for r in race["results"]]
    assert names == ["Alice", None]
    assert stations == [1, 2]


def test_snapshot_with_only_anonymous_rows_returns_them_not_nothing(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            {
                "node-01": _row(
                    "node-01",
                    None,
                    station_number=1,
                    distance_m=100,
                    finished_time_ms=5000,
                ),
                "node-02": _row(
                    "node-02",
                    None,
                    station_number=2,
                    distance_m=80,
                    finished_time_ms=6000,
                ),
            }
        )
    )
    query = RaceResultsQuery(store)

    race = query.get_race("1000-2000-distance")

    assert race is not None
    assert race["athlete_count"] == 2
    assert len(race["results"]) == 2
    assert [r["station_number"] for r in race["results"]] == [1, 2]


def test_genuinely_empty_or_non_dict_row_is_still_skipped(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            {
                "node-01": _row(
                    "node-01",
                    "Alice",
                    station_number=1,
                    distance_m=100,
                    finished_time_ms=5000,
                ),
                "node-02": {},
                "node-03": "not-a-dict",
                "node-04": None,
            }
        )
    )
    query = RaceResultsQuery(store)

    race = query.get_race("1000-2000-distance")

    assert race is not None
    assert race["athlete_count"] == 1
    assert len(race["results"]) == 1
    assert race["results"][0]["athlete_name"] == "Alice"


def test_empty_string_name_with_station_is_not_a_participant_but_none_name_is(
    tmp_path,
):
    """The distinction test: this is what stops someone from "simplifying"
    the predicate back to `if not name`. An empty-string name (the
    pre-existing test-fixture convention for "not a real participant") must
    stay excluded even though it carries a real station_number, while a
    None name with the same station_number -- the actual shape of a real
    anonymous registrant -- must be included."""
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            {
                "node-01": _row(
                    "node-01",
                    "",
                    station_number=1,
                    distance_m=100,
                    finished_time_ms=1000,
                ),
                "node-02": _row(
                    "node-02",
                    None,
                    station_number=2,
                    distance_m=80,
                    finished_time_ms=6000,
                ),
            }
        )
    )
    query = RaceResultsQuery(store)

    race = query.get_race("1000-2000-distance")

    assert race is not None
    assert race["athlete_count"] == 1
    assert len(race["results"]) == 1
    assert race["results"][0]["station_number"] == 2
    assert race["results"][0]["athlete_name"] is None
