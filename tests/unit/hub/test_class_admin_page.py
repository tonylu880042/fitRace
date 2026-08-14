"""Tests for the Class Admin operator console (`hub_server/static/classAdmin.html`).

Class Admin is the operator-facing page for building and running a training
class plan: an editable list of warmup/work/rest/cooldown segments, a
proportional timeline preview, read-only station status, and controls to
save the plan, start/stop/reset the class, and switch the projector into
class mode (`POST /api/session/mode {"mode": "class"}`).

Per CLAUDE.md's testability guidance, the recurring defect in this codebase
is a correct pure helper whose result never reaches the DOM, caught only by
a test that greps the source for a function name. To close that gap:

  1. Pure functions (no DOM access) are extracted out of the page's inline
     `<script>` by brace-depth matching -- the same technique used by
     tests/unit/hub/test_dashboard_class_board.py and
     tests/unit/hub/test_game_admin_race_rule_guidance.py -- and executed
     under `node -e` so real return values are asserted, not source text.
  2. The render-path helpers (buildPlanPreviewHtml, buildStationStatusHtml,
     buildPlanEditorHtml) are executed with stubbed `t`/`escapeHtml`/`Intl`
     to prove the returned HTML actually contains translated labels and
     computed values -- not just that the helper is invoked somewhere.
  3. repeatSegmentGroup (the "repeat this group x N" feature) gets dedicated
     coverage for producing a FLAT list of the expected length and order,
     since CLAUDE.md flags this as the piece most likely to be subtly wrong.

This module also covers the six-locale `classAdmin.*` i18n keys (the six
supported locales, not the two-language inline-dictionary pattern used by
gameAdmin.html/systemAdmin.html), and the gameAdmin.html "switch projector
to race mode" control added alongside it (see the bottom section) -- that
control lives in gameAdmin.html's *inline* dictionaries, per that page's
existing i18n pattern.
"""

import json
import re
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi.app import app

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"
LOCALES_DIR = (
    Path(__file__).resolve().parents[3] / "hub_server" / "infrastructure" / "locales"
)

client = TestClient(app)

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CJK_RE = re.compile(r"[一-鿿]")


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read_class_admin() -> str:
    return (STATIC_DIR / "classAdmin.html").read_text(encoding="utf-8")


def _read_game_admin() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _matching_brace_end(source: str, open_idx: int) -> int:
    """Mirrors tests/unit/hub/test_dashboard_class_board.py."""
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


_BRACKET_PAIRS = {"{": "}", "[": "]", "(": ")"}
_BRACKET_CLOSERS = {close: open_ for open_, close in _BRACKET_PAIRS.items()}


def _matching_close(source: str, open_idx: int) -> int:
    """Like _matching_brace_end, but tracks a stack of {}/[]/() so it can
    close whichever bracket kind opens at open_idx -- used to pull the
    `const DEFAULT_PLAN_ROWS = [...]` array literal out of the page, since
    _matching_brace_end only understands curly braces."""
    stack = []
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
        elif char in _BRACKET_PAIRS:
            stack.append(_BRACKET_PAIRS[char])
        elif char in _BRACKET_CLOSERS:
            if not stack or stack[-1] != char:
                raise ValueError("mismatched bracket while scanning for close")
            stack.pop()
            if not stack:
                return i
        i += 1
    raise ValueError("no matching close found")


def _extract_const(source: str, name: str) -> str:
    marker = f"const {name} = "
    start = source.index(marker)
    value_start = start + len(marker)
    close_idx = _matching_close(source, value_start)
    return source[start : close_idx + 1] + ";"


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def _t_stub() -> str:
    """Mirrors _team_rule_t_stub in test_game_admin_race_rule_guidance.py:
    plain `T[key]` when called with no params (so untouched call sites keep
    matching a bare key), but `T[key|{"name":"value"}]` when params are
    supplied -- so a test can prove a parameterised label's params actually
    reached t(), not merely that some key was passed."""
    return (
        "const t = (key, params = {}) => {\n"
        "  if (params && Object.keys(params).length) {\n"
        "    return `T[${key}|${JSON.stringify(params)}]`;\n"
        "  }\n"
        "  return `T[${key}]`;\n"
        "};\n"
    )


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


def _intl_number_format_stub() -> str:
    return "const Intl = { NumberFormat: function(locale) { return { format: (n) => String(n) }; } };\n"


# ---------------------------------------------------------------------------
# 1. formatDurationClock -- pure, mm:ss / h:mm:ss, never toLocaleTimeString.
# ---------------------------------------------------------------------------


def _run_format_duration_clock(total_seconds: int) -> str:
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "formatDurationClock"))
    script = (
        _metric_number_stub()
        + fn
        + f"\nconsole.log(formatDurationClock({total_seconds}));"
    )
    return _run_node(script)


def test_format_duration_clock_zero():
    assert _run_format_duration_clock(0) == "00:00"


def test_format_duration_clock_under_an_hour():
    assert _run_format_duration_clock(65) == "01:05"


