"""Regression coverage for a real defect found in review: the venue-status
banner's restore-on-hide was untested end-to-end. renderIdleTelemetry hides
#race-stage-banner while idle cards show (see
test_dashboard_idle_telemetry.py); hideIdleTelemetry is supposed to bring it
back the instant the idle panel goes away. On race day, a race starting
right after the idle view was showing must not leave the banner (which
carries race state/countdown) hidden for the whole race.

This drives the REAL show -> hide path end to end:
  1. renderIdleTelemetry(...) with visible station data (the real
     "idle_telemetry" WS handler's target) -- banner hidden.
  2. The real `if (shouldHideIdleTelemetry(data, currentState)) {
     hideIdleTelemetry(); }` block inside updateUIState (the real
     "state_change" WS handler's target), with currentState = "RUNNING" --
     exactly what happens when a race starts right after the idle view was
     showing.
Then asserts the banner is visible again. Never calls hideIdleTelemetry by
name directly -- only through these two real call paths.
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


def _extract_hide_wiring_block(source: str) -> str:
    marker = "if (shouldHideIdleTelemetry(data, currentState)) {"
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def _run_show_then_hide(hide_state: str, hide_data_js: str) -> dict:
    source = _read_index()
    stripped = _strip_js_comments(source)
    pieces = [
        _strip_js_comments(_extract_function(source, "hideIdleTelemetry")),
        _strip_js_comments(_extract_function(source, "formatIdleMetricValue")),
        _strip_js_comments(_extract_function(source, "idleParticipantLabel")),
        _strip_js_comments(_extract_function(source, "isRunningEquipment")),
        _strip_js_comments(_extract_function(source, "formatTreadmillPace")),
        _strip_js_comments(_extract_const(source, "KNOWN_EQUIPMENT_TYPES_FOR_LABEL")),
        _strip_js_comments(_extract_function(source, "equipmentTypeLabelKey")),
        _strip_js_comments(_extract_function(source, "equipmentIconSvg")),
        _strip_js_comments(_extract_function(source, "idleStationIconHtml")),
        _strip_js_comments(_extract_function(source, "renderIdleStationCard")),
        _strip_js_comments(_extract_function(source, "renderIdleBestRow")),
        _strip_js_comments(_extract_function(source, "renderIdleTelemetry")),
        _strip_js_comments(_extract_const(source, "IDLE_BEST_METRIC_LABEL_KEYS")),
        _strip_js_comments(_extract_function(source, "shouldHideIdleTelemetry")),
    ]
    # The real production if-block from updateUIState -- executed for real,
    # not reimplemented.
    hide_wiring_block = _extract_hide_wiring_block(stripped)

    script = (
        # currentState starts IDLE/race so renderIdleTelemetry's own hide
        # guard does not itself refuse to show the panel.
        'let currentState = "IDLE";\n'
        'let currentSessionMode = "race";\n'
        "function t(key) { return key; }\n"
        "function escapeHtml(value) { return String(value); }\n"
        "function enterIdleRecordWall() {}\n"
        "function exitIdleRecordWall() {}\n"
        "class FakeClassList { constructor(el) { this.el = el; } "
        "add(cls) { this.el.classes.add(cls); } "
        "remove(cls) { this.el.classes.delete(cls); } "
        "contains(cls) { return this.el.classes.has(cls); } }\n"
        "function makeElement() { "
        "const el = { innerHTML: '', style: { display: '' }, classes: new Set() }; "
        "el.classList = new FakeClassList(el); "
        "return el; }\n"
        "const elements = { "
        "'idle-stations-panel': makeElement(), "
        "'idle-stations-grid': makeElement(), "
        "'idle-best-panel': makeElement(), "
        "'leaderboard-container': makeElement(), "
        "'race-stage-banner': makeElement() };\n"
        "const document = { getElementById: (id) => elements[id] || null };\n"
        + "\n".join(pieces)
        + "\n"
        # Step 1: the real "idle_telemetry" WS handler's target, with a
        # visible station -- this is what hides the banner in the first
        # place.
        + 'renderIdleTelemetry({"visible": true, "stations": [{"station_number": 1, '
        + '"instantaneous_speed_kph": 9.0, "is_stale": false}], "best": []});\n'
        + "const bannerAfterShow = elements['race-stage-banner'].style.display;\n"
        # Step 2: the real "state_change" WS handler's target -- a race
        # starting (or the 3-2-1-Go countdown reaching RUNNING) right after
        # the idle view was showing.
        + f"currentState = {json.dumps(hide_state)};\n"
        + f"const data = {hide_data_js};\n"
        + hide_wiring_block
        + "\n"
        + "console.log(JSON.stringify({\n"
        + "  bannerAfterShow,\n"
        + "  bannerAfterHide: elements['race-stage-banner'].style.display,\n"
        + "}));\n"
    )
    return json.loads(_run_node(script))


def test_banner_is_restored_when_a_race_starts_right_after_the_idle_view():
    result = _run_show_then_hide("RUNNING", '{"idle_live_telemetry_visible": true}')
    assert result["bannerAfterShow"] == "none"
    assert result["bannerAfterHide"] == ""


def test_banner_is_restored_when_the_toggle_turns_off_while_idle():
    result = _run_show_then_hide("IDLE", '{"idle_live_telemetry_visible": false}')
    assert result["bannerAfterShow"] == "none"
    assert result["bannerAfterHide"] == ""
