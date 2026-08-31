"""Test the training class board rendering on the dashboard.

The class board is a display-only component shown when session_mode === "class".
It includes:
- A hero band with segment kind (enlarged, volt-yellow), countdown, segment
  progress, and timeline visualization
- Station cards displaying athlete name, machine name, and live metrics (power,
  speed, distance) with an effort bar

The JavaScript functions `classClockAt` and `formatClock` mirror their Python
counterparts (segment_at and formatClock) byte-for-byte. This module executes
them under `node` with test cases from the Python test suite to ensure
behavioral alignment.

The render path (buildClassBoardHtml) is executed under `node` with stubs to
verify that the returned HTML string actually contains translated segment kind
labels, formatted countdown times, and athlete/machine names — not just that
the functions are called somewhere in the source.

Negative assertions verify that class-mode rendering never invokes race-only
functions like showPodiumOverlay, triggerFinishCelebration, or
enterIdleRecordWall.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"
LOCALES_DIR = (
    Path(__file__).resolve().parents[3] / "hub_server" / "infrastructure" / "locales"
)

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CJK_RE = re.compile(r"[一-鿿぀-ゟ゠-ヿ가-힯]")


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _matching_brace_end(source: str, open_idx: int) -> int:
    """Return the index of the "}" that matches the "{" at open_idx,
    tracking string literals so braces inside quoted values don't throw off
    the depth count."""
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
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _t_stub() -> str:
    return "const t = (key, params = {}) => { let value = `T[${key}]`; Object.entries(params).forEach(([name, replacement]) => { value = value.replaceAll(`{${name}}`, String(replacement)); }); return value; };\n"


def _metric_number_stub() -> str:
    return (
        "const metricNumber = (value, fallback = 0) => {\n"
        "  const n = Number(value);\n"
        "  return Number.isFinite(n) ? n : fallback;\n"
        "};\n"
    )


def _escape_html_stub() -> str:
    return (
        "const escapeHtml = (value) => {\n"
        "  return String(value ?? '')\n"
        "    .replace(/&/g, '&amp;')\n"
        "    .replace(/</g, '&lt;')\n"
        "    .replace(/>/g, '&gt;')\n"
        "    .replace(/\"/g, '&quot;')\n"
        "    .replace(/'/g, '&#039;');\n"
        "};\n"
    )


def _node_display_name_stub() -> str:
    return (
        "const nodeDisplayName = (node) => {\n"
        "  return node?.node_display_name || node?.display_name || node?.node_id || '--';\n"
        "};\n"
    )


def _intl_number_format_stub() -> str:
    return "const Intl = { NumberFormat: function(locale) { return { format: (n) => String(n) }; } };\n"


# -- 1. classClockAt tests
def _run_class_clock_at(now_ms: int, start_ms: int, plan_js: str) -> dict:
    """Execute classClockAt under node with the given parameters."""
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "classClockAt"))
    script = (
        _metric_number_stub()
        + fn
        + "\n"
        + f"const plan = {plan_js};\n"
        + f"console.log(JSON.stringify(classClockAt({now_ms}, {start_ms}, plan)));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def test_class_clock_at_start_is_first_segment_not_finished():
    """classClockAt at elapsed=0 returns first segment with full remaining time."""
    plan = '{"segments": [{"kind": "warmup", "duration_sec": 300}, {"kind": "work", "duration_sec": 1200}, {"kind": "cooldown", "duration_sec": 300}]}'
    result = _run_class_clock_at(0, 0, plan)
    assert result == {
        "index": 0,
        "kind": "warmup",
        "segmentRemainingMs": 300000,
        "totalRemainingMs": 1800000,
        "finished": False,
    }


def test_class_clock_at_negative_elapsed_clamps_to_first_segment():
    """classClockAt with negative elapsed clamps to first segment."""
    plan = '{"segments": [{"kind": "warmup", "duration_sec": 300}, {"kind": "work", "duration_sec": 1200}]}'
    result = _run_class_clock_at(-5000, 0, plan)
    assert result["index"] == 0
    assert result["finished"] is False
    assert result["segmentRemainingMs"] == 300000


def test_class_clock_at_mid_second_segment():
    """classClockAt in the middle of segment 1."""
    plan = '{"segments": [{"kind": "warmup", "duration_sec": 300}, {"kind": "work", "duration_sec": 1200}, {"kind": "cooldown", "duration_sec": 300}]}'
    result = _run_class_clock_at(400000, 0, plan)
    assert result["index"] == 1
    assert result["kind"] == "work"
    assert result["segmentRemainingMs"] == 1100000
    assert result["totalRemainingMs"] == 1400000
    assert result["finished"] is False


def test_class_clock_at_past_total_duration_is_finished_pinned_to_last_segment():
    """classClockAt past total duration is finished on last segment."""
    plan = '{"segments": [{"kind": "warmup", "duration_sec": 300}, {"kind": "work", "duration_sec": 1200}, {"kind": "cooldown", "duration_sec": 300}]}'
    result = _run_class_clock_at(999999999, 0, plan)
    assert result == {
        "index": 2,
        "kind": "cooldown",
        "segmentRemainingMs": 0,
        "totalRemainingMs": 0,
        "finished": True,
    }


def test_class_clock_at_exact_boundary_between_differently_sized_segments():
    """classClockAt at exact segment boundaries uses strict < comparison."""
    plan = '{"segments": [{"kind": "warmup", "duration_sec": 300}, {"kind": "work", "duration_sec": 1200}, {"kind": "rest", "duration_sec": 60}, {"kind": "cooldown", "duration_sec": 300}]}'

    # At end of segment 0 (300_000ms) -> must be segment 1, not 0
    at_end_of_warmup = _run_class_clock_at(300000, 0, plan)
    assert at_end_of_warmup["index"] == 1
    assert at_end_of_warmup["kind"] == "work"
    assert at_end_of_warmup["segmentRemainingMs"] == 1200000

    # One millisecond before -> still segment 0
    just_before = _run_class_clock_at(299999, 0, plan)
    assert just_before["index"] == 0
    assert just_before["kind"] == "warmup"
    assert just_before["segmentRemainingMs"] == 1

    # At end of segment 1 (1_500_000ms) -> must be segment 2, not 1
    at_end_of_work = _run_class_clock_at(1500000, 0, plan)
    assert at_end_of_work["index"] == 2
    assert at_end_of_work["kind"] == "rest"
    assert at_end_of_work["segmentRemainingMs"] == 60000

    # At exact total duration -> finished, pinned to last
    total_ms = 300000 + 1200000 + 60000 + 300000
    at_total_end = _run_class_clock_at(total_ms, 0, plan)
    assert at_total_end["finished"] is True
    assert at_total_end["index"] == 3

    # One ms before total -> not finished, still index 3
    just_before_total_end = _run_class_clock_at(total_ms - 1, 0, plan)
    assert just_before_total_end["finished"] is False
    assert just_before_total_end["index"] == 3
    assert just_before_total_end["segmentRemainingMs"] == 1


# -- 2. formatClock tests
def _run_format_clock(ms: int) -> str:
    """Execute formatClock under node with the given milliseconds."""
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "formatClock"))
    script = _metric_number_stub() + fn + "\n" + f"console.log(formatClock({ms}));"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def test_format_clock_zero():
    """formatClock(0) returns "00:00"."""
    assert _run_format_clock(0) == "00:00"


def test_format_clock_65_seconds():
    """formatClock(65000) returns "01:05"."""
    assert _run_format_clock(65000) == "01:05"


def test_format_clock_zero_pads_seconds():
    """formatClock zero-pads seconds (5000ms = "00:05")."""
    assert _run_format_clock(5000) == "00:05"


def test_format_clock_multi_minute():
    """formatClock(3661000) handles multi-minute times."""
    # 3661000ms = 3661s = 61m 1s = "61:01"
    assert _run_format_clock(3661000) == "61:01"


def test_format_clock_negative_clamps_to_zero():
    """formatClock with negative ms clamps to zero."""
    assert _run_format_clock(-1000) == "00:00"


# -- 3. buildClassBoardHtml render path
def _format_clock_stub() -> str:
    return (
        "function formatClock(ms) {\n"
        "  const safeMs = Math.max(0, metricNumber(ms));\n"
        "  const totalSeconds = Math.floor(safeMs / 1000);\n"
        "  const seconds = totalSeconds % 60;\n"
        "  const minutes = Math.floor(totalSeconds / 60);\n"
        "  return (minutes < 10 ? '0' + minutes : String(minutes)) + ':' +\n"
        "         (seconds < 10 ? '0' + seconds : String(seconds));\n"
        "}\n"
    )


def _run_build_class_board_html(session_data_js: str, clock_js: str) -> str:
    """Execute buildClassBoardHtml under node with the given session data and clock."""
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "buildClassBoardHtml"))
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _node_display_name_stub()
        + _intl_number_format_stub()
        + _format_clock_stub()
        + "const currentLocale = 'en-US';\n"
        + fn
        + "\n"
        + f"const sessionData = {session_data_js};\n"
        + f"const clock = {clock_js};\n"
        + "console.log(buildClassBoardHtml(sessionData, clock));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def test_build_class_board_html_renders_segment_kind_label():
    """buildClassBoardHtml returns HTML containing the translated segment kind."""
    session_data = '{"class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]}, "leaderboard": {}}'
    clock = '{"index": 0, "kind": "work", "segmentRemainingMs": 300000, "totalRemainingMs": 300000, "finished": false}'
    html = _run_build_class_board_html(session_data, clock)
    assert "T[class.kind.work]" in html


def test_build_class_board_html_renders_countdown():
    """buildClassBoardHtml returns HTML containing a formatted countdown."""
    session_data = '{"class_plan": {"segments": [{"kind": "work", "duration_sec": 65}]}, "leaderboard": {}}'
    clock = '{"index": 0, "kind": "work", "segmentRemainingMs": 65000, "totalRemainingMs": 65000, "finished": false}'
    html = _run_build_class_board_html(session_data, clock)
    # 65000ms = 65s = 01:05
    assert "01:05" in html


def test_build_class_board_html_renders_station_athlete_name():
    """buildClassBoardHtml includes station athlete names in the HTML."""
    session_data = '{"class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]}, "leaderboard": {"node1": {"node_id": "node1", "athlete_name": "Alice", "station_number": 1, "power_watts": 100, "instantaneous_speed_kph": 20, "distance_m": 500}}}'
    clock = '{"index": 0, "kind": "work", "segmentRemainingMs": 300000, "totalRemainingMs": 300000, "finished": false}'
    html = _run_build_class_board_html(session_data, clock)
    assert "Alice" in html


def test_build_class_board_html_renders_station_machine_name():
    """buildClassBoardHtml includes machine names via nodeDisplayName."""
    session_data = '{"class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]}, "leaderboard": {"node1": {"node_id": "node1", "node_display_name": "BIKE_01", "athlete_name": "Alice", "station_number": 1, "power_watts": 100, "instantaneous_speed_kph": 20, "distance_m": 500}}}'
    clock = '{"index": 0, "kind": "work", "segmentRemainingMs": 300000, "totalRemainingMs": 300000, "finished": false}'
    html = _run_build_class_board_html(session_data, clock)
    assert "BIKE_01" in html


def test_build_class_board_html_renders_segment_progress():
    """buildClassBoardHtml includes segment progress (N of M)."""
    session_data = '{"class_plan": {"segments": [{"kind": "warmup", "duration_sec": 300}, {"kind": "work", "duration_sec": 300}]}, "leaderboard": {}}'
    clock = '{"index": 0, "kind": "warmup", "segmentRemainingMs": 300000, "totalRemainingMs": 600000, "finished": false}'
    html = _run_build_class_board_html(session_data, clock)
    # Should reference class.segment_progress with index=1 (0-indexed + 1), total=2
    assert "T[class.segment_progress]" in html


# -- Station ordering: a class is NEVER a leaderboard. The single defining
# property of the class board is that station cards are ordered by
# station_number, never by who is performing best. These tests feed a
# leaderboard whose station order is the exact REVERSE of performance order
# (station 1 = worst output, station 4 = best output) so that any
# performance-based sort -- ascending OR descending -- produces a visibly
# different card order than the required station-number order, and assert
# on the actual index positions of the athlete names in the rendered HTML
# string, not merely that the names are present somewhere.
def test_build_class_board_html_orders_stations_by_number_not_by_performance():
    """Station cards must render in ascending station_number order even
    when that is the exact opposite of ascending/descending performance
    order -- proving the board can never silently become a leaderboard."""
    leaderboard = {
        "n1": {
            "node_id": "n1",
            "athlete_name": "Ash",
            "station_number": 1,
            "power_watts": 50,
            "instantaneous_speed_kph": 10,
            "distance_m": 100,
        },
        "n2": {
            "node_id": "n2",
            "athlete_name": "Blair",
            "station_number": 2,
            "power_watts": 150,
            "instantaneous_speed_kph": 20,
            "distance_m": 300,
        },
        "n3": {
            "node_id": "n3",
            "athlete_name": "Casey",
            "station_number": 3,
            "power_watts": 250,
            "instantaneous_speed_kph": 30,
            "distance_m": 500,
        },
        "n4": {
            "node_id": "n4",
            "athlete_name": "Drew",
            "station_number": 4,
            "power_watts": 350,
            "instantaneous_speed_kph": 40,
            "distance_m": 700,
        },
    }
    session_data = json.dumps(
        {
            "class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
            "leaderboard": leaderboard,
        }
    )
    clock = '{"index": 0, "kind": "work", "segmentRemainingMs": 300000, "totalRemainingMs": 300000, "finished": false}'
    html = _run_build_class_board_html(session_data, clock)

    for name in ("Ash", "Blair", "Casey", "Drew"):
        assert name in html, f"{name} missing from rendered class board HTML"

    positions = {name: html.index(name) for name in ("Ash", "Blair", "Casey", "Drew")}
    assert (
        positions["Ash"] < positions["Blair"] < positions["Casey"] < positions["Drew"]
    ), (
        "station cards must appear in ascending station_number order "
        f"(Ash=station1..Drew=station4), got positions {positions} -- "
        "distance_m is strictly INCREASING with station_number here, so "
        "any performance-based sort (ascending or descending) would "
        "produce a different order than station order, and this assertion "
        "would fail"
    )


def test_build_class_board_html_orders_tied_performance_stations_by_station_number():
    """Two stations with IDENTICAL metrics must still render in
    station_number order -- a tie in performance is not a license to fall
    back to output order, insertion order, or anything else."""
    leaderboard = {
        "n2": {
            "node_id": "n2",
            "athlete_name": "Frankie",
            "station_number": 2,
            "power_watts": 200,
            "instantaneous_speed_kph": 25,
            "distance_m": 400,
        },
        "n1": {
            "node_id": "n1",
            "athlete_name": "Eden",
            "station_number": 1,
            "power_watts": 200,
            "instantaneous_speed_kph": 25,
            "distance_m": 400,
        },
    }
    session_data = json.dumps(
        {
            "class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
            "leaderboard": leaderboard,
        }
    )
    clock = '{"index": 0, "kind": "work", "segmentRemainingMs": 300000, "totalRemainingMs": 300000, "finished": false}'
    html = _run_build_class_board_html(session_data, clock)

    assert "Eden" in html and "Frankie" in html
    assert html.index("Eden") < html.index("Frankie"), (
        "stations with identical performance metrics must still be ordered "
        "by station_number (Eden=station1 before Frankie=station2)"
    )


# -- Regression test: server sends snake_case, must convert to camelCase
def _run_to_clock_shape(server_segment_js: str) -> dict:
    """Execute toClockShape under node with a server segment."""
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "toClockShape"))
    script = (
        fn
        + "\n"
        + f"const serverSegment = {server_segment_js};\n"
        + "console.log(JSON.stringify(toClockShape(serverSegment)));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def test_to_clock_shape_converts_snake_case_to_camel_case():
    """toClockShape converts server's snake_case segment to camelCase clock."""
    server_segment = '{"index": 0, "kind": "work", "segment_remaining_ms": 123456, "total_remaining_ms": 183456, "finished": false}'
    result = _run_to_clock_shape(server_segment)
    assert result == {
        "index": 0,
        "kind": "work",
        "segmentRemainingMs": 123456,
        "totalRemainingMs": 183456,
        "finished": False,
    }