def test_format_duration_clock_zero_pads_seconds():
    assert _run_format_duration_clock(5) == "00:05"


def test_format_duration_clock_includes_hours_past_sixty_minutes():
    # 3661s = 1h 1m 1s
    assert _run_format_duration_clock(3661) == "1:01:01"


def test_format_duration_clock_negative_clamps_to_zero():
    assert _run_format_duration_clock(-30) == "00:00"


def test_class_admin_never_uses_to_locale_time_string():
    source = _read_class_admin()
    assert "toLocaleTimeString" not in source


def test_class_admin_never_uses_twelve_hour_am_pm_formatting():
    source = _read_class_admin()
    assert "hour12" not in source
    assert re.search(r"\bAM\b", source) is None
    assert re.search(r"\bPM\b", source) is None


# ---------------------------------------------------------------------------
# 2. buildSegmentsPayload -- editor rows -> flat {segments: [...]} payload.
# ---------------------------------------------------------------------------


def _run_build_segments_payload(rows_js: str) -> dict:
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "buildSegmentsPayload"))
    script = (
        _metric_number_stub()
        + fn
        + "\n"
        + f"const rows = {rows_js};\n"
        + "console.log(JSON.stringify(buildSegmentsPayload(rows)));"
    )
    return json.loads(_run_node(script))


def test_build_segments_payload_converts_rows_to_flat_segment_list():
    rows = '[{"kind": "warmup", "durationSec": 300}, {"kind": "work", "durationSec": 1200}]'
    result = _run_build_segments_payload(rows)
    assert result == {
        "segments": [
            {"kind": "warmup", "duration_sec": 300},
            {"kind": "work", "duration_sec": 1200},
        ]
    }


def test_build_segments_payload_coerces_string_durations_to_numbers():
    rows = '[{"kind": "rest", "durationSec": "60"}]'
    result = _run_build_segments_payload(rows)
    assert result["segments"][0]["duration_sec"] == 60
    assert isinstance(result["segments"][0]["duration_sec"], int)


def test_build_segments_payload_empty_rows_is_empty_segments():
    assert _run_build_segments_payload("[]") == {"segments": []}


# ---------------------------------------------------------------------------
# 3. repeatSegmentGroup -- "repeat this group x N" must expand into a FLAT
# list of the expected length and order at edit time.
# ---------------------------------------------------------------------------


def _run_repeat_segment_group(rows_js: str, start: int, end: int, times: int) -> list:
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "repeatSegmentGroup"))
    script = (
        _metric_number_stub()
        + fn
        + "\n"
        + f"const rows = {rows_js};\n"
        + f"console.log(JSON.stringify(repeatSegmentGroup(rows, {start}, {end}, {times})));"
    )
    return json.loads(_run_node(script))


def test_repeat_segment_group_expands_contiguous_pair_three_times():
    # [warmup, work, rest, cooldown], repeat (work, rest) x3.
    rows = (
        '[{"kind": "warmup", "durationSec": 300},'
        ' {"kind": "work", "durationSec": 40},'
        ' {"kind": "rest", "durationSec": 20},'
        ' {"kind": "cooldown", "durationSec": 300}]'
    )
    result = _run_repeat_segment_group(rows, 1, 2, 3)
    kinds = [row["kind"] for row in result]
    assert kinds == [
        "warmup",
        "work",
        "rest",
        "work",
        "rest",
        "work",
        "rest",
        "cooldown",
    ]
    assert len(result) == 8


def test_repeat_segment_group_preserves_duration_values_in_every_copy():
    rows = '[{"kind": "work", "durationSec": 45}, {"kind": "rest", "durationSec": 15}]'
    result = _run_repeat_segment_group(rows, 0, 1, 2)
    assert result == [
        {"kind": "work", "durationSec": 45},
        {"kind": "rest", "durationSec": 15},
        {"kind": "work", "durationSec": 45},
        {"kind": "rest", "durationSec": 15},
    ]


def test_repeat_segment_group_single_row_group():
    rows = '[{"kind": "work", "durationSec": 30}]'
    result = _run_repeat_segment_group(rows, 0, 0, 4)
    assert [row["kind"] for row in result] == ["work"] * 4
    assert len(result) == 4


def test_repeat_segment_group_times_of_one_is_a_no_op_on_length():
    rows = (
        '[{"kind": "warmup", "durationSec": 300}, {"kind": "work", "durationSec": 40}]'
    )
    result = _run_repeat_segment_group(rows, 0, 1, 1)
    assert len(result) == 2
    assert [row["kind"] for row in result] == ["warmup", "work"]


def test_repeat_segment_group_leaves_rows_outside_the_group_untouched_and_in_place():
    rows = (
        '[{"kind": "warmup", "durationSec": 300},'
        ' {"kind": "work", "durationSec": 40},'
        ' {"kind": "cooldown", "durationSec": 300}]'
    )
    result = _run_repeat_segment_group(rows, 1, 1, 3)
    assert result[0] == {"kind": "warmup", "durationSec": 300}
    assert result[-1] == {"kind": "cooldown", "durationSec": 300}
    assert len(result) == 5  # warmup + work*3 + cooldown


