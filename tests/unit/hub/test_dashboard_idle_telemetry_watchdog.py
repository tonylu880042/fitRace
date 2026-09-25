"""Regression coverage for a real defect found in review: if every device
stops sending telemetry (Wi-Fi drop, edges powered off, ...), no further
"idle_telemetry" WS broadcast ever arrives (broadcasts are only triggered by
incoming samples -- see MqttSubscriber._handle_telemetry), so the dashboard
kept showing the last live-looking numbers forever. `is_stale` is only
computed at broadcast time on the hub, so the hub itself has no way to tell
the dashboard "this went stale" without a new sample to attach that flag to.

Fix: a dashboard-side watchdog. If more than ~5s pass since the last
idle_telemetry message while the panel is showing, every station card is
forced into the waiting state in place -- the panel stays up (per the
review's "don't hide the panel" requirement), only the numbers stop looking
live. The next real message clears/replaces it as normal.

Covers:
  1. shouldForceIdleStale -- pure decision function.
  2. markIdleStationsStale -- rewrites the grid to the waiting state for
     every last-known station, executed for real against a DOM stub.
  3. checkIdleTelemetryWatchdog -- the wiring the setInterval calls.
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
# 1. shouldForceIdleStale -- pure decision function.
# ---------------------------------------------------------------------------


def _run_should_force(last_received_at_ms, now_ms, panel_shown) -> bool:
    source = _read_index()
    const = _strip_js_comments(
        _extract_const(source, "IDLE_TELEMETRY_STALE_WATCHDOG_MS")
    )
    fn = _strip_js_comments(_extract_function(source, "shouldForceIdleStale"))
    script = (
        f"{const}\n{fn}\n"
        f"console.log(JSON.stringify(shouldForceIdleStale({json.dumps(last_received_at_ms)}, "
        f"{json.dumps(now_ms)}, {json.dumps(panel_shown)})));"
    )
    return json.loads(_run_node(script))


def test_does_not_force_stale_before_the_threshold():
    assert _run_should_force(1000, 1000 + 4000, True) is False


def test_forces_stale_after_the_threshold():
    assert _run_should_force(1000, 1000 + 6000, True) is True


def test_does_not_force_stale_when_panel_not_shown():
    # Nothing to freeze if the panel isn't up -- e.g. RUNNING, toggle off,
    # or simply no idle telemetry to show at all.
    assert _run_should_force(1000, 1000 + 6000, False) is False


def test_does_not_force_stale_before_any_message_has_ever_arrived():
    assert _run_should_force(None, 999_999, True) is False


# ---------------------------------------------------------------------------
# 2 & 3. markIdleStationsStale / checkIdleTelemetryWatchdog -- wiring,
#    executed for real against a DOM stub.
# ---------------------------------------------------------------------------


def _run_watchdog_check(
    last_received_at_ms, now_offset_ms, panel_has_show_class, last_stations_js
):
    source = _read_index()
    pieces = [
        _strip_js_comments(_extract_const(source, "IDLE_TELEMETRY_STALE_WATCHDOG_MS")),
        _strip_js_comments(_extract_function(source, "shouldForceIdleStale")),
        _strip_js_comments(_extract_function(source, "formatIdleMetricValue")),
        _strip_js_comments(_extract_function(source, "idleParticipantLabel")),
        _strip_js_comments(_extract_function(source, "isRunningEquipment")),
        _strip_js_comments(_extract_function(source, "formatTreadmillPace")),
        _strip_js_comments(_extract_function(source, "renderIdleStationCard")),
        _strip_js_comments(_extract_function(source, "markIdleStationsStale")),
        _strip_js_comments(_extract_function(source, "checkIdleTelemetryWatchdog")),
    ]
    script = (
        f"function t(key) {{ return key; }}\n"
        f"function escapeHtml(value) {{ return String(value); }}\n"
        f"let lastIdleTelemetryReceivedAtMs = {json.dumps(last_received_at_ms)};\n"
        f"let lastIdleTelemetryStations = {last_stations_js};\n"
        "const NOW_MS = "
        + json.dumps(
            (last_received_at_ms or 0) + now_offset_ms
            if last_received_at_ms is not None
            else now_offset_ms
        )
        + ";\n"
        "Date.now = () => NOW_MS;\n"
        "const gridEl = { innerHTML: '' };\n"
        f"const panelEl = {{ classes: new Set({json.dumps(['show'] if panel_has_show_class else [])}) }};\n"
        "panelEl.classList = { contains: (cls) => panelEl.classes.has(cls) };\n"
        "const elements = { 'idle-stations-panel': panelEl, 'idle-stations-grid': gridEl };\n"
        "const document = { getElementById: (id) => elements[id] || null };\n"
        + "\n".join(pieces)
        + "\n"
        + "checkIdleTelemetryWatchdog();\n"
        + "console.log(JSON.stringify({ gridHtml: gridEl.innerHTML }));"
    )
    return json.loads(_run_node(script))


def test_watchdog_forces_all_known_stations_stale_after_silence():
    result = _run_watchdog_check(
        last_received_at_ms=1000,
        now_offset_ms=6000,
        panel_has_show_class=True,
        last_stations_js=(
            '[{"station_number": 1, '
            '"instantaneous_speed_kph": 9.0, "is_stale": false}]'
        ),
    )
    assert "idle-station-waiting-label" in result["gridHtml"]
    assert "9.0" not in result["gridHtml"]
    assert "stations.station 1" in result["gridHtml"]


def test_watchdog_does_not_touch_grid_before_the_threshold():
    result = _run_watchdog_check(
        last_received_at_ms=1000,
        now_offset_ms=2000,
        panel_has_show_class=True,
        last_stations_js=(
            '[{"station_number": 1, '
            '"instantaneous_speed_kph": 9.0, "is_stale": false}]'
        ),
    )
    assert result["gridHtml"] == ""


def test_watchdog_does_not_act_when_panel_is_not_shown():
    result = _run_watchdog_check(
        last_received_at_ms=1000,
        now_offset_ms=6000,
        panel_has_show_class=False,
        last_stations_js=(
            '[{"station_number": 1, '
            '"instantaneous_speed_kph": 9.0, "is_stale": false}]'
        ),
    )
    assert result["gridHtml"] == ""


# ---------------------------------------------------------------------------
# 4. End-to-end through the real message path: handleIdleTelemetryMessage ->
#    ensureIdleTelemetryWatchdog -> window.setInterval(checkIdleTelemetryWatchdog).
#
# The tests above call checkIdleTelemetryWatchdog directly, which cannot
# catch a broken ensureIdleTelemetryWatchdog (e.g. one that never actually
# calls window.setInterval, so the watchdog is wired up in source but never
# runs in the real page). This harness never calls checkIdleTelemetryWatchdog
# by name at all -- it stubs window.setInterval to CAPTURE whatever callback
# handleIdleTelemetryMessage's own call chain hands it, advances a
# controllable Date.now() past the stale threshold with no second message,
# and then invokes that captured callback exactly as a real browser's timer
# would. If ensureIdleTelemetryWatchdog is ever changed to skip calling
# window.setInterval, no callback is captured and this fails.
# ---------------------------------------------------------------------------


def _run_full_message_path_watchdog():
    source = _read_index()
    pieces = [
        _strip_js_comments(_extract_const(source, "IDLE_TELEMETRY_STALE_WATCHDOG_MS")),
        _strip_js_comments(
            _extract_const(source, "IDLE_TELEMETRY_WATCHDOG_INTERVAL_MS")
        ),
        _strip_js_comments(_extract_const(source, "IDLE_BEST_METRIC_LABEL_KEYS")),
        _strip_js_comments(_extract_function(source, "hideIdleTelemetry")),
        _strip_js_comments(_extract_function(source, "formatIdleMetricValue")),
        _strip_js_comments(_extract_function(source, "idleParticipantLabel")),
        _strip_js_comments(_extract_function(source, "isRunningEquipment")),
        _strip_js_comments(_extract_function(source, "formatTreadmillPace")),
        _strip_js_comments(_extract_function(source, "renderIdleStationCard")),
        _strip_js_comments(_extract_function(source, "renderIdleBestRow")),
        _strip_js_comments(_extract_function(source, "renderIdleTelemetry")),
        _strip_js_comments(_extract_function(source, "shouldForceIdleStale")),
        _strip_js_comments(_extract_function(source, "markIdleStationsStale")),
        _strip_js_comments(_extract_function(source, "checkIdleTelemetryWatchdog")),
        _strip_js_comments(_extract_function(source, "ensureIdleTelemetryWatchdog")),
        _strip_js_comments(_extract_function(source, "handleIdleTelemetryMessage")),
    ]
    script = (
        # Race-state globals renderIdleTelemetry reads: IDLE + race mode, so
        # the message is actually rendered rather than hidden.
        'let currentState = "IDLE";\n'
        'let currentSessionMode = "race";\n'
        "let lastIdleTelemetryStations = null;\n"
        "let lastIdleTelemetryReceivedAtMs = null;\n"
        "let idleTelemetryWatchdogTimer = null;\n"
        "function t(key) { return key; }\n"
        "function escapeHtml(value) { return String(value); }\n"
        "function enterIdleRecordWall() {}\n"
        "function exitIdleRecordWall() {}\n"
        # Controllable clock -- the production code calls Date.now(), never
        # a locally-scoped variable, so overriding the global is what makes
        # this genuinely simulate time passing with no new message.
        "let nowMs = 1000;\n"
        "Date.now = () => nowMs;\n"
        # window.setInterval stub: records every (callback, delay) call so
        # the test can assert one was actually registered, then lets the
        # test fire it manually -- exactly the real callback function
        # object handleIdleTelemetryMessage's call chain produced, not a
        # reimplementation.
        "const setIntervalCalls = [];\n"
        "const window = { setInterval: (fn, delay) => { setIntervalCalls.push({ fn, delay }); return setIntervalCalls.length; } };\n"
        "const gridEl = { innerHTML: '' };\n"
        "const bestPanelEl = { innerHTML: '', style: { display: '' } };\n"
        "const leaderboardEl = { style: { display: '' } };\n"
        "const panelEl = { classes: new Set() };\n"
        "panelEl.classList = { add: (cls) => panelEl.classes.add(cls), remove: (cls) => panelEl.classes.delete(cls), contains: (cls) => panelEl.classes.has(cls) };\n"
        "const elements = { 'idle-stations-panel': panelEl, 'idle-stations-grid': gridEl, 'idle-best-panel': bestPanelEl, 'leaderboard-container': leaderboardEl };\n"
        "const document = { getElementById: (id) => elements[id] || null };\n"
        + "\n".join(pieces)
        + "\n"
        # Step 1: a real "idle_telemetry" WS message arrives with one live
        # station -- this is the exact call the WS onmessage handler makes.
        + "handleIdleTelemetryMessage({ visible: true, stations: ["
        + "  { station_number: 1, instantaneous_speed_kph: 9.0, power_watts: 100, cadence_rpm: 150, heart_rate_bpm: 120, is_stale: false }"
        + "], best: [] });\n"
        + "const gridAfterFirstMessage = gridEl.innerHTML;\n"
        # Step 2: time passes well beyond the stale threshold, and no
        # second message ever arrives.
        + "nowMs += 6000;\n"
        # Step 3: fire whatever callback window.setInterval was actually
        # given -- if ensureIdleTelemetryWatchdog never called
        # window.setInterval, setIntervalCalls is empty and this throws,
        # which is exactly the failure this test exists to produce.
        + "setIntervalCalls[0].fn();\n"
        + "console.log(JSON.stringify({\n"
        + "  setIntervalCallCount: setIntervalCalls.length,\n"
        + "  gridAfterFirstMessage,\n"
        + "  gridAfterWatchdogFires: gridEl.innerHTML,\n"
        + "}));\n"
    )
    return json.loads(_run_node(script))


def test_watchdog_actually_runs_through_the_real_message_and_timer_wiring():
    result = _run_full_message_path_watchdog()
    # A watchdog was genuinely registered with window.setInterval as a
    # side effect of the real WS message handler -- not skipped.
    assert result["setIntervalCallCount"] == 1
    # Before any silence, the grid shows real live numbers.
    assert "9.0" in result["gridAfterFirstMessage"]
    assert "idle-station-waiting-label" not in result["gridAfterFirstMessage"]
    # After the registered timer callback fires with no second message
    # having arrived, the card is forced into the waiting state.
    assert "idle-station-waiting-label" in result["gridAfterWatchdogFires"]
    assert "9.0" not in result["gridAfterWatchdogFires"]