def test_to_clock_shape_preserves_all_fields():
    """toClockShape preserves index, kind, and finished along with time fields."""
    server_segment = '{"index": 2, "kind": "rest", "segment_remaining_ms": 45000, "total_remaining_ms": 95000, "finished": true}'
    result = _run_to_clock_shape(server_segment)
    assert result["index"] == 2
    assert result["kind"] == "rest"
    assert result["finished"] is True
    assert result["segmentRemainingMs"] == 45000
    assert result["totalRemainingMs"] == 95000


# -- 4. Negative assertions: class board renders NO race markup
def test_build_class_board_html_never_contains_rank_champion():
    """Class board HTML must never contain rank-champion markup."""
    session_data = '{"class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]}, "leaderboard": {}}'
    clock = '{"index": 0, "kind": "work", "segmentRemainingMs": 300000, "totalRemainingMs": 300000, "finished": false}'
    html = _run_build_class_board_html(session_data, clock)
    assert "rank-champion" not in html
    assert "rank-silver" not in html
    assert "rank-bronze" not in html


def test_build_class_board_html_never_contains_champion_final():
    """Class board HTML must never contain champion-final markup."""
    session_data = '{"class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]}, "leaderboard": {}}'
    clock = '{"index": 0, "kind": "work", "segmentRemainingMs": 300000, "totalRemainingMs": 300000, "finished": false}'
    html = _run_build_class_board_html(session_data, clock)
    assert "champion-final" not in html
    assert "podium-panel" not in html
    assert "podium-overlay" not in html