# ---------------------------------------------------------------------------
# 4. computePlanSummary -- total duration and proportional timeline blocks.
# ---------------------------------------------------------------------------


def _run_compute_plan_summary(rows_js: str) -> dict:
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "computePlanSummary"))
    script = (
        _metric_number_stub()
        + fn
        + "\n"
        + f"const rows = {rows_js};\n"
        + "console.log(JSON.stringify(computePlanSummary(rows)));"
    )
    return json.loads(_run_node(script))


def test_compute_plan_summary_totals_duration_and_counts_segments():
    rows = (
        '[{"kind": "warmup", "durationSec": 300}, {"kind": "work", "durationSec": 700}]'
    )
    summary = _run_compute_plan_summary(rows)
    assert summary["totalDurationSec"] == 1000
    assert summary["segmentCount"] == 2


def test_compute_plan_summary_block_widths_are_proportional_to_duration():
    rows = (
        '[{"kind": "warmup", "durationSec": 250}, {"kind": "work", "durationSec": 750}]'
    )
    summary = _run_compute_plan_summary(rows)
    widths = [block["widthPercent"] for block in summary["blocks"]]
    assert widths == [25.0, 75.0]


def test_compute_plan_summary_empty_plan_has_zero_total_and_no_blocks():
    summary = _run_compute_plan_summary("[]")
    assert summary["totalDurationSec"] == 0
    assert summary["segmentCount"] == 0
    assert summary["blocks"] == []


# ---------------------------------------------------------------------------
# 5. mergeStationStatus -- merges station roster with live/offline health,
# read-only, sorted by station number.
# ---------------------------------------------------------------------------


def _run_merge_station_status(stations_js: str, health_js: str) -> list:
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "mergeStationStatus"))
    script = (
        fn
        + "\n"
        + f"const stations = {stations_js};\n"
        + f"const health = {health_js};\n"
        + "console.log(JSON.stringify(mergeStationStatus(stations, health)));"
    )
    return json.loads(_run_node(script))


def test_merge_station_status_marks_online_stations_live():
    stations = '{"1": {"athlete_name": "Alice", "node_display_name": "BIKE_01"}}'
    health = '[{"station_number": 1, "health": "online"}]'
    result = _run_merge_station_status(stations, health)
    assert result == [
        {
            "stationNumber": 1,
            "athleteName": "Alice",
            "machineName": "BIKE_01",
            "isLive": True,
        }
    ]


def test_merge_station_status_marks_missing_health_as_offline():
    stations = '{"2": {"athlete_name": "Bob"}}'
    result = _run_merge_station_status(stations, "[]")
    assert result[0]["isLive"] is False


def test_merge_station_status_sorted_by_station_number():
    stations = '{"3": {}, "1": {}, "2": {}}'
    result = _run_merge_station_status(stations, "[]")
    assert [row["stationNumber"] for row in result] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 6. sessionModeSwitchState -- disabled state + explanation for the "switch
# projector to class mode" control, computed in one pass (partition,
# mirroring gameAdmin's completionFieldState idiom).
# ---------------------------------------------------------------------------


def _run_session_mode_switch_state(race_state: str) -> dict:
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "sessionModeSwitchState"))
    script = f"{fn}\nconsole.log(JSON.stringify(sessionModeSwitchState({json.dumps(race_state)})));"
    return json.loads(_run_node(script))


def test_session_mode_switch_disabled_while_running():
    result = _run_session_mode_switch_state("RUNNING")
    assert result["disabled"] is True
    assert result["noteKey"] == "classAdmin.switch_mode_note_disabled"


def test_session_mode_switch_enabled_when_idle_ready_or_stopped():
    for race_state in ("IDLE", "READY", "STOPPED"):
        result = _run_session_mode_switch_state(race_state)
        assert result["disabled"] is False, race_state
        assert result["noteKey"] == "classAdmin.switch_mode_note"


# ---------------------------------------------------------------------------
# 7. Render-path: buildPlanPreviewHtml, buildStationStatusHtml,
# buildPlanEditorHtml. Executed with stubbed t/escapeHtml/Intl to prove the
# returned HTML actually contains translated labels and computed values --
# not merely that the pure helpers above are called somewhere.
# ---------------------------------------------------------------------------


def _run_build_plan_preview_html(summary_js: str) -> str:
    source = _read_class_admin()
    segment_kind_key_fn = _strip_js_comments(
        _extract_function(source, "segmentKindKey")
    )
    format_duration_fn = _strip_js_comments(
        _extract_function(source, "formatDurationClock")
    )
    fn = _strip_js_comments(_extract_function(source, "buildPlanPreviewHtml"))
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _intl_number_format_stub()
        + "const currentLocale = 'en-US';\n"
        + segment_kind_key_fn
        + "\n"
        + format_duration_fn
        + "\n"
        + fn
        + "\n"
        + f"const summary = {summary_js};\n"
        + "console.log(buildPlanPreviewHtml(summary));"
    )
    return _run_node(script)


