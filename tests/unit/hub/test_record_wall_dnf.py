"""The record wall's "Latest race" slide (hub_server/static/index.html,
buildRecordWallSlides) must render a distance/calories row that never
crossed the finish line (finished_time_ms is null) as a translated DNF
label plus its raw progress in the right unit -- never as a bogus time
produced by dividing the leftover progress metric by 1000 and appending
"s" (the formatting meant for a real finish time in milliseconds).

This executes the REAL, unmodified functions pulled out of index.html's
inline <script> via brace-depth matching (the same technique as
test_record_wall_division.py) under node -- never a source-text grep --
so deleting the real DNF wiring, or leaving a comment that merely mentions
"DNF", turns this red instead of being satisfied by a nearby comment.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _matching_bracket_end(
    source: str, open_idx: int, open_ch: str, close_ch: str
) -> int:
    depth = 0
    i = open_idx
    in_str = None
    while i < len(source):
        char = source[i]
        if in_str:
            if char == "\\":
                i += 2
                continue
            if char == in_str:
                in_str = None
        elif char in ('"', "'", "`"):
            in_str = char
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching close bracket found")


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_bracket_end(source, brace_open, "{", "}")
    return source[start : brace_end + 1]


def _t_stub() -> str:
    return (
        "const t = (key) => ({"
        '"record_wall.title": "Record Wall",'
        '"record_wall.latest_race": "Latest Race",'
        '"record_wall.type_distance": "Distance",'
        '"record_wall.type_calories": "Calories",'
        '"record_wall.dnf": "DNF",'
        '"stations.station": "Station",'
        '"stations.athlete": "Athlete"'
        "}[key] || key);\n"
    )


def _metric_number_stub() -> str:
    return (
        "function metricNumber(value, fallback) {\n"
        "  const fb = fallback === undefined ? 0 : fallback;\n"
        "  const n = Number(value);\n"
        "  return Number.isFinite(n) ? n : fb;\n"
        "}\n"
    )


def _escape_html_stub() -> str:
    return (
        "function escapeHtml(value) {\n"
        "  return String(value === null || value === undefined ? '' : value)\n"
        "    .replace(/&/g, '&amp;')\n"
        "    .replace(/</g, '&lt;')\n"
        "    .replace(/>/g, '&gt;');\n"
        "}\n"
    )


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def _record_wall_fns(source: str) -> str:
    return "\n".join(
        _extract_function(source, name)
        for name in (
            "recordWallTypeLabel",
            "recordWallDivisionLabel",
            "formatRecordEntryValue",
            "formatRecordProgressValue",
            "formatRecordWallEntryValue",
            "renderRecordWallRows",
            "buildLatestRaceEntry",
            "buildRecordWallSlides",
        )
    )


def _build_slides(latest_race_js: str) -> list:
    source = _strip_js_comments(_read_index())
    fns = _record_wall_fns(source)
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + fns
        + "\n"
        + f"console.log(JSON.stringify(buildRecordWallSlides([], {latest_race_js})));"
    )
    output = _run_node(script)
    return json.loads(output)


def _build_slides_from_records(records_js: str) -> list:
    """Feed buildRecordWallSlides the shape GET /api/results/records
    actually returns: record.entries built by race_results_query.py's
    _top_three(), which are always finishers and carry no `finished` key
    at all (unlike the latest-race topThree entries, which get a real
    `finished` boolean from buildLatestRaceEntry)."""
    source = _strip_js_comments(_read_index())
    fns = _record_wall_fns(source)
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + fns
        + "\n"
        + f"console.log(JSON.stringify(buildRecordWallSlides({records_js}, null)));"
    )
    output = _run_node(script)
    return json.loads(output)


def test_unfinished_distance_row_shows_dnf_and_progress_in_meters():
    latest_race = (
        "{race_type: 'distance', results: ["
        "{athlete_name: 'Alex', team_name: 'Team 1', station_number: 1,"
        " finished_time_ms: null, distance_m: 243, calories: 999,"
        " max_power_watts: 999}"
        "]}"
    )
    slides = _build_slides(latest_race)
    assert "DNF" in slides[0]
    assert "243 m" in slides[0]
    assert "0.2s" not in slides[0]


def test_unfinished_calories_row_shows_dnf_and_progress_in_kcal():
    latest_race = (
        "{race_type: 'calories', results: ["
        "{athlete_name: 'Alex', team_name: 'Team 1', station_number: 1,"
        " finished_time_ms: null, distance_m: 999, calories: 42,"
        " max_power_watts: 999}"
        "]}"
    )
    slides = _build_slides(latest_race)
    assert "DNF" in slides[0]
    assert "42 kcal" in slides[0]


def test_finished_distance_row_still_shows_time_not_dnf():
    latest_race = (
        "{race_type: 'distance', results: ["
        "{athlete_name: 'Alex', team_name: 'Team 1', station_number: 1,"
        " finished_time_ms: 29600, distance_m: 300, calories: 50,"
        " max_power_watts: 999}"
        "]}"
    )
    slides = _build_slides(latest_race)
    assert "29.6s" in slides[0]
    assert "DNF" not in slides[0]


def test_finishers_still_rank_ahead_of_unfinished_rows():
    latest_race = (
        "{race_type: 'distance', results: ["
        "{athlete_name: 'Finisher', team_name: 'Team 1', station_number: 1,"
        " finished_time_ms: 29600, distance_m: 300, calories: 50,"
        " max_power_watts: 999},"
        "{athlete_name: 'NonFinisher', team_name: 'Team 2', station_number: 2,"
        " finished_time_ms: null, distance_m: 243, calories: 40,"
        " max_power_watts: 999}"
        "]}"
    )
    slides = _build_slides(latest_race)
    slide = slides[0]
    assert slide.index("Finisher") < slide.index("NonFinisher")
    assert "29.6s" in slide
    assert "DNF" in slide
    assert "243 m" in slide


def test_time_boxed_race_type_is_unaffected_by_dnf_logic():
    latest_race = (
        "{race_type: 'time', results: ["
        "{athlete_name: 'Alex', team_name: 'Team 1', station_number: 1,"
        " finished_time_ms: null, distance_m: 812, calories: 50,"
        " max_power_watts: 999}"
        "]}"
    )
    slides = _build_slides(latest_race)
    assert "812m" in slides[0]
    assert "DNF" not in slides[0]


def test_alltime_distance_record_entry_with_no_finished_field_renders_as_time():
    """GET /api/results/records entries (race_results_query.py's
    _top_three()) are always finishers -- the value is their finish
    time -- and the dict it returns never has a `finished` key at all
    (unlike a latest-race topThree entry, which buildLatestRaceEntry
    always gives a real boolean `finished`). formatRecordWallEntryValue
    must only take the DNF branch when `finished` is the *boolean*
    false, not merely falsy/undefined -- otherwise every all-time
    record on the venue screen (e.g. "#1 王小明 30.2s" on a
    distance/calories slide) would flip to showing DNF plus a bogus
    "raw value in meters" reading instead of the real finish time."""
    records = (
        "[{race_type: 'distance', label: '500 m', division: 'men', entries: ["
        "{athlete_name: '王小明', team_name: null, value: 30247}"
        "]}]"
    )
    slides = _build_slides_from_records(records)
    assert "30.2s" in slides[0]
    assert "DNF" not in slides[0]


def test_alltime_calories_record_entry_with_no_finished_field_renders_as_time():
    records = (
        "[{race_type: 'calories', label: '50 kcal', division: null, entries: ["
        "{athlete_name: 'Jamie', team_name: null, value: 42500}"
        "]}]"
    )
    slides = _build_slides_from_records(records)
    assert "42.5s" in slides[0]
    assert "DNF" not in slides[0]