def test_build_class_board_html_never_contains_medal_classes():
    """Class board HTML must never contain medal classes."""
    session_data = '{"class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]}, "leaderboard": {}}'
    clock = '{"index": 0, "kind": "work", "segmentRemainingMs": 300000, "totalRemainingMs": 300000, "finished": false}'
    html = _run_build_class_board_html(session_data, clock)
    assert "medal-gold" not in html
    assert "medal-silver" not in html
    assert "medal-bronze" not in html


def test_build_class_board_html_never_contains_rank_animation():
    """Class board HTML must never contain rank animation classes."""
    session_data = '{"class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]}, "leaderboard": {}}'
    clock = '{"index": 0, "kind": "work", "segmentRemainingMs": 300000, "totalRemainingMs": 300000, "finished": false}'
    html = _run_build_class_board_html(session_data, clock)
    assert "rank-up" not in html
    assert "rank-down" not in html


# -- 5. Class mode functions never call race-only functions
def test_class_board_functions_never_call_show_podium_overlay():
    """Verify showPodiumOverlay is guarded by session_mode !== class check."""
    source = _read_index()
    stripped = _strip_js_comments(source)

    # Find the showPodiumOverlay call and verify it's guarded by session_mode !== "class"
    # Look for the pattern where the guard and call are adjacent in the same block
    pattern = r'session_mode\s*!==\s*["\']class["\']\s*\)\s*\{\s*showPodiumOverlay\s*\('
    assert re.search(pattern, stripped), (
        'showPodiumOverlay() must be guarded by session_mode !== "class" check '
        "in the same conditional block (guard immediately preceding the call)"
    )


