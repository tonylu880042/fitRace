import csv
import io

from hub_server.infrastructure.locales import load_locale
from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery
from hub_server.usecases.results_export import build_results_csv

# Column order produced by build_results_csv -- kept in one place so a test
# that only cares about one field doesn't have to hardcode the others' index.
COL_RACE_START = 0
COL_RACE_TYPE = 1
COL_TARGET = 2
COL_MODE = 3
COL_RANK = 4
COL_NAME = 5
COL_DIVISION = 6
COL_TEAM = 7
COL_STATION = 8
COL_TIME_SEC = 9
COL_STATUS = 10
COL_DISTANCE_M = 11
COL_CALORIES = 12
COL_MAX_POWER_W = 13
COL_RELAY_MEMBERS = 14
COL_RELAY_SPLITS = 15


def _row(
    node_id,
    athlete_name,
    station_number=1,
    distance_m=0.0,
    calories=0.0,
    max_power_watts=0,
    finished_time_ms=None,
    division=None,
    team_name=None,
    relay_members=None,
    relay_splits=None,
):
    return {
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
        "relay_members": relay_members or [],
        "relay_splits": relay_splits or [],
    }


def _snapshot(
    start_ms,
    end_ms,
    race_type="distance",
    target_value=100,
    duration_sec=0,
    competition_mode="individual",
    relay_legs=None,
    rows=None,
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


def _translate_for(lang):
    messages = load_locale(lang)["messages"]
    return lambda key: messages.get(key, key)


def _format_local_datetime(epoch_ms):
    # Deterministic, timezone-independent stand-in for the real hub-local
    # formatter injected in production -- tests only need to see the
    # injected callable get invoked with the right epoch value, not exercise
    # the system timezone.
    return f"local:{epoch_ms}"


def _parse_rows(csv_text):
    assert csv_text.startswith("﻿"), "CSV must start with a UTF-8 BOM"
    body = csv_text[1:]
    return list(csv.reader(io.StringIO(body)))


def _build(store, lang="zh-TW"):
    query = RaceResultsQuery(store)
    return build_results_csv(
        store,
        query,
        translate=_translate_for(lang),
        format_local_datetime=_format_local_datetime,
        lang=lang,
    )


def test_empty_store_returns_header_row_only(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")

    csv_text = _build(store)

    rows = _parse_rows(csv_text)
    assert len(rows) == 1
    assert rows[0][COL_RACE_START] != ""  # header cell is a real label


def test_bom_present_at_start_of_output(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    csv_text = _build(store)
    assert csv_text[0] == "﻿"


def test_header_localized_zh_tw(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    csv_text = _build(store, lang="zh-TW")
    rows = _parse_rows(csv_text)
    header = rows[0]
    assert header[COL_RACE_START] == "比賽開始"
    assert header[COL_RANK] == "名次"
    assert header[COL_NAME] == "姓名"
    assert header[COL_RELAY_SPLITS] == "接力分段時間（秒）"


def test_header_localized_en_us(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    csv_text = _build(store, lang="en-US")
    rows = _parse_rows(csv_text)
    header = rows[0]
    assert header[COL_RACE_START] == "Race Start"
    assert header[COL_RANK] == "Rank"
    assert header[COL_NAME] == "Name"
    assert header[COL_RELAY_SPLITS] == "Relay Splits (s)"


def test_ordering_by_race_start_ascending_then_rank(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    # Saved out of order and with more than one participant per race, so a
    # naive "file order" or "insertion order" pass would get this wrong.
    store.save_finished_snapshot(
        _snapshot(
            5000,
            6000,
            rows={
                "node-01": _row(
                    "node-01", "Alice", station_number=1, finished_time_ms=29608
                ),
                "node-02": _row(
                    "node-02", "Bob", station_number=2, finished_time_ms=40000
                ),
            },
        )
    )
    store.save_finished_snapshot(
        _snapshot(
            1000,
            2000,
            rows={
                "node-03": _row(
                    "node-03", "Zed", station_number=3, finished_time_ms=10000
                ),
            },
        )
    )
    csv_text = _build(store)
    rows = _parse_rows(csv_text)[1:]

    names = [r[COL_NAME] for r in rows]
    assert names == ["Zed", "Alice", "Bob"]
    ranks = [r[COL_RANK] for r in rows]
    assert ranks == ["1", "1", "2"]


def test_finisher_time_formatted_with_three_decimals(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1000,
            2000,
            rows={
                "node-01": _row(
                    "node-01", "Alice", station_number=1, finished_time_ms=29608
                ),
            },
        )
    )
    rows = _parse_rows(_build(store))[1:]
    assert rows[0][COL_TIME_SEC] == "29.608"
    assert rows[0][COL_STATUS] == ""


def test_dnf_row_has_blank_time_and_localized_status(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1000,
            2000,
            race_type="distance",
            rows={
                "node-01": _row(
                    "node-01",
                    "Bob",
                    station_number=1,
                    distance_m=50,
                    finished_time_ms=None,
                ),
            },
        )
    )
    rows = _parse_rows(_build(store, lang="zh-TW"))[1:]
    assert rows[0][COL_TIME_SEC] == ""
    assert rows[0][COL_STATUS] == "未完成"


def test_relay_row_members_and_splits_joined(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1000,
            2000,
            competition_mode="relay",
            relay_legs=2,
            rows={
                "node-01": _row(
                    "node-01",
                    "隊伍A",
                    station_number=1,
                    finished_time_ms=29608,
                    relay_members=["王小明", "陳大文", "林小華"],
                    relay_splits=[14976, 29608],
                ),
            },
        )
    )
    rows = _parse_rows(_build(store))[1:]
    assert rows[0][COL_RELAY_MEMBERS] == "王小明 → 陳大文 → 林小華"
    assert rows[0][COL_RELAY_SPLITS] == "14.976 / 29.608"


def test_division_localized(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1000,
            2000,
            rows={
                "node-01": _row(
                    "node-01",
                    "Alice",
                    station_number=1,
                    finished_time_ms=1000,
                    division="women",
                ),
                "node-02": _row(
                    "node-02",
                    "Bob",
                    station_number=2,
                    finished_time_ms=2000,
                    division="men",
                ),
                "node-03": _row(
                    "node-03",
                    "Cara",
                    station_number=3,
                    finished_time_ms=3000,
                    division=None,
                ),
            },
        )
    )
    rows = _parse_rows(_build(store, lang="zh-TW"))[1:]
    by_name = {r[COL_NAME]: r for r in rows}
    assert by_name["Alice"][COL_DIVISION] == "女子組"
    assert by_name["Bob"][COL_DIVISION] == "男子組"
    assert by_name["Cara"][COL_DIVISION] == ""


def test_formula_injection_cells_prefixed_numeric_columns_are_not(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    store.save_finished_snapshot(
        _snapshot(
            1000,
            2000,
            competition_mode="relay",
            relay_legs=2,
            rows={
                "node-01": _row(
                    "node-01",
                    "=SUM(A1)",
                    station_number=9,
                    distance_m=12.34,
                    finished_time_ms=5000,
                    team_name="+1",
                    relay_members=["-2", "@x"],
                    relay_splits=[1000, 2000],
                ),
            },
        )
    )
    rows = _parse_rows(_build(store))[1:]
    row = rows[0]
    assert row[COL_NAME] == "'=SUM(A1)"
    assert row[COL_TEAM] == "'+1"
    assert row[COL_RELAY_MEMBERS] == "'-2 → '@x"
    # Numbers we generate ourselves are never prefixed, even in the same row.
    assert row[COL_RANK] == "1"
    assert row[COL_DISTANCE_M] == "12.3"
    assert row[COL_TIME_SEC] == "5.000"
