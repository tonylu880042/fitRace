"""Tests for the "idle live telemetry" Layout A rendering on the dashboard
(`hub_server/static/index.html`), mirroring the node-extraction technique
already used in test_dashboard_class_segment_cue.py.

Covers:
  1. formatIdleMetricValue -- pure formatting helper.
  2. renderIdleTelemetry -- the wiring function the "idle_telemetry" WS
     handler calls: must hide during RUNNING/STOPPED/class mode, populate
     the grid + mini leaderboard when visible, and fall back to the
     existing idle record wall when there is nothing to show.
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


def _matching_brace_end(source: str, open_idx: int) -> int:
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
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching closing brace found")


def _extract_function(source: str, name: str) -> str:
    async_marker = f"async function {name}("
    marker = f"function {name}("
    if async_marker in source:
        start = source.index(async_marker)
    else:
        start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _extract_const(source: str, name: str) -> str:
    marker = f"const {name} = "
    start = source.index(marker)
    end = source.index(";\n", start)
    return source[start : end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 1. formatIdleMetricValue -- pure formatting helper.
# ---------------------------------------------------------------------------


def _run_format(value, unit, digits=0):
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "formatIdleMetricValue"))
    script = (
        f"{fn}\n"
        f"console.log(JSON.stringify(formatIdleMetricValue({json.dumps(value)}, "
        f"{json.dumps(unit)}, {json.dumps(digits)})));"
    )
    return json.loads(_run_node(script))


def test_format_idle_metric_value_renders_number_with_unit():
    assert _run_format(9.5, " km/h", 1) == "9.5 km/h"


def test_format_idle_metric_value_renders_dash_for_none():
    assert _run_format(None, " W", 0) == "--"


def test_format_idle_metric_value_renders_dash_for_non_finite():
    assert _run_format(float("nan"), "", 0) == "--"


# ---------------------------------------------------------------------------
# 2. renderIdleTelemetry -- wiring/visibility logic.
# ---------------------------------------------------------------------------


class _FakeElement:
    pass


def _run_render_idle_telemetry(
    current_state, current_session_mode, snapshot_js: str
) -> dict:
    source = _read_index()
    pieces = [
        _strip_js_comments(_extract_function(source, "hideIdleTelemetry")),
        _strip_js_comments(_extract_function(source, "formatIdleMetricValue")),
        _strip_js_comments(_extract_function(source, "idleParticipantLabel")),
        _strip_js_comments(_extract_function(source, "renderIdleStationCard")),
        _strip_js_comments(_extract_function(source, "renderIdleBestRow")),
        _strip_js_comments(_extract_function(source, "renderIdleTelemetry")),
        _strip_js_comments(_extract_const(source, "IDLE_BEST_METRIC_LABEL_KEYS")),
    ]
    script = (
        f"let currentState = {json.dumps(current_state)};\n"
        f"let currentSessionMode = {json.dumps(current_session_mode)};\n"
        "function t(key) { return key; }\n"
        "function escapeHtml(value) { return String(value); }\n"
        "let enterIdleRecordWallCalls = 0;\n"
        "let exitIdleRecordWallCalls = 0;\n"
        "function enterIdleRecordWall() { enterIdleRecordWallCalls += 1; }\n"
        "function exitIdleRecordWall() { exitIdleRecordWallCalls += 1; }\n"
        "class FakeClassList { constructor(el) { this.el = el; } "
        "add(cls) { this.el.classes.add(cls); } "
        "remove(cls) { this.el.classes.delete(cls); } }\n"
        "function makeElement() { "
        "const el = { innerHTML: '', style: { display: '' }, classes: new Set() }; "
        "el.classList = new FakeClassList(el); "
        "return el; }\n"
        "const elements = { "
        "'idle-stations-panel': makeElement(), "
        "'idle-stations-grid': makeElement(), "
        "'idle-best-panel': makeElement(), "
        "'leaderboard-container': makeElement() };\n"
        "const document = { getElementById: (id) => elements[id] || null };\n"
        + "\n".join(pieces)
        + "\n"
        + f"renderIdleTelemetry({snapshot_js});\n"
        + "console.log(JSON.stringify({\n"
        + "  panelShown: elements['idle-stations-panel'].classes.has('show'),\n"
        + "  gridHtml: elements['idle-stations-grid'].innerHTML,\n"
        + "  bestHtml: elements['idle-best-panel'].innerHTML,\n"
        + "  bestDisplay: elements['idle-best-panel'].style.display,\n"
        + "  leaderboardDisplay: elements['leaderboard-container'].style.display,\n"
        + "  enterIdleRecordWallCalls,\n"
        + "  exitIdleRecordWallCalls,\n"
        + "}));\n"
    )
    return json.loads(_run_node(script))


def test_render_idle_telemetry_hides_while_running():
    result = _run_render_idle_telemetry(
        "RUNNING",
        "race",
        '{"visible": true, "stations": [{"station_number": 1, '
        '"instantaneous_speed_kph": 9.0, "is_stale": false}], "best": []}',
    )
    assert result["panelShown"] is False
    assert result["leaderboardDisplay"] == ""


def test_render_idle_telemetry_hides_in_class_mode():
    result = _run_render_idle_telemetry(
        "IDLE",
        "class",
        '{"visible": true, "stations": [{"station_number": 1, '
        '"instantaneous_speed_kph": 9.0, "is_stale": false}], "best": []}',
    )
    assert result["panelShown"] is False


def test_render_idle_telemetry_shows_grid_when_visible_with_stations():
    result = _run_render_idle_telemetry(
        "IDLE",
        "race",
        '{"visible": true, "stations": [{"station_number": 1, '
        '"athlete_name": "Alice", "instantaneous_speed_kph": 9.5, '
        '"power_watts": 120, "cadence_rpm": 80, "heart_rate_bpm": 140, '
        '"is_stale": false}], "best": []}',
    )
    assert result["panelShown"] is True
    assert "Alice" in result["gridHtml"]
    assert "9.5" in result["gridHtml"]
    assert result["leaderboardDisplay"] == "none"
    # Idle telemetry takes over from the older idle record wall carousel.
    assert result["exitIdleRecordWallCalls"] == 1


def test_render_idle_telemetry_shows_waiting_label_for_stale_station():
    result = _run_render_idle_telemetry(
        "READY",
        "race",
        '{"visible": true, "stations": [{"station_number": 2, '
        '"athlete_name": null, "is_stale": true}], "best": []}',
    )
    assert "idle-station-waiting-label" in result["gridHtml"]
    assert "9.5" not in result["gridHtml"]


def test_render_idle_telemetry_renders_mini_leaderboard_rows():
    result = _run_render_idle_telemetry(
        "IDLE",
        "race",
        '{"visible": true, "stations": [{"station_number": 1, '
        '"instantaneous_speed_kph": 9.5, "is_stale": false}], '
        '"best": [{"metric": "instantaneous_speed_kph", "value": 20.0, '
        '"station_number": 3, "athlete_name": "Bob"}]}',
    )
    assert result["bestDisplay"] == ""
    assert "Bob" in result["bestHtml"]
    assert "20.0" in result["bestHtml"]


def test_render_idle_telemetry_falls_back_to_record_wall_when_no_stations():
    result = _run_render_idle_telemetry(
        "IDLE", "race", '{"visible": true, "stations": [], "best": []}'
    )
    assert result["panelShown"] is False
    assert result["enterIdleRecordWallCalls"] == 1


def test_render_idle_telemetry_falls_back_to_record_wall_when_not_visible():
    result = _run_render_idle_telemetry(
        "IDLE",
        "race",
        '{"visible": false, "stations": [{"station_number": 1, '
        '"instantaneous_speed_kph": 9.5, "is_stale": false}], "best": []}',
    )
    assert result["panelShown"] is False
    assert result["enterIdleRecordWallCalls"] == 1