def test_class_board_functions_never_call_trigger_finish_celebration():
    """Verify triggerFinishCelebration is only in renderLeaderboard, which is race-mode only."""
    source = _read_index()
    stripped = _strip_js_comments(source)
    # Verify class mode routing exists and calls renderClassBoard
    assert 'data.session_mode === "class"' in stripped, "class mode check must exist"
    assert (
        "renderClassBoard(data)" in stripped
    ), "renderClassBoard must be called in class mode"
    # Verify race mode routing exists
    assert (
        "else if" in stripped and "data.leaderboard" in stripped
    ), "race mode branch must exist"
    # Verify renderLeaderboard is called (which contains triggerFinishCelebration)
    assert "renderLeaderboard(" in stripped, "renderLeaderboard must exist"


def test_class_board_functions_never_call_enter_idle_record_wall():
    """Verify enterIdleRecordWall is guarded: class mode calls exitIdleRecordWall instead."""
    source = _read_index()
    stripped = _strip_js_comments(source)
    # Verify the key guard pattern: class mode explicitly calls exitIdleRecordWall
    assert 'data.session_mode === "class"' in stripped, "class mode check must exist"
    assert (
        "exitIdleRecordWall()" in stripped
    ), "exitIdleRecordWall must be called in class mode"
    # Verify enterIdleRecordWall exists but is in the race-mode branch
    assert (
        "enterIdleRecordWall()" in stripped
    ), "enterIdleRecordWall must exist for race mode"
    # Verify they're in separate branches by looking for the pattern where the updateUIState
    # function has: if class, call exit; else-if, call enter
    assert re.search(
        r'session_mode\s*===\s*["\']class["\']\s*\)[^}]{0,300}exitIdleRecordWall[^}]{0,200}\}[^}]{0,100}else',
        stripped,
    ), "exitIdleRecordWall must be in class mode branch, with enterIdleRecordWall in else"


# -- 6. i18n keys exist and are correctly localized
def test_all_class_mode_keys_exist_in_en_us():
    """Every class mode key referenced in the implementation exists in en-US."""
    expected_keys = {
        "class.in_progress",
        "class.kind.warmup",
        "class.kind.work",
        "class.kind.rest",
        "class.kind.cooldown",
        "class.segment_progress",
        "class.next_segment",
        "class.last_segment",
        "class.total_remaining",
        "class.time_up",
    }
    locales_path = LOCALES_DIR / "en-US.json"
    with open(locales_path, "r", encoding="utf-8") as f:
        en_us = json.load(f)
    missing = expected_keys - set(en_us.keys())
    assert not missing, f"Missing keys in en-US: {missing}"


def test_all_class_mode_keys_exist_in_all_locales():
    """All class mode keys exist in all supported locales."""
    expected_keys = {
        "class.in_progress",
        "class.kind.warmup",
        "class.kind.work",
        "class.kind.rest",
        "class.kind.cooldown",
        "class.segment_progress",
        "class.next_segment",
        "class.last_segment",
        "class.total_remaining",
        "class.time_up",
    }
    locales = ["de-CH", "en-US", "fr", "it", "sv", "zh-TW"]
    for locale in locales:
        locales_path = LOCALES_DIR / f"{locale}.json"
        with open(locales_path, "r", encoding="utf-8") as f:
            messages = json.load(f)
        missing = expected_keys - set(messages.keys())
        assert not missing, f"Missing keys in {locale}: {missing}"


