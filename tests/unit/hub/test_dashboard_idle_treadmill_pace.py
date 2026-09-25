"""Requirement B: treadmill pace in the idle live telemetry view
(`hub_server/static/index.html`).

For equipment_type TREADMILL (and curved_treadmill, matching the existing
isRunningEquipment grouping used elsewhere on this dashboard), the idle
station card's primary, largest number is pace in min/km ("m:ss /km"),
derived from instantaneous_speed_kph (pace_sec_per_km = 3600 / kph).
Speed below ~1 km/h or 0 shows "--" (no 60:00+ nonsense). The treadmill
card shows pace (primary), speed, cadence, heart rate, and drops the power
tile entirely. Every other equipment type (bike, fan bike, ski erg, rower,
...) keeps speed + power (+ cadence + heart rate) -- no pace at all.

The pace computation happens in exactly one place: the pure JS formatter
formatTreadmillPace, tested here with the product owner's own edge cases
(12 kph -> 5:00, 10.5 kph -> 5:43, 0 -> "--").
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
# 1. formatTreadmillPace -- the single pace formatter, product-owner edge
#    cases.
# ---------------------------------------------------------------------------


def _run_format_pace(speed_kph):
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "formatTreadmillPace"))
    script = (
        f"{fn}\n"
        f"console.log(JSON.stringify(formatTreadmillPace({json.dumps(speed_kph)})));"
    )
    return json.loads(_run_node(script))


def test_pace_at_12_kph_is_5_00():
    assert _run_format_pace(12) == "5:00 /km"


def test_pace_at_10_5_kph_is_5_43():
    assert _run_format_pace(10.5) == "5:43 /km"


def test_pace_at_zero_is_dashes():
    assert _run_format_pace(0) == "--"


def test_pace_below_one_kph_is_dashes():
    assert _run_format_pace(0.9) == "--"


def test_pace_at_exactly_one_kph_is_not_dashes():
    assert _run_format_pace(1) != "--"


def test_pace_for_non_finite_speed_is_dashes():
    assert _run_format_pace(None) == "--"


# ---------------------------------------------------------------------------
# 2. renderIdleStationCard -- treadmill vs. every other equipment type.
# ---------------------------------------------------------------------------


def _run_render_station_card(station_js: str) -> str:
    source = _read_index()
    pieces = [
        _strip_js_comments(_extract_function(source, "isRunningEquipment")),
        _strip_js_comments(_extract_function(source, "formatTreadmillPace")),
        _strip_js_comments(_extract_function(source, "formatIdleMetricValue")),
        _strip_js_comments(_extract_function(source, "idleParticipantLabel")),
        _strip_js_comments(_extract_function(source, "renderIdleStationCard")),
    ]
    script = (
        "function t(key) { return key; }\n"
        "function escapeHtml(value) { return String(value); }\n"
        + "\n".join(pieces)
        + "\n"
        + f"console.log(JSON.stringify(renderIdleStationCard({station_js})));"
    )
    return json.loads(_run_node(script))


def test_treadmill_card_shows_pace_as_primary_and_no_power_tile():
    html = _run_render_station_card(
        '{"station_number": 1, "equipment_type": "treadmill", '
        '"instantaneous_speed_kph": 12.0, "power_watts": 999, '
        '"cadence_rpm": 160, "heart_rate_bpm": 140, "is_stale": false}'
    )
    assert "idle-metric-value-primary" in html
    assert "5:00 /km" in html
    assert "12.0" in html  # secondary speed tile
    assert "160" in html
    assert "140" in html
    # Power is never shown for a treadmill, even though the payload
    # carries a (meaningless) power_watts value.
    assert "999" not in html
    assert "idle.metric.power" not in html


def test_curved_treadmill_card_also_shows_pace_not_power():
    html = _run_render_station_card(
        '{"station_number": 1, "equipment_type": "curved_treadmill", '
        '"instantaneous_speed_kph": 12.0, "power_watts": 300, '
        '"cadence_rpm": 160, "heart_rate_bpm": 140, "is_stale": false}'
    )
    assert "5:00 /km" in html
    assert "300" not in html


def test_treadmill_card_shows_dashes_when_stopped():
    html = _run_render_station_card(
        '{"station_number": 1, "equipment_type": "treadmill", '
        '"instantaneous_speed_kph": 0.0, "power_watts": 0, '
        '"cadence_rpm": 0, "heart_rate_bpm": 0, "is_stale": false}'
    )
    assert ">--<" in html


def test_rower_card_shows_speed_and_power_no_pace():
    html = _run_render_station_card(
        '{"station_number": 2, "equipment_type": "rower", '
        '"instantaneous_speed_kph": 12.0, "power_watts": 180, '
        '"cadence_rpm": 28, "heart_rate_bpm": 150, "is_stale": false}'
    )
    assert "idle-metric-value-primary" not in html
    assert "12.0" in html
    assert "180" in html
    assert "/km" not in html
    assert "/500m" not in html


def test_fan_bike_card_shows_speed_and_power_no_pace():
    html = _run_render_station_card(
        '{"station_number": 3, "equipment_type": "fan_bike", '
        '"instantaneous_speed_kph": 20.0, "power_watts": 250, '
        '"cadence_rpm": 90, "heart_rate_bpm": 130, "is_stale": false}'
    )
    assert "20.0" in html
    assert "250" in html
    assert "/km" not in html


# ---------------------------------------------------------------------------
# 3. renderIdleBestRow -- the treadmill-only "fastest pace" mini-leaderboard
#    row.
# ---------------------------------------------------------------------------


def _run_render_best_row(row_js: str) -> str:
    source = _read_index()
    pieces = [
        _strip_js_comments(_extract_const(source, "IDLE_BEST_METRIC_LABEL_KEYS")),
        _strip_js_comments(_extract_function(source, "formatTreadmillPace")),
        _strip_js_comments(_extract_function(source, "formatIdleMetricValue")),
        _strip_js_comments(_extract_function(source, "idleParticipantLabel")),
        _strip_js_comments(_extract_function(source, "renderIdleBestRow")),
    ]
    script = (
        "function t(key) { return key; }\n"
        "function escapeHtml(value) { return String(value); }\n"
        + "\n".join(pieces)
        + "\n"
        + f"console.log(JSON.stringify(renderIdleBestRow({row_js})));"
    )
    return json.loads(_run_node(script))


def test_best_row_renders_treadmill_pace_with_station_label():
    html = _run_render_best_row(
        '{"metric": "treadmill_pace_speed_kph", "value": 12.0, "station_number": 2}'
    )
    assert "5:00 /km" in html
    assert "stations.station 2" in html


def test_best_row_still_renders_generic_speed_metric():
    html = _run_render_best_row(
        '{"metric": "instantaneous_speed_kph", "value": 20.0, "station_number": 3}'
    )
    assert "20.0" in html
    assert "km/h" in html