def test_build_plan_preview_html_renders_total_duration():
    summary = '{"totalDurationSec": 125, "segmentCount": 2, "blocks": [{"kind": "work", "durationSec": 125, "widthPercent": 100}]}'
    html = _run_build_plan_preview_html(summary)
    assert "02:05" in html


def test_build_plan_preview_html_renders_translated_segment_count():
    summary = '{"totalDurationSec": 100, "segmentCount": 3, "blocks": []}'
    html = _run_build_plan_preview_html(summary)
    assert "T[classAdmin.label_segment_count|" in html
    assert '"count":"3"' in html


def test_build_plan_preview_html_renders_timeline_block_with_translated_kind_title():
    summary = '{"totalDurationSec": 60, "segmentCount": 1, "blocks": [{"kind": "rest", "durationSec": 60, "widthPercent": 100}]}'
    html = _run_build_plan_preview_html(summary)
    assert "T[class.kind.rest]" in html
    assert "timeline-rest" in html
    assert "width:100%" in html


def _run_build_station_status_html(rows_js: str) -> str:
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "buildStationStatusHtml"))
    script = (
        _t_stub()
        + _escape_html_stub()
        + fn
        + "\n"
        + f"const rows = {rows_js};\n"
        + "console.log(buildStationStatusHtml(rows));"
    )
    return _run_node(script)


def test_build_station_status_html_renders_athlete_and_machine_names():
    rows = '[{"stationNumber": 1, "athleteName": "Alice", "machineName": "BIKE_01", "isLive": true}]'
    html = _run_build_station_status_html(rows)
    assert "Alice" in html
    assert "BIKE_01" in html
    assert "T[connection.online]" in html


def test_build_station_status_html_shows_offline_label_for_dead_station():
    rows = '[{"stationNumber": 2, "athleteName": null, "machineName": null, "isLive": false}]'
    html = _run_build_station_status_html(rows)
    assert "T[connection.offline]" in html
    assert "T[classAdmin.station_no_athlete]" in html
    assert "T[classAdmin.station_no_machine]" in html


def test_build_station_status_html_empty_list_shows_empty_state():
    html = _run_build_station_status_html("[]")
    assert "T[classAdmin.no_stations]" in html


def _run_build_plan_editor_html(rows_js: str) -> str:
    source = _read_class_admin()
    segment_kind_key_fn = _strip_js_comments(
        _extract_function(source, "segmentKindKey")
    )
    fn = _strip_js_comments(_extract_function(source, "buildPlanEditorHtml"))
    script = (
        _t_stub()
        + _escape_html_stub()
        + segment_kind_key_fn
        + "\n"
        + fn
        + "\n"
        + f"const rows = {rows_js};\n"
        + "console.log(buildPlanEditorHtml(rows));"
    )
    return _run_node(script)


def test_build_plan_editor_html_marks_selected_kind_option():
    rows = '[{"kind": "work", "durationSec": 300}]'
    html = _run_build_plan_editor_html(rows)
    assert '<option value="work" selected>' in html
    assert "segment-row-work" in html
    assert 'value="300"' in html


def test_build_plan_editor_html_wires_delete_button_to_row_index():
    rows = (
        '[{"kind": "warmup", "durationSec": 300}, {"kind": "rest", "durationSec": 60}]'
    )
    html = _run_build_plan_editor_html(rows)
    assert "deleteSegmentRow(0)" in html
    assert "deleteSegmentRow(1)" in html
    assert "segment-row-rest" in html


def test_build_plan_editor_html_empty_rows_shows_empty_state():
    html = _run_build_plan_editor_html("[]")
    assert "T[classAdmin.plan_empty]" in html


# ---------------------------------------------------------------------------
# 8. syncSessionModeControl DOM wiring -- proves the note's rendered
# textContent actually changes with race state (the same "computed but
# never assigned" failure mode test_game_admin_race_rule_guidance.py guards
# against for gameAdmin's syncCompetitionFields).
# ---------------------------------------------------------------------------


def _run_sync_session_mode_control(race_state: str) -> dict:
    source = _read_class_admin()
    session_mode_switch_state_fn = _strip_js_comments(
        _extract_function(source, "sessionModeSwitchState")
    )
    sync_fn = _strip_js_comments(_extract_function(source, "syncSessionModeControl"))
    script = (
        "const mockElements = {};\n"
        "function makeEl() {\n"
        "  return { textContent: '', dataset: {}, disabled: false };\n"
        "}\n"
        "function $(id) {\n"
        "  if (!mockElements[id]) mockElements[id] = makeEl();\n"
        "  return mockElements[id];\n"
        "}\n"
        "function t(key) { return `T[${key}]`; }\n"
        "const state = { race: { state: "
        + json.dumps(race_state)
        + " } };\n"
        + session_mode_switch_state_fn
        + "\n"
        + sync_fn
        + "\n"
        "syncSessionModeControl();\n"
        "console.log(JSON.stringify({\n"
        "  disabled: mockElements['btn-switch-to-class-mode'].disabled,\n"
        "  noteText: mockElements['switch-mode-note'].textContent,\n"
        "}));\n"
    )
    return json.loads(_run_node(script))