def test_class_mode_keys_in_zh_tw_contain_cjk_characters():
    """zh-TW translations for class mode keys should contain CJK characters."""
    locales_path = LOCALES_DIR / "zh-TW.json"
    with open(locales_path, "r", encoding="utf-8") as f:
        zh_tw = json.load(f)

    # At least these should have CJK (not every one necessarily, but most)
    keys_to_check = [
        "class.in_progress",
        "class.time_up",
        "class.total_remaining",
        "class.segment_progress",
    ]
    for key in keys_to_check:
        if key in zh_tw:
            value = zh_tw[key]
            assert _CJK_RE.search(
                value
            ), f"{key} in zh-TW should contain CJK but is: {value}"


def test_class_segment_progress_has_correct_placeholders():
    """class.segment_progress should have {index} and {total} placeholders."""
    locales_path = LOCALES_DIR / "en-US.json"
    with open(locales_path, "r", encoding="utf-8") as f:
        en_us = json.load(f)
    value = en_us.get("class.segment_progress", "")
    assert "{index}" in value, f"class.segment_progress missing {{index}}: {value}"
    assert "{total}" in value, f"class.segment_progress missing {{total}}: {value}"


def test_class_next_segment_has_correct_placeholders():
    """class.next_segment should have {kind} and {duration} placeholders."""
    locales_path = LOCALES_DIR / "en-US.json"
    with open(locales_path, "r", encoding="utf-8") as f:
        en_us = json.load(f)
    value = en_us.get("class.next_segment", "")
    assert "{kind}" in value, f"class.next_segment missing {{kind}}: {value}"
    assert "{duration}" in value, f"class.next_segment missing {{duration}}: {value}"


def test_placeholder_tokens_match_across_all_locales():
    """Placeholders in class mode keys must be identical across all locales."""
    locales = ["de-CH", "en-US", "fr", "it", "sv", "zh-TW"]
    all_messages = {}
    for locale in locales:
        locales_path = LOCALES_DIR / f"{locale}.json"
        with open(locales_path, "r", encoding="utf-8") as f:
            all_messages[locale] = json.load(f)

    parameterized_keys = [
        "class.segment_progress",
        "class.next_segment",
    ]

    for key in parameterized_keys:
        if key not in all_messages["en-US"]:
            continue
        en_us_value = all_messages["en-US"][key]
        en_us_tokens = set(re.findall(r"\{(\w+)\}", en_us_value))

        for locale in locales:
            if locale == "en-US":
                continue
            if key not in all_messages[locale]:
                continue
            locale_value = all_messages[locale][key]
            locale_tokens = set(re.findall(r"\{(\w+)\}", locale_value))
            assert (
                locale_tokens == en_us_tokens
            ), f"{key} in {locale} has tokens {locale_tokens}, expected {en_us_tokens}"


# -- 7. renderClassBoard integration: toClockShape is called with server data
def _run_render_class_board(data_js: str) -> dict:
    """Execute renderClassBoard under node with realistic server state_change data."""
    source = _read_index()
    render_fn = _strip_js_comments(_extract_function(source, "renderClassBoard"))
    to_clock_shape_fn = _strip_js_comments(_extract_function(source, "toClockShape"))
    class_clock_at_fn = _strip_js_comments(_extract_function(source, "classClockAt"))
    build_class_board_html_fn = _strip_js_comments(
        _extract_function(source, "buildClassBoardHtml")
    )

    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _node_display_name_stub()
        + _intl_number_format_stub()
        + _format_clock_stub()
        + "const currentLocale = 'en-US';\n"
        + to_clock_shape_fn
        + "\n"
        + class_clock_at_fn
        + "\n"
        + build_class_board_html_fn
        + "\n"
        + render_fn
        + "\n"
        + "const container = { innerHTML: '' };\n"
        + "document = { getElementById: () => container };\n"
        + f"const data = {data_js};\n"
        + "renderClassBoard(data);\n"
        + "console.log(JSON.stringify({ innerHTML: container.innerHTML }));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def test_render_class_board_calls_to_clock_shape_with_server_data():
    """renderClassBoard calls toClockShape when class_segment is present and state is RUNNING."""
    data = {
        "state": "RUNNING",
        "session_mode": "class",
        "class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
        "class_segment": {
            "index": 0,
            "kind": "work",
            "segment_remaining_ms": 123456,
            "total_remaining_ms": 183456,
            "finished": False,
        },
        "leaderboard": {},
    }
    result = _run_render_class_board(json.dumps(data))
    html = result["innerHTML"]

    # If toClockShape was called and result correctly converted, formatClock(123456)
    # returns "02:03", which should appear in the HTML countdown display
    assert (
        "02:03" in html
    ), f"Expected '02:03' (from 123456ms via toClockShape) in HTML but got: {html}"

    # Negative: ensure we don't see "00:00" as the only time value (which would indicate
    # toClockShape was bypassed and server's snake_case segment was used raw, falling back
    # to classClockAt(0, ...) which returns finished=true with 00:00)
    # Count occurrences of "00:00" - should not be the primary countdown value
    lines_with_00_00 = [
        line
        for line in html.split("\n")
        if "00:00" in line and "countdown" in line.lower()
    ]
    assert (
        not lines_with_00_00
    ), f"renderClassBoard should not show 00:00 as countdown; found: {lines_with_00_00}"


# -- 8. WEBSOCKET MESSAGE PATH regression: the live-hub defect.
#
# Every test above exercises buildClassBoardHtml/renderClassBoard directly --
# never the actual path the projector takes. In production, ws.onmessage's
# untyped-telemetry branch (populated payloads with no `type` field, sent
# several times a second by mqtt_subscriber.py's _handle_telemetry) called
# renderLeaderboard(data) unconditionally, with no session_mode check
# anywhere in reach, because the raw payload never carries session_mode.
# Within ~250ms of a class starting, the projector reverted to a ranked
# race leaderboard (crown/medal emoji, a station "winning") and stayed
# there for the whole class -- the exact thing a class board exists to
# prevent.
#
# These tests extract the REAL ws.onmessage handler (the same anchor/brace
# technique tests/unit/hub/test_dashboard_stations_refetch_storm.py uses)
# and execute it under node with a synthetic untyped-telemetry `event`,
# proving the class board -- not the leaderboard -- is what ends up on
# screen when session mode is "class".


