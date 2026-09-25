"""Regression coverage for a real defect found in review: turning the Game
Admin "show live data while idle" toggle OFF did not hide the dashboard's
idle station grid.

Root cause: MqttSubscriber._maybe_broadcast_idle_telemetry returns early
when the snapshot is not visible (see test_mqtt_subscriber_idle_telemetry.py),
so once the toggle is off, no further "idle_telemetry" WS message is EVER
sent -- an already-shown panel had nothing telling it to disappear. The fix
is the state_change handler (updateUIState in index.html): every toggle
flip goes through POST /api/dashboard/idle-telemetry-visibility ->
broadcast_race_state(), which IS always delivered, so updateUIState must
itself hide the panel when idle_live_telemetry_visible is false.

Covers:
  1. shouldHideIdleTelemetry -- pure decision function.
  2. The actual `if (shouldHideIdleTelemetry(...)) { hideIdleTelemetry(); }`
     block inside updateUIState, extracted and executed for real (not a
     source-text grep) so deleting the real wiring turns this red.
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


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 1. shouldHideIdleTelemetry -- pure decision function.
# ---------------------------------------------------------------------------


def _run_should_hide(data: dict, state: str) -> bool:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "shouldHideIdleTelemetry"))
    script = (
        f"{fn}\n"
        f"console.log(JSON.stringify(shouldHideIdleTelemetry({json.dumps(data)}, "
        f"{json.dumps(state)})));"
    )
    return json.loads(_run_node(script))


def test_hides_when_toggle_is_off_even_while_idle():
    assert _run_should_hide({"idle_live_telemetry_visible": False}, "IDLE") is True


def test_hides_when_toggle_is_off_even_while_ready():
    assert _run_should_hide({"idle_live_telemetry_visible": False}, "READY") is True


def test_does_not_hide_when_toggle_is_on_and_idle():
    assert _run_should_hide({"idle_live_telemetry_visible": True}, "IDLE") is False


def test_does_not_hide_when_toggle_field_absent_and_idle():
    # Older/partial state payloads (or a settings file predating this
    # feature) must default to "on", matching RaceManager's own default.
    assert _run_should_hide({}, "IDLE") is False


def test_hides_while_running_regardless_of_toggle():
    assert _run_should_hide({"idle_live_telemetry_visible": True}, "RUNNING") is True


def test_hides_while_stopped_regardless_of_toggle():
    assert _run_should_hide({"idle_live_telemetry_visible": True}, "STOPPED") is True


def test_hides_in_class_mode_regardless_of_toggle():
    assert (
        _run_should_hide(
            {"idle_live_telemetry_visible": True, "session_mode": "class"}, "IDLE"
        )
        is True
    )


# ---------------------------------------------------------------------------
# 2. Wiring: the real `if (shouldHideIdleTelemetry(...)) { hideIdleTelemetry(); }`
#    block inside updateUIState, executed for real.
# ---------------------------------------------------------------------------


def _extract_hide_wiring_block(source: str) -> str:
    marker = "if (shouldHideIdleTelemetry(data, currentState)) {"
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _run_hide_wiring(current_state: str, data: dict) -> int:
    source = _strip_js_comments(_read_index())
    should_hide_fn = _extract_function(source, "shouldHideIdleTelemetry")
    block = _extract_hide_wiring_block(source)
    script = (
        f"let currentState = {json.dumps(current_state)};\n"
        "let hideCalls = 0;\n"
        "function hideIdleTelemetry() { hideCalls += 1; }\n"
        f"{should_hide_fn}\n"
        f"const data = {json.dumps(data)};\n"
        f"{block}\n"
        "console.log(JSON.stringify({ hideCalls }));"
    )
    return json.loads(_run_node(script))["hideCalls"]


def test_state_change_handler_hides_panel_when_toggle_turns_off():
    assert (
        _run_hide_wiring("IDLE", {"idle_live_telemetry_visible": False}) == 1
    ), "updateUIState must call hideIdleTelemetry() when idle_live_telemetry_visible is false"


def test_state_change_handler_does_not_hide_panel_while_idle_and_visible():
    assert _run_hide_wiring("IDLE", {"idle_live_telemetry_visible": True}) == 0
