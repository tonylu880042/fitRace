"""Regression coverage for results older than the query read window."""

import csv
import io
import json

from hub_server.infrastructure.locales import load_locale
from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery
from hub_server.usecases.results_export import build_results_csv


def _record(index: int) -> dict:
    finished_time_ms = 1 if index == 0 else 1000 + index
    return {
        "result_id": f"race-{index:03d}",
        "saved_epoch_ms": index,
        "snapshot": {
            "state": "STOPPED",
            "config": {
                "race_type": "distance",
                "competition_mode": "individual",
                "target_value": 100,
                "duration_sec": 0,
                "relay_legs": None,
            },
            "start_time_epoch_ms": 1000 + index,
            "end_time_epoch_ms": 2000 + index,
            "leaderboard": {
                "node-01": {
                    "athlete_name": f"Athlete {index}",
                    "station_number": 1,
                    "division": None,
                    "team_name": None,
                    "distance_m": 100,
                    "calories": 10,
                    "max_power_watts": 0,
                    "finished_time_ms": finished_time_ms,
                    "relay_members": [],
                    "relay_splits": [],
                }
            },
            "team_leaderboard": None,
        },
    }


def _write_history(path, count=501):
    path.write_text(
        "".join(
            json.dumps(_record(index), ensure_ascii=False) + "\n"
            for index in range(count)
        ),
        encoding="utf-8",
    )


def _translate_for(lang):
    messages = load_locale(lang)["messages"]
    return lambda key: messages.get(key, key)


def test_list_results_supports_unbounded_reads_without_changing_tail_defaults(tmp_path):
    path = tmp_path / "race_results.jsonl"
    _write_history(path)
    store = RaceResultStore(path)

    assert len(store.list_results()) == 50
    assert store.list_results(limit=1)[0]["result_id"] == "race-500"
    assert len(store.list_results(limit=None)) == 501
    assert store.list_results(limit=None)[0]["result_id"] == "race-000"


def test_query_and_csv_keep_the_first_race_after_500_records(tmp_path):
    path = tmp_path / "race_results.jsonl"
    _write_history(path)
    store = RaceResultStore(path)
    query = RaceResultsQuery(store)

    race = query.get_race("race-000")
    assert race is not None
    assert race["results"][0]["athlete_name"] == "Athlete 0"

    token = race["results"][0]["token"]
    athlete_result = query.get_athlete_result(token)
    assert athlete_result is not None
    assert athlete_result["athlete"]["athlete_name"] == "Athlete 0"

    records = query.get_records()["records"]
    distance_record = next(record for record in records if record["label"] == "100 m")
    assert distance_record["entries"][0]["athlete_name"] == "Athlete 0"

    standings = query.get_standings()
    assert standings["race_count"] == 501
    assert len(standings["sections"][0]["rows"]) == 501
    assert standings["sections"][0]["rows"][0]["athlete_name"] == "Athlete 0"

    csv_text = build_results_csv(
        store,
        query,
        translate=_translate_for("en-US"),
        format_local_datetime=lambda epoch_ms: f"local:{epoch_ms}",
        lang="en-US",
    )
    rows = list(csv.reader(io.StringIO(csv_text[1:])))
    exported_names = [row[5] for row in rows[1:]]
    assert len(exported_names) == 501
    assert exported_names[0] == "Athlete 0"
    assert rows[1][2] == "100 m"