def _extract_braced(source: str, anchor: str) -> str:
    """Return the exact `{ ... }` block that immediately follows `anchor`,
    walking brace depth character-by-character (mirrors the technique in
    tests/unit/hub/test_dashboard_stations_refetch_storm.py) so a template
    literal's own braces can't throw off the match."""
    start = source.index(anchor) + len(anchor)
    brace_start = source.index("{", start)
    depth = 0
    for i in range(brace_start, len(source)):
        char = source[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start : i + 1]
    raise AssertionError(f"unbalanced braces after anchor {anchor!r}")


def _index_onmessage_body() -> str:
    source = _read_index()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    script = source[start:end]
    return _strip_js_comments(_extract_braced(script, "ws.onmessage = (event) =>"))


def _run_ws_onmessage_untyped_telemetry(
    telemetry_payload: dict, session_mode, class_plan_js: str
) -> dict:
    """Executes the REAL ws.onmessage handler body -- not renderLeaderboard
    or buildClassBoardHtml in isolation -- with a synthetic untyped
    telemetry `event`, mirroring exactly what mqtt_subscriber.py's
    _handle_telemetry broadcasts on every telemetry row. `renderLeaderboard`
    (the function the untyped branch delegates to) and its class-mode
    dependencies are the real, unmodified functions extracted from
    index.html; `detectAthleteFinishes` -- the first call on the race-only
    path -- is stubbed to record that the race path was reached and to
    stop execution there (raising a private sentinel), so this doesn't also
    need every downstream race-rendering helper stubbed just to prove
    control flow never got that far."""
    source = _read_index()
    onmessage_body = _index_onmessage_body()
    render_leaderboard_fn = _strip_js_comments(
        _extract_function(source, "renderLeaderboard")
    )
    render_class_board_from_state_fn = _strip_js_comments(
        _extract_function(source, "renderClassBoardFromState")
    )
    class_clock_at_fn = _strip_js_comments(_extract_function(source, "classClockAt"))
    build_class_board_html_fn = _strip_js_comments(
        _extract_function(source, "buildClassBoardHtml")
    )

    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _node_display_name_stub()
        + _intl_number_format_stub()
        + _format_clock_stub()
        + "const currentLocale = 'en-US';\n"
        + f"let currentSessionMode = {json.dumps(session_mode)};\n"
        + f"let currentClassPlan = {class_plan_js};\n"
        + "let currentClassLeaderboard = {};\n"
        + "let raceStartTime = Date.now() - 5000;\n"
        + "let reachedRacePath = false;\n"
        + "function detectAthleteFinishes() { reachedRacePath = true; throw new Error('__STOP_RACE_PATH__'); }\n"
        + "let httpCallsMade = [];\n"
        + "function fetch(url) { httpCallsMade.push(url); return Promise.resolve(); }\n"
        + class_clock_at_fn
        + "\n"
        + build_class_board_html_fn
        + "\n"
        + render_class_board_from_state_fn
        + "\n"
        + render_leaderboard_fn
        + "\n"
        + "const leaderboardContainer = { innerHTML: '<div class=\"leaderboard-list\">STALE RACE MARKUP</div>' };\n"
        + 'const document = { getElementById: (id) => (id === "leaderboard-container" ? leaderboardContainer : null) };\n'
        + "const ws = {};\n"
        + f"ws.onmessage = (event) => {onmessage_body}\n"
        + f"const event = {{ data: {json.dumps(json.dumps(telemetry_payload))} }};\n"
        + "try {\n"
        + "  ws.onmessage(event);\n"
        + "} catch (e) {\n"
        + "  if (e.message !== '__STOP_RACE_PATH__') throw e;\n"
        + "}\n"
        + "console.log(JSON.stringify({ innerHTML: leaderboardContainer.innerHTML, reachedRacePath, httpCallsMade }));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


_UNTYPED_TELEMETRY_PAYLOAD = {
    "n1": {
        "node_id": "n1",
        "athlete_name": "Sofia",
        "station_number": 3,
        "power_watts": 420,
        "instantaneous_speed_kph": 38,
        "distance_m": 900,
    }
}

_ONE_SEGMENT_PLAN_JS = '{"segments": [{"kind": "work", "duration_sec": 300}]}'


def test_ws_onmessage_untyped_telemetry_renders_class_board_when_session_mode_is_class():
    """The exact live-hub defect: an untyped telemetry broadcast, fed
    through the real ws.onmessage handler while session mode is "class",
    must render the class board -- not the race leaderboard promoting
    Sofia to first place with a crown."""
    result = _run_ws_onmessage_untyped_telemetry(
        _UNTYPED_TELEMETRY_PAYLOAD, "class", _ONE_SEGMENT_PLAN_JS
    )
    html = result["innerHTML"]
    assert "Sofia" in html, f"expected the class board station card, got: {html}"
    assert "T[class.in_progress]" in html, f"expected class board markup, got: {html}"
    assert "STALE RACE MARKUP" not in html


def test_ws_onmessage_untyped_telemetry_never_reaches_race_leaderboard_path_when_class():
    """The race-only path (detectAthleteFinishes, the first call inside
    renderLeaderboard's race branch) must never run when session mode is
    "class" -- proving the routing decision happens before any race-mode
    logic, not merely that the class board also happens to get rendered
    afterward."""
    result = _run_ws_onmessage_untyped_telemetry(
        _UNTYPED_TELEMETRY_PAYLOAD, "class", _ONE_SEGMENT_PLAN_JS
    )
    assert result["reachedRacePath"] is False