def test_sync_session_mode_control_disables_button_and_shows_note_while_running():
    result = _run_sync_session_mode_control("RUNNING")
    assert result["disabled"] is True
    assert result["noteText"] == "T[classAdmin.switch_mode_note_disabled]"


def test_sync_session_mode_control_enables_button_and_shows_default_note_when_idle():
    result = _run_sync_session_mode_control("IDLE")
    assert result["disabled"] is False
    assert result["noteText"] == "T[classAdmin.switch_mode_note]"


# ---------------------------------------------------------------------------
# 8b. applyRaceState -- the LOAD PATH. This is the function refreshState()
# calls with every fresh GET /api/race/state reply. It must derive the
# editor rows from the reply's class_plan and push them all the way into
# the rendered DOM (plan-rows innerHTML, summary-total-duration text), not
# just compute them and leave the result unused. Falls back to
# DEFAULT_PLAN_ROWS only when class_plan is null.
# ---------------------------------------------------------------------------


def _run_apply_race_state(race_state_js: str) -> dict:
    source = _read_class_admin()
    default_plan_rows_const = _strip_js_comments(
        _extract_const(source, "DEFAULT_PLAN_ROWS")
    )
    segment_kind_key_fn = _strip_js_comments(
        _extract_function(source, "segmentKindKey")
    )
    format_duration_fn = _strip_js_comments(
        _extract_function(source, "formatDurationClock")
    )
    compute_plan_summary_fn = _strip_js_comments(
        _extract_function(source, "computePlanSummary")
    )
    build_plan_editor_html_fn = _strip_js_comments(
        _extract_function(source, "buildPlanEditorHtml")
    )
    build_plan_preview_html_fn = _strip_js_comments(
        _extract_function(source, "buildPlanPreviewHtml")
    )
    state_display_key_fn = _strip_js_comments(
        _extract_function(source, "stateDisplayKey")
    )
    session_mode_switch_state_fn = _strip_js_comments(
        _extract_function(source, "sessionModeSwitchState")
    )
    sync_session_mode_control_fn = _strip_js_comments(
        _extract_function(source, "syncSessionModeControl")
    )
    render_plan_editor_fn = _strip_js_comments(
        _extract_function(source, "renderPlanEditor")
    )
    render_plan_preview_fn = _strip_js_comments(
        _extract_function(source, "renderPlanPreview")
    )
    render_summary_state_fn = _strip_js_comments(
        _extract_function(source, "renderSummaryState")
    )
    rows_from_class_plan_fn = _strip_js_comments(
        _extract_function(source, "rowsFromClassPlan")
    )
    apply_race_state_fn = _strip_js_comments(
        _extract_function(source, "applyRaceState")
    )

    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _intl_number_format_stub()
        + "const currentLocale = 'en-US';\n"
        + "const mockElements = {};\n"
        + "function makeEl() {\n"
        + "  return { textContent: '', innerHTML: '', className: '', dataset: {}, disabled: false };\n"
        + "}\n"
        + "function $(id) {\n"
        + "  if (!mockElements[id]) mockElements[id] = makeEl();\n"
        + "  return mockElements[id];\n"
        + "}\n"
        + default_plan_rows_const
        + "\n"
        + "const state = { race: {}, rows: [] };\n"
        + segment_kind_key_fn
        + "\n"
        + format_duration_fn
        + "\n"
        + compute_plan_summary_fn
        + "\n"
        + build_plan_editor_html_fn
        + "\n"
        + build_plan_preview_html_fn
        + "\n"
        + state_display_key_fn
        + "\n"
        + session_mode_switch_state_fn
        + "\n"
        + sync_session_mode_control_fn
        + "\n"
        + render_plan_editor_fn
        + "\n"
        + render_plan_preview_fn
        + "\n"
        + render_summary_state_fn
        + "\n"
        + rows_from_class_plan_fn
        + "\n"
        + apply_race_state_fn
        + "\n"
        + f"const raceState = {race_state_js};\n"
        + "applyRaceState(raceState);\n"
        + "console.log(JSON.stringify({\n"
        + "  rows: state.rows,\n"
        + "  totalDurationText: mockElements['summary-total-duration'].textContent,\n"
        + "  planRowsHtml: mockElements['plan-rows'].innerHTML,\n"
        + "}));\n"
    )
    return json.loads(_run_node(script))


