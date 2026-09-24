"""GET /api/results/standings must not let an old rehearsal race bleed into
today's overall standings just because it shares the same (race_type,
label, relay_legs) scope. RaceResultsQuery.get_standings() gains an
optional `event_start_epoch_ms` argument -- when given, only races whose
snapshot start_time_epoch_ms is >= that boundary are considered at all
(both for picking the "latest race" scope and for the rows that
contribute). None (the default -- no boundary set yet) preserves the
current, unscoped behaviour so existing installs are unaffected.
"""

from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery


def _row(node_id, athlete_name, distance_m=0, finished_time_ms=None, station_number=1):
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
        "calories": 0,
        "power_watts": 0,
        "max_power_watts": 0,
        "finished_time_ms": finished_time_ms,
    }


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


def test_race_before_boundary_is_excluded_race_after_is_included(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    # Rehearsal race two days ago -- before the boundary.
    store.save_finished_snapshot(
        _snapshot(
            1_000,
            2_000,
            {"node-01": _row("node-01", "陳大文", finished_time_ms=1300)},
        )
    )
    boundary_ms = 5_000
    # Today's heat -- at/after the boundary.
    store.save_finished_snapshot(
        _snapshot(
            6_000,
            7_000,
            {"node-01": _row("node-01", "王小明", finished_time_ms=2000)},
        )
    )
    query = RaceResultsQuery(store)

    standings = query.get_standings(event_start_epoch_ms=boundary_ms)

    names = [
        row["athlete_name"]
        for section in standings["sections"]
        for row in section["rows"]
    ]
    assert names == ["王小明"]
    assert "陳大文" not in names
    assert standings["race_count"] == 1


def test_no_boundary_set_keeps_full_history(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1_000,
            2_000,
            {"node-01": _row("node-01", "陳大文", finished_time_ms=1300)},
        )
    )
    store.save_finished_snapshot(
        _snapshot(
            6_000,
            7_000,
            {"node-01": _row("node-01", "王小明", finished_time_ms=2000)},
        )
    )
    query = RaceResultsQuery(store)

    standings = query.get_standings()

    names = [
        row["athlete_name"]
        for section in standings["sections"]
        for row in section["rows"]
    ]
    assert set(names) == {"陳大文", "王小明"}
    assert standings["race_count"] == 2


def test_all_races_before_boundary_yields_empty_standings(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1_000,
            2_000,
            {"node-01": _row("node-01", "陳大文", finished_time_ms=1300)},
        )
    )
    query = RaceResultsQuery(store)

    standings = query.get_standings(event_start_epoch_ms=5_000)

    assert standings["sections"] == []
    assert standings["race_count"] == 0