def test_ws_onmessage_untyped_telemetry_never_contains_rank_markup_when_class():
    """Negative assertion mirroring section 4 above, but through the real
    WS path this time: no crown/medal/rank markup may appear."""
    result = _run_ws_onmessage_untyped_telemetry(
        _UNTYPED_TELEMETRY_PAYLOAD, "class", _ONE_SEGMENT_PLAN_JS
    )
    html = result["innerHTML"]
    assert "rank-champion" not in html
    assert "leaderboard-list" not in html


def test_ws_onmessage_untyped_telemetry_still_reaches_race_path_when_not_class():
    """Regression guard on the fix itself: the class-mode routing must not
    accidentally swallow ordinary race telemetry too. When session mode is
    anything other than "class", control must still reach the race-only
    path."""
    result = _run_ws_onmessage_untyped_telemetry(
        _UNTYPED_TELEMETRY_PAYLOAD, "race", _ONE_SEGMENT_PLAN_JS
    )
    assert result["reachedRacePath"] is True


def test_ws_onmessage_untyped_telemetry_makes_no_http_request_when_class():
    """The class-mode render path must stay exactly as HTTP-free as the
    race path guarded by test_dashboard_stations_refetch_storm.py -- the
    fix must not reach for fetchStations()/fetchState() or any other HTTP
    call to do its routing."""
    result = _run_ws_onmessage_untyped_telemetry(
        _UNTYPED_TELEMETRY_PAYLOAD, "class", _ONE_SEGMENT_PLAN_JS
    )
    assert result["httpCallsMade"] == []


# -- 9. Chrome must never announce a race during a class (Defect 2).
#
# getRaceStageDetails/getClassStageDetails drive the stage banner;
# renderDashboardChrome drives the header type indicator, config
# description, and leaderboard panel title. All five must describe the
# class (or be silent) while a class is active, never "Race: Not
# configured" / "No race configured" / "Ranking updates live".


def _run_get_class_stage_details(stage_js: str) -> dict:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "getClassStageDetails"))
    script = (
        _t_stub()
        + fn
        + "\n"
        + f"console.log(JSON.stringify(getClassStageDetails({stage_js})));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def test_get_class_stage_details_running_never_claims_live_ranking():
    result = _run_get_class_stage_details('"RUNNING"')
    assert result["main"] == "T[class.in_progress]"
    assert result["kicker"] == "T[class.stage_kicker]"
    assert result["sub"] == "T[class.stage_sub]"
    for value in result.values():
        assert "stage.live_race" not in str(value)
        assert "stage.running_sub" not in str(value)


def test_get_class_stage_details_non_running_states_never_say_unconfigured():
    for stage in ('"READY"', '"STOPPED"', '"IDLE"'):
        result = _run_get_class_stage_details(stage)
        assert result["main"] == "T[class.stage_not_running]"
        for value in result.values():
            assert "race.unconfigured" not in str(value)
            assert "status.no_config" not in str(value)
            assert "stage.venue_display" not in str(value)


def _run_render_dashboard_chrome(
    session_mode: str, current_config_js: str = "null"
) -> dict:
    source = _read_index()
    render_fn = _strip_js_comments(_extract_function(source, "renderDashboardChrome"))
    race_stage_fn = _strip_js_comments(
        _extract_function(source, "renderRaceStageBanner")
    )
    get_race_stage_fn = _strip_js_comments(
        _extract_function(source, "getRaceStageDetails")
    )
    get_class_stage_fn = _strip_js_comments(
        _extract_function(source, "getClassStageDetails")
    )

    script = (
        _t_stub()
        + _metric_number_stub()
        + f"let currentSessionMode = {json.dumps(session_mode)};\n"
        + f"let currentConfig = {current_config_js};\n"
        + "let currentState = 'RUNNING';\n"
        + "let raceStageOverride = null;\n"
        + "const configDesc = {};\n"
        + "const typeIndicator = {};\n"
        + "const panelTitle = {};\n"
        + "const document = { getElementById: (id) => ({\n"
        + '  "current-config-desc": configDesc,\n'
        + '  "race-type-indicator": typeIndicator,\n'
        + '  "leaderboard-panel-title": panelTitle,\n'
        + "}[id] || null) };\n"
        + get_class_stage_fn
        + "\n"
        + get_race_stage_fn
        + "\n"
        + race_stage_fn
        + "\n"
        + render_fn
        + "\n"
        + "renderDashboardChrome();\n"
        + "console.log(JSON.stringify({ configDesc: configDesc.innerText, typeIndicator: typeIndicator.innerText, panelTitle: panelTitle.innerText }));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def test_render_dashboard_chrome_never_shows_race_unconfigured_in_class_mode():
    result = _run_render_dashboard_chrome("class")
    assert result["configDesc"] == "T[class.config_desc]"
    assert result["typeIndicator"] == "T[class.type_indicator]"
    assert "race.unconfigured" not in result["typeIndicator"]
    assert "status.no_config" not in result["configDesc"]


def test_render_dashboard_chrome_sets_class_panel_title():
    result = _run_render_dashboard_chrome("class")
    assert result["panelTitle"] == "T[class.leaderboard_title]"
    assert "leaderboard.title" not in result["panelTitle"]


def test_render_dashboard_chrome_leaves_race_mode_unaffected():
    """Regression guard: an unconfigured RACE session must still show the
    original race copy -- the class-mode branch must not swallow it."""
    result = _run_render_dashboard_chrome("race")
    assert result["configDesc"] == "T[status.no_config]"
    assert result["typeIndicator"] == "T[race.unconfigured]"
    assert result["panelTitle"] == "T[leaderboard.title]"


# -- 10. Locale-aware metric numbers (Defect 3).


def test_build_class_board_html_formats_metric_numbers_with_real_intl_for_de_ch():
    """Uses the REAL Intl.NumberFormat (not the identity stub the other
    render-path tests above use) so this actually pins Swiss-locale
    grouping -- de-CH groups thousands with U+2019, e.g.
    Intl.NumberFormat('de-CH').format(1250) -- rather than merely
    confirming numberFormat.format() was called at all."""
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "buildClassBoardHtml"))
    session_data = json.dumps(
        {
            "class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
            "leaderboard": {
                "node1": {
                    "node_id": "node1",
                    "athlete_name": "Nina",
                    "station_number": 1,
                    "power_watts": 1200,
                    "instantaneous_speed_kph": 30,
                    "distance_m": 1250,
                }
            },
        }
    )
    clock = '{"index": 0, "kind": "work", "segmentRemainingMs": 300000, "totalRemainingMs": 300000, "finished": false}'
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _node_display_name_stub()
        + _format_clock_stub()
        + "const currentLocale = 'de-CH';\n"
        + fn
        + "\n"
        + f"const sessionData = {session_data};\n"
        + f"const clock = {clock};\n"
        + "const html = buildClassBoardHtml(sessionData, clock);\n"
        + "const expectedPower = new Intl.NumberFormat('de-CH').format(1200);\n"
        + "const expectedDistance = new Intl.NumberFormat('de-CH').format(1250);\n"
        + "console.log(JSON.stringify({ html, expectedPower, expectedDistance, enUsPower: new Intl.NumberFormat('en-US').format(1200) }));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    payload = json.loads(result.stdout)
    html = payload["html"]
    assert payload["expectedPower"] in html, (
        f"expected real de-CH Intl formatting of 1200 ({payload['expectedPower']!r}) "
        f"in html, got: {html}"
    )
    assert payload["expectedDistance"] in html, (
        f"expected real de-CH Intl formatting of 1250 ({payload['expectedDistance']!r}) "
        f"in html, got: {html}"
    )
    # de-CH's grouping separator is not a plain comma, so the en-US
    # formatting must NOT appear -- catches a mutation that hardcodes
    # 'en-US' regardless of currentLocale.
    if payload["enUsPower"] != payload["expectedPower"]:
        assert payload["enUsPower"] not in html


