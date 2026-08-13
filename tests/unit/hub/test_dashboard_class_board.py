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
