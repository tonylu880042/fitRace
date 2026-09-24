"""Two heats each with an unassigned station fall back to the placeholder
name "Station 1"/"Station 2" (see RaceManager._default_participant_name).
RaceResultsQuery._dedupe_best_row (get_standings) and _top_three
(get_records) must never merge those placeholder rows across heats just
because they share the literal string "Station 1" -- that collapsed two
different people down to one row and silently dropped the other's finish.

The robust signal is `is_registered_name` on the stored leaderboard row
(set by RaceManager.update_telemetry: True only when the athlete_name came
from an explicit registration, False when it's the station-number
fallback). A row missing that key entirely (older stored data, from before
this field existed) falls back to the previous "startswith('Station ')"
heuristic so historical data doesn't regress.

Two rows that DO share a genuine, registered athlete name must still
dedupe to that athlete's best row, exactly as before.
"""

from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery


def _row(
    node_id,
    athlete_name,
    is_registered_name,
    station_number=1,
    finished_time_ms=None,
    distance_m=0,
):
    row = {
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
        "calories": 0,
        "power_watts": 0,
        "max_power_watts": 0,
        "finished_time_ms": finished_time_ms,
    }
    if is_registered_name is not None:
        row["is_registered_name"] = is_registered_name
    return row


def _snapshot(start_ms, end_ms, rows, target_value=150):
    return {
        "state": "STOPPED",
        "config": {
            "race_type": "distance",
            "competition_mode": "individual",
            "team_scoring_policy": None,
            "target_value": target_value,
            "duration_sec": 0,
            "relay_legs": None,
        },
        "start_time_epoch_ms": start_ms,
        "end_time_epoch_ms": end_ms,
        "leaderboard": rows,
        "team_leaderboard": None,
    }


def test_two_heats_of_station_placeholder_names_stay_two_rows(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1_000,
            2_000,
            {
                "node-01": _row(
                    "node-01",
                    "Station 1",
                    is_registered_name=False,
                    finished_time_ms=1300,
                )
            },
        )
    )
    store.save_finished_snapshot(
        _snapshot(
            3_000,
            4_000,
            {
                "node-01": _row(
                    "node-01",
                    "Station 1",
                    is_registered_name=False,
                    finished_time_ms=1500,
                )
            },
        )
    )
    query = RaceResultsQuery(store)

    standings = query.get_standings()

    rows = standings["sections"][0]["rows"]
    assert len(rows) == 2
    assert all(r["athlete_name"] == "Station 1" for r in rows)


def test_two_rows_of_a_genuine_registered_name_still_dedupe(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1_000,
            2_000,
            {
                "node-01": _row(
                    "node-01", "Amy", is_registered_name=True, finished_time_ms=1300
                )
            },
        )
    )
    store.save_finished_snapshot(
        _snapshot(
            3_000,
            4_000,
            {
                "node-01": _row(
                    "node-01", "Amy", is_registered_name=True, finished_time_ms=900
                )
            },
        )
    )
    query = RaceResultsQuery(store)

    standings = query.get_standings()

    rows = standings["sections"][0]["rows"]
    assert len(rows) == 1
    assert rows[0]["athlete_name"] == "Amy"
    assert rows[0]["value"] == 900  # best (fastest) row kept


def test_legacy_rows_without_the_flag_fall_back_to_string_heuristic(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1_000,
            2_000,
            {
                "node-01": _row(
                    "node-01",
                    "Station 3",
                    is_registered_name=None,
                    finished_time_ms=1300,
                )
            },
        )
    )
    store.save_finished_snapshot(
        _snapshot(
            3_000,
            4_000,
            {
                "node-01": _row(
                    "node-01",
                    "Station 3",
                    is_registered_name=None,
                    finished_time_ms=1500,
                )
            },
        )
    )
    query = RaceResultsQuery(store)

    standings = query.get_standings()

    rows = standings["sections"][0]["rows"]
    # Legacy data (no is_registered_name key at all) has no robust signal,
    # so falls back to the "Station " prefix heuristic -- never merges.
    assert len(rows) == 2


def test_get_records_applies_the_same_placeholder_dedupe(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1_000,
            2_000,
            {
                "node-01": _row(
                    "node-01",
                    "Station 1",
                    is_registered_name=False,
                    finished_time_ms=1300,
                )
            },
        )
    )
    store.save_finished_snapshot(
        _snapshot(
            3_000,
            4_000,
            {
                "node-01": _row(
                    "node-01",
                    "Station 1",
                    is_registered_name=False,
                    finished_time_ms=1500,
                )
            },
        )
    )
    query = RaceResultsQuery(store)

    records = query.get_records()["records"]
    assert len(records) == 1
    assert len(records[0]["entries"]) == 2