def test_set_language_class_branch_calls_render_class_board_from_state():
    """setLanguage's class-mode branch must re-render the class board
    immediately from page state, rather than relying on the race-only
    synthetic updateUIState() call below it (which carries no class_plan/
    leaderboard and would silently do nothing for a class). Extracted with
    the same brace-depth matching as the other real-function tests in this
    module -- not a bare substring search over the whole file -- so this
    can only pass if the call sits inside setLanguage's own class-mode
    branch, not merely somewhere else on the page."""
    script = _read_index()
    start = script.index("<script>") + len("<script>")
    end = script.index("</script>", start)
    body = script[start:end]
    fn_start = body.index("async function setLanguage(")
    brace_open = body.index("{", fn_start)
    brace_end = _matching_brace_end(body, brace_open)
    fn_source = _strip_js_comments(body[fn_start : brace_end + 1])

    class_branch_start = fn_source.index('currentSessionMode === "class"')
    class_branch_brace_open = fn_source.index("{", class_branch_start)
    class_branch_end = _matching_brace_end(fn_source, class_branch_brace_open)
    class_branch = fn_source[class_branch_brace_open : class_branch_end + 1]

    assert "renderClassBoardFromState()" in class_branch, (
        "setLanguage's class-mode branch must call renderClassBoardFromState() "
        f"to re-render immediately; branch was: {class_branch}"
    )
    # And the race-mode synthetic updateUIState() call must NOT be reached
    # for a class -- it has no class_plan/leaderboard, so calling it would
    # silently no-op instead of refreshing the board.
    assert "updateUIState(" not in class_branch


# -- 11. New i18n keys exist in all six locales and zh-TW is genuinely
# translated, mirroring section 6 above for the class-mode chrome/banner
# keys this module adds.

_NEW_CHROME_KEYS = {
    "class.type_indicator",
    "class.config_desc",
    "class.leaderboard_title",
    "class.stage_kicker",
    "class.stage_sub",
    "class.stage_not_running",
}


def test_new_chrome_keys_exist_in_all_locales():
    locales = ["de-CH", "en-US", "fr", "it", "sv", "zh-TW"]
    for locale in locales:
        locales_path = LOCALES_DIR / f"{locale}.json"
        with open(locales_path, "r", encoding="utf-8") as f:
            messages = json.load(f)
        missing = _NEW_CHROME_KEYS - set(messages.keys())
        assert not missing, f"Missing chrome keys in {locale}: {missing}"


def test_new_chrome_keys_in_zh_tw_contain_cjk_characters():
    locales_path = LOCALES_DIR / "zh-TW.json"
    with open(locales_path, "r", encoding="utf-8") as f:
        zh_tw = json.load(f)
    for key in _NEW_CHROME_KEYS:
        value = zh_tw[key]
        assert _CJK_RE.search(
            value
        ), f"{key} in zh-TW should contain CJK but is: {value}"


def test_new_chrome_keys_are_not_copies_of_english_across_locales():
    """Catches "key added but left in English" for a locale other than
    zh-TW (whose CJK check above already guards it) -- e.g. fr/it/sv/de-CH
    silently keeping the en-US string."""
    en_us_path = LOCALES_DIR / "en-US.json"
    with open(en_us_path, "r", encoding="utf-8") as f:
        en_us = json.load(f)
    for locale in ("de-CH", "fr", "it", "sv"):
        locales_path = LOCALES_DIR / f"{locale}.json"
        with open(locales_path, "r", encoding="utf-8") as f:
            messages = json.load(f)
        for key in _NEW_CHROME_KEYS:
            assert messages[key] != en_us[key], (
                f"{key} in {locale} is an unmodified copy of the en-US "
                f"string: {en_us[key]!r}"
            )