def test_apply_race_state_loads_the_saved_plan_into_the_editor_not_the_default():
    # Mirrors the reported reproduction: POST /api/class/configure with
    # segments totalling 210s (03:30), then a fresh GET /api/race/state
    # reply carrying that class_plan.
    race_state = json.dumps(
        {
            "state": "IDLE",
            "class_plan": {
                "segments": [
                    {"kind": "warmup", "duration_sec": 90},
                    {"kind": "work", "duration_sec": 45},
                    {"kind": "rest", "duration_sec": 15},
                    {"kind": "cooldown", "duration_sec": 60},
                ]
            },
        }
    )
    result = _run_apply_race_state(race_state)
    assert result["rows"] == [
        {"kind": "warmup", "durationSec": 90},
        {"kind": "work", "durationSec": 45},
        {"kind": "rest", "durationSec": 15},
        {"kind": "cooldown", "durationSec": 60},
    ]
    assert result["totalDurationText"] == "03:30"
    assert 'value="90"' in result["planRowsHtml"]
    assert 'value="45"' in result["planRowsHtml"]
    assert 'value="15"' in result["planRowsHtml"]
    assert 'value="60"' in result["planRowsHtml"]
    # The stale hardcoded default must not survive the load.
    assert 'value="300"' not in result["planRowsHtml"]
    assert 'value="1200"' not in result["planRowsHtml"]


def test_apply_race_state_null_class_plan_falls_back_to_default():
    race_state = json.dumps({"state": "IDLE", "class_plan": None})
    result = _run_apply_race_state(race_state)
    assert result["rows"] == [
        {"kind": "warmup", "durationSec": 300},
        {"kind": "work", "durationSec": 1200},
        {"kind": "cooldown", "durationSec": 300},
    ]
    assert result["totalDurationText"] == "30:00"
    assert 'value="300"' in result["planRowsHtml"]
    assert 'value="1200"' in result["planRowsHtml"]


def test_apply_race_state_missing_class_plan_key_falls_back_to_default():
    # GET /api/race/state before any class has ever been configured may omit
    # the key entirely rather than sending it as null.
    race_state = json.dumps({"state": "IDLE"})
    result = _run_apply_race_state(race_state)
    assert result["rows"] == [
        {"kind": "warmup", "durationSec": 300},
        {"kind": "work", "durationSec": 1200},
        {"kind": "cooldown", "durationSec": 300},
    ]


# ---------------------------------------------------------------------------
# 8c. rowsFromClassPlan -- the pure conversion at the core of the load path.
# ---------------------------------------------------------------------------


def _run_rows_from_class_plan(class_plan_js: str) -> list:
    source = _read_class_admin()
    default_plan_rows_const = _strip_js_comments(
        _extract_const(source, "DEFAULT_PLAN_ROWS")
    )
    fn = _strip_js_comments(_extract_function(source, "rowsFromClassPlan"))
    script = (
        _metric_number_stub()
        + default_plan_rows_const
        + "\n"
        + fn
        + "\n"
        + f"const classPlan = {class_plan_js};\n"
        + "console.log(JSON.stringify(rowsFromClassPlan(classPlan)));"
    )
    return json.loads(_run_node(script))


def test_rows_from_class_plan_converts_segments_to_rows():
    class_plan = '{"segments": [{"kind": "work", "duration_sec": 45}, {"kind": "rest", "duration_sec": 15}]}'
    result = _run_rows_from_class_plan(class_plan)
    assert result == [
        {"kind": "work", "durationSec": 45},
        {"kind": "rest", "durationSec": 15},
    ]


def test_rows_from_class_plan_null_yields_default_rows():
    result = _run_rows_from_class_plan("null")
    assert result == [
        {"kind": "warmup", "durationSec": 300},
        {"kind": "work", "durationSec": 1200},
        {"kind": "cooldown", "durationSec": 300},
    ]


def test_rows_from_class_plan_empty_segments_yields_default_rows():
    result = _run_rows_from_class_plan('{"segments": []}')
    assert result == [
        {"kind": "warmup", "durationSec": 300},
        {"kind": "work", "durationSec": 1200},
        {"kind": "cooldown", "durationSec": 300},
    ]


# ---------------------------------------------------------------------------
# 9. i18n: classAdmin.* keys exist symmetrically in all six locales, zh-TW
# carries genuine CJK translations, and the parameterised key's placeholder
# survives translation.
# ---------------------------------------------------------------------------

