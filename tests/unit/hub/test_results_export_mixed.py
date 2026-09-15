"""Mixed-race coverage for build_results_csv.

Non-mixed export behaviour is locked by test_results_export.py and must
stay untouched. This module only exercises the new group-aware branch: a
mixed race's row uses ITS OWN group's race_type/target for the race-type
label, category label ("Target / Duration"), and DNF status column -- not
the race's own (always "mixed") race_type. An ungrouped row (equipment
matched no group) falls back to the race_type.mixed label with an empty
category label and empty status, since there is no group race_type to
judge DNF against.
"""

import csv
import io

from hub_server.infrastructure.locales import load_locale
from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery
from hub_server.usecases.results_export import build_results_csv

COL_RACE_TYPE = 1
COL_TARGET = 2
COL_RANK = 4
COL_NAME = 5
COL_STATUS = 10
COL_DISTANCE_M = 11


def _row(node_id, athlete_name, group_index, distance_m=0, finished_time_ms=None):
    return {
        "node_id": node_id,
        "athlete_name": athlete_name,
        "station_number": 1,
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
        "group_index": group_index,
        "relay_members": [],
        "relay_splits": [],
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
                "node-tm-1", "Alice", 0, distance_m=500, finished_time_ms=60000
            ),
            "node-tm-2": _row("node-tm-2", "Bob", 0, distance_m=300),
            "node-row-1": _row("node-row-1", "Dan", 1, distance_m=900),
            "node-mystery": _row("node-mystery", "Erin", None, distance_m=10),
        },
        "team_leaderboard": None,
    }


def _translate_for(lang):
    messages = load_locale(lang)["messages"]
    return lambda key: messages.get(key, key)


def _format_local_datetime(epoch_ms):
    return f"local:{epoch_ms}"


def _parse_rows(csv_text):
    body = csv_text[1:]
    return list(csv.reader(io.StringIO(body)))


def _build(store, lang="en-US"):
    query = RaceResultsQuery(store)
    return build_results_csv(
        store,
        query,
        translate=_translate_for(lang),
        format_local_datetime=_format_local_datetime,
        lang=lang,
    )


def _build_store(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(_mixed_snapshot())
    return store


def test_mixed_row_uses_its_own_group_race_type_label(tmp_path):
    store = _build_store(tmp_path)
    rows = _parse_rows(_build(store))[1:]
    by_name = {r[COL_NAME]: r for r in rows}

    assert by_name["Alice"][COL_RACE_TYPE] == "Distance"
    assert by_name["Alice"][COL_TARGET] == "500 m"
    assert by_name["Dan"][COL_RACE_TYPE] == "Time"
    assert by_name["Dan"][COL_TARGET] == "2 min"


def test_mixed_row_dnf_status_judged_against_own_group_race_type(tmp_path):
    store = _build_store(tmp_path)
    rows = _parse_rows(_build(store, lang="zh-TW"))[1:]
    by_name = {r[COL_NAME]: r for r in rows}

    # Bob is in the distance group and never finished -> DNF.
    assert by_name["Bob"][COL_STATUS] == "未完成"
    # Dan is in the time-boxed group -- DNF status does not apply there at
    # all, finished_time_ms or not.
    assert by_name["Dan"][COL_STATUS] == ""


def test_ungrouped_row_falls_back_to_mixed_label_with_blank_category_and_status(
    tmp_path,
):
    store = _build_store(tmp_path)
    rows = _parse_rows(_build(store))[1:]
    by_name = {r[COL_NAME]: r for r in rows}

    erin = by_name["Erin"]
    assert erin[COL_RACE_TYPE] == "Multi-equipment"
    assert erin[COL_TARGET] == ""
    assert erin[COL_STATUS] == ""


def test_mixed_export_ranks_and_columns_match_the_grouped_query_ranking(tmp_path):
    store = _build_store(tmp_path)
    rows = _parse_rows(_build(store))[1:]
    by_name = {r[COL_NAME]: r for r in rows}

    assert by_name["Alice"][COL_RANK] == "1"
    assert by_name["Bob"][COL_RANK] == "2"
    assert by_name["Dan"][COL_RANK] == "1"
    assert by_name["Erin"][COL_RANK] == ""
    assert by_name["Dan"][COL_DISTANCE_M] == "900.0"
