"""Simplified Chinese (zh-CN) on the backend paths that used to branch on
zh-TW only: the results CSV export, the roster CSV template and the roster
CSV importer (see docs/zh_cn_i18n_spec.md section 3)."""

import csv
import io

import pytest
from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi import app as app_module
from hub_server.infrastructure.locales import load_locale
from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery
from hub_server.usecases.results_export import _display_name, build_results_csv
from hub_server.usecases.roster import (
    build_import_preview,
    build_roster_template_csv,
    parse_roster_csv,
)

client = TestClient(app_module.app)

COL_RANK = 4
COL_NAME = 5
COL_DIVISION = 6
COL_TEAM = 7
COL_STATUS = 10
COL_RELAY_SPLITS = 15


def _first_line(response) -> str:
    return response.content.decode("utf-8").lstrip("﻿").splitlines()[0]


# ---------------------------------------------------------------------------
# Results export
# ---------------------------------------------------------------------------


def _row(node_id, athlete_name, station_number):
    return {
        "node_id": node_id,
        "athlete_name": athlete_name,
        "station_number": station_number,
        "team_name": None,
        "division": None,
        "avatar_url": None,
        "distance_m": 50.0,
        "elapsed_time_ms": 60000,
        "instantaneous_speed_kph": 0.0,
        "progress_percent": 50.0,
        "calories": 0.0,
        "power_watts": 0,
        "max_power_watts": 0,
        "finished_time_ms": None,
        "relay_members": [],
        "relay_splits": [],
    }


def _snapshot(rows):
    return {
        "state": "STOPPED",
        "config": {
            "race_type": "distance",
            "competition_mode": "individual",
            "team_scoring_policy": None,
            "target_value": 100,
            "duration_sec": 0,
            "relay_legs": None,
        },
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": rows,
        "team_leaderboard": None,
    }


def _export_rows(tmp_path, lang, rows=None):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    if rows:
        store.save_finished_snapshot(_snapshot(rows))
    messages = load_locale(lang)["messages"]
    text = build_results_csv(
        store,
        RaceResultsQuery(store),
        translate=lambda key: messages.get(key, key),
        format_local_datetime=lambda epoch_ms: f"local:{epoch_ms}",
        lang=lang,
    )
    assert text.startswith("﻿")
    return list(csv.reader(io.StringIO(text[1:])))


def test_export_header_is_simplified_for_zh_cn(tmp_path):
    header = _export_rows(tmp_path, "zh-CN")[0]
    assert header[COL_RANK] == "名次"
    assert header[COL_DIVISION] == "组别"
    assert header[COL_TEAM] == "队伍"
    assert header[COL_RELAY_SPLITS] == "接力分段时间（秒）"


def test_export_dnf_status_and_anonymous_names_are_simplified_for_zh_cn(tmp_path):
    rows = _export_rows(tmp_path, "zh-CN", {"node-01": _row("node-01", None, 3)})[1:]
    assert [row[COL_NAME] for row in rows] == ["站位 3"]
    assert [row[COL_STATUS] for row in rows] == ["未完成"]


def test_export_generic_athlete_word_is_simplified_for_zh_cn():
    # An export row without a station is not a participant, so this fallback
    # is only reachable through the helper itself.
    assert _display_name({"athlete_name": None}, "zh-CN") == "选手"
    assert _display_name({"athlete_name": None}, "zh-TW") == "選手"


def test_export_endpoint_picks_the_requested_chinese_script(monkeypatch, tmp_path):
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    store = RaceResultStore(tmp_path / "empty_race_results.jsonl")
    monkeypatch.setattr(app_module, "race_result_store", store)
    monkeypatch.setattr(app_module, "race_results_query", RaceResultsQuery(store))

    simplified = client.get("/api/results/export.csv?lang=zh-CN").text
    traditional = client.get("/api/results/export.csv?lang=zh-TW").text
    english = client.get("/api/results/export.csv?lang=fr").text

    assert "比赛开始" in simplified
    assert "比賽開始" in traditional
    assert "Race Start" in english


# ---------------------------------------------------------------------------
# Roster template
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["individual", "relay"])
def test_zh_cn_template_round_trips_with_no_errors(mode):
    entries, errors = parse_roster_csv(
        build_roster_template_csv(mode=mode, lang="zh-CN", legs=2)
    )
    assert errors == []
    assert entries


def test_zh_cn_individual_template_is_simplified():
    lines = (
        build_roster_template_csv(mode="individual", lang="zh-CN")
        .lstrip("﻿")
        .splitlines()
    )
    assert lines[0] == "姓名,组别,队伍"
    assert lines[1] == "王小明,男,"
    assert lines[2] == "林小华,女,"
    assert lines[3] == "陈大同,,熊队"


def test_zh_cn_relay_template_uses_simplified_team_names():
    entries, _ = parse_roster_csv(
        build_roster_template_csv(mode="relay", lang="zh-CN", legs=2)
    )
    assert {entry["team"] for entry in entries} == {"熊队", "虎队"}


def test_template_endpoint_zh_cn_is_simplified_and_round_trips():
    res = client.get("/api/roster/template.csv?mode=individual&lang=zh-CN")
    assert res.status_code == 200
    assert _first_line(res) == "姓名,组别,队伍"

    parsed, errors = parse_roster_csv(res.content.decode("utf-8"))
    assert errors == []
    assert {entry["team"] for entry in parsed if entry["team"]} == {"熊队"}


def test_template_endpoint_accepts_zh_hans_alias():
    res = client.get("/api/roster/template.csv?mode=individual&lang=zh-Hans")
    assert _first_line(res) == "姓名,组别,队伍"


def test_template_endpoint_keeps_zh_tw_and_english():
    zh = client.get("/api/roster/template.csv?mode=individual&lang=zh-TW")
    en = client.get("/api/roster/template.csv?mode=individual&lang=en")
    assert _first_line(zh) == "姓名,組別,隊伍"
    assert _first_line(en) == "name,division,team"


# ---------------------------------------------------------------------------
# Roster import
# ---------------------------------------------------------------------------


def test_parse_csv_accepts_simplified_headers_and_division_values():
    entries, errors = parse_roster_csv(
        "姓名,组别,队伍\n小明,男子组,熊队\n小华,女子组,虎队\n大同,男,\n小美,女,\n"
    )
    assert errors == []
    assert [(e["name"], e["division"], e["team"]) for e in entries] == [
        ("小明", "men", "熊队"),
        ("小华", "women", "虎队"),
        ("大同", "men", None),
        ("小美", "women", None),
    ]


def test_parse_csv_accepts_simplified_team_name_and_gender_headers():
    entries, errors = parse_roster_csv("姓名,性别,队名\n小明,男子组,熊队\n")
    assert errors == []
    assert entries[0]["division"] == "men"
    assert entries[0]["team"] == "熊队"


def test_import_preview_accepts_simplified_csv():
    preview = build_import_preview(
        "姓名,组别,队伍\n小华,女子组,虎队\n", existing_count=0
    )
    assert preview["errors"] == []
    assert preview["rows"][0]["division"] == "women"
    assert preview["rows"][0]["team"] == "虎队"