CLASS_ADMIN_KEYS = [
    "classAdmin.subtitle",
    "classAdmin.nav_dashboard",
    "classAdmin.nav_game_admin",
    "classAdmin.nav_system_admin",
    "classAdmin.nav_operator_unlock",
    "classAdmin.tile_state",
    "classAdmin.tile_stations",
    "classAdmin.tile_total_duration",
    "classAdmin.panel_plan_editor",
    "classAdmin.panel_plan_preview",
    "classAdmin.panel_station_status",
    "classAdmin.label_segment_kind",
    "classAdmin.label_segment_duration",
    "classAdmin.btn_add_segment",
    "classAdmin.btn_delete_segment",
    "classAdmin.btn_repeat_group",
    "classAdmin.label_repeat_times",
    "classAdmin.label_repeat_to",
    "classAdmin.label_segment_count",
    "classAdmin.plan_empty",
    "classAdmin.station_col_number",
    "classAdmin.station_col_athlete",
    "classAdmin.station_col_machine",
    "classAdmin.station_no_athlete",
    "classAdmin.station_no_machine",
    "classAdmin.no_stations",
    "classAdmin.btn_save_plan",
    "classAdmin.btn_start_class",
    "classAdmin.btn_stop_class",
    "classAdmin.btn_reset_class",
    "classAdmin.btn_switch_to_class_mode",
    "classAdmin.switch_mode_note",
    "classAdmin.switch_mode_note_disabled",
    "classAdmin.guidance_auto_advance",
    "classAdmin.guidance_manual_stop",
    "classAdmin.message_plan_saved",
    "classAdmin.message_class_started",
    "classAdmin.message_class_stopped",
    "classAdmin.message_class_reset",
    "classAdmin.message_mode_switched",
    "classAdmin.confirm_reset",
    "classAdmin.login_title",
    "classAdmin.login_description",
    "classAdmin.label_access_code",
    "classAdmin.btn_save",
    "classAdmin.btn_cancel",
    "classAdmin.error_generic",
]

LOCALES = ["de-CH", "en-US", "fr", "it", "sv", "zh-TW"]


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_all_class_admin_keys_exist_in_every_locale():
    for locale in LOCALES:
        messages = _load_locale(locale)
        missing = set(CLASS_ADMIN_KEYS) - set(messages.keys())
        assert not missing, f"Missing classAdmin keys in {locale}: {missing}"


def test_class_admin_keys_in_zh_tw_contain_cjk_characters():
    zh_tw = _load_locale("zh-TW")
    for key in CLASS_ADMIN_KEYS:
        value = zh_tw[key]
        assert _CJK_RE.search(value), f"{key} in zh-TW has no CJK character: {value!r}"


def test_class_admin_keys_differ_from_english_in_every_other_locale():
    en_us = _load_locale("en-US")
    for locale in LOCALES:
        if locale == "en-US":
            continue
        messages = _load_locale(locale)
        for key in CLASS_ADMIN_KEYS:
            assert messages[key] != en_us[key], (
                f"{key} in {locale} is untranslated (identical to en-US): "
                f"{messages[key]!r}"
            )


def test_class_admin_segment_count_placeholder_present_and_consistent_across_locales():
    en_us_value = _load_locale("en-US")["classAdmin.label_segment_count"]
    assert "{count}" in en_us_value
    for locale in LOCALES:
        value = _load_locale(locale)["classAdmin.label_segment_count"]
        assert "{count}" in value, f"{locale} lost the {{count}} placeholder: {value!r}"


# ---------------------------------------------------------------------------
# 10. Route + page content.
# ---------------------------------------------------------------------------


def test_class_admin_route_redirects_to_static_page():
    response = client.get("/classAdmin", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/static/classAdmin.html"


def test_class_admin_static_page_serves_and_has_expected_controls():
    response = client.get("/static/classAdmin.html")
    assert response.status_code == 200
    body = response.text
    assert 'id="plan-rows"' in body
    assert 'id="plan-preview"' in body
    assert 'id="station-list"' in body
    assert 'id="btn-save-plan"' in body
    assert 'id="btn-start-class"' in body
    assert 'id="btn-stop-class"' in body
    assert 'id="btn-reset-class"' in body
    assert 'id="btn-switch-to-class-mode"' in body
    assert "/api/class/configure" in body
    assert "/api/session/mode" in body
    assert '"mode": "class"' in body or 'mode: "class"' in body


def test_class_admin_page_fetches_locales_via_api_not_inline_dictionaries():
    """classAdmin.html must use the six-locale /api/locales system like
    index.html, NOT the inline `const dictionaries = {...}` pattern used by
    gameAdmin.html/systemAdmin.html."""
    body = _read_class_admin()
    assert "/api/locales/" in body
    assert "const dictionaries" not in body


def test_class_admin_page_never_hardcodes_admin_token_header_name_wrong():
    body = _read_class_admin()
    assert "X-FitRace-Admin-Token" in body


def test_repeat_to_label_routes_through_i18n_not_hardcoded_english():
    """The repeat-group toolbar's "to row #" label used to render a literal
    English string with no data-i18n attribute at all, so it stayed English
    in every locale. It must now carry a data-i18n key like every other
    label on the page."""
    body = _read_class_admin()
    assert '<label for="repeat-to">To row #</label>' not in body
    assert 'data-i18n="classAdmin.label_repeat_to"' in body


# ---------------------------------------------------------------------------
# 11. gameAdmin.html "switch projector to race mode" control (commit 2).
# Lives here, not in a second new test file, because
# tests/unit/hub/test_class_admin_page.py is the only new test file this
# task is permitted to create. gameAdmin uses inline en-US/zh-TW
# dictionaries (not the six-locale JSON files), so these assertions follow
# tests/unit/hub/test_game_admin_race_rule_guidance.py's extraction pattern
# instead of the locale-file pattern used above.
# ---------------------------------------------------------------------------


def _game_admin_stripped_script() -> str:
    source = _read_game_admin()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _run_game_admin_session_mode_switch_state(race_state: str) -> dict:
    source = _game_admin_stripped_script()
    fn = _extract_function(source, "sessionModeSwitchState")
    script = f"{fn}\nconsole.log(JSON.stringify(sessionModeSwitchState({json.dumps(race_state)})));"
    return json.loads(_run_node(script))


def test_game_admin_session_mode_switch_disabled_while_running():
    result = _run_game_admin_session_mode_switch_state("RUNNING")
    assert result["disabled"] is True


def test_game_admin_session_mode_switch_enabled_otherwise():
    for race_state in ("IDLE", "READY", "STOPPED"):
        result = _run_game_admin_session_mode_switch_state(race_state)
        assert result["disabled"] is False, race_state


def _run_game_admin_sync_session_mode_control(race_state: str) -> dict:
    source = _game_admin_stripped_script()
    session_mode_switch_state_fn = _extract_function(source, "sessionModeSwitchState")
    sync_fn = _extract_function(source, "syncSessionModeControl")
    script = (
        "const mockElements = {};\n"
        "function makeEl() {\n"
        "  return { textContent: '', dataset: {}, disabled: false };\n"
        "}\n"
        "function $(id) {\n"
        "  if (!mockElements[id]) mockElements[id] = makeEl();\n"
        "  return mockElements[id];\n"
        "}\n"
        "function t(key) { return `T[${key}]`; }\n"
        "const state = { race: "
        + json.dumps({"state": race_state})
        + " };\n"
        + session_mode_switch_state_fn
        + "\n"
        + sync_fn
        + "\n"
        "syncSessionModeControl();\n"
        "console.log(JSON.stringify({\n"
        "  disabled: mockElements['btn-switch-to-race-mode'].disabled,\n"
        "  noteText: mockElements['switch-to-race-mode-note'].textContent,\n"
        "}));\n"
    )
    return json.loads(_run_node(script))


def test_game_admin_sync_session_mode_control_renders_disabled_note_while_running():
    result = _run_game_admin_sync_session_mode_control("RUNNING")
    assert result["disabled"] is True
    assert result["noteText"] == "T[text.switch_to_race_mode_note_disabled]"


def test_game_admin_sync_session_mode_control_renders_default_note_when_idle():
    result = _run_game_admin_sync_session_mode_control("IDLE")
    assert result["disabled"] is False
    assert result["noteText"] == "T[text.switch_to_race_mode_note]"


def test_game_admin_has_switch_to_race_mode_button_and_calls_session_mode_api():
    source = _read_game_admin()
    assert 'id="btn-switch-to-race-mode"' in source
    assert "/api/session/mode" in source
    assert '"mode": "race"' in source or 'mode: "race"' in source


GAME_ADMIN_NEW_KEYS = [
    "button.switch_to_race_mode",
    "text.switch_to_race_mode_note",
    "text.switch_to_race_mode_note_disabled",
    "message.mode_switched_to_race",
]


def _load_game_admin_dictionaries() -> dict:
    source = _read_game_admin()
    const_start = source.index("const dictionaries = {")
    const_open = source.index("{", const_start)
    const_close = _matching_brace_end(source, const_open)

    zh_marker = 'dictionaries["zh-TW"] = {'
    zh_start = source.index(zh_marker, const_close)
    zh_open = source.index("{", zh_start)
    zh_close = _matching_brace_end(source, zh_open)

    js = source[const_start : zh_close + 1] + ";"
    js += "\nconsole.log(JSON.stringify(dictionaries));"
    result = subprocess.run(
        ["node", "-e", js], capture_output=True, text=True, timeout=5
    )
    assert (
        result.returncode == 0
    ), f"node failed to evaluate dictionaries: {result.stderr}"
    return json.loads(result.stdout)


def test_game_admin_new_keys_exist_in_both_inline_dictionaries():
    dictionaries = _load_game_admin_dictionaries()
    en = dictionaries["en-US"]
    zh = dictionaries["zh-TW"]
    for key in GAME_ADMIN_NEW_KEYS:
        assert key in en, f"{key} missing from gameAdmin.html en-US dictionary"
        assert key in zh, f"{key} missing from gameAdmin.html zh-TW dictionary"
        assert en[key], f"{key} has an empty en-US value"
        assert zh[key] != en[key], f"{key} zh-TW value is untranslated"
        assert _CJK_RE.search(
            zh[key]
        ), f"{key} zh-TW value has no CJK character: {zh[key]!r}"


def test_game_admin_live_presentation_block_contains_the_new_control():
    """The control belongs in the existing Live Presentation block -- the
    'what the projector shows' area -- next to the leaderboard-view
    control, per CLAUDE.md's page-responsibility guidance."""
    source = _read_game_admin()
    live_block_start = source.index('id="live-block"')
    live_block_end = source.index('id="local-block"', live_block_start)
    live_block_source = source[live_block_start:live_block_end]
    assert 'id="btn-switch-to-race-mode"' in live_block_source
