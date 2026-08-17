"""Regression tests for the class-plan editor being wiped mid-typing by the
four-second background poll (`hub_server/static/classAdmin.html`).

Reported from the venue floor: a coach types a target-watts value into a
plan-editor row, and within a few seconds it is replaced by the saved
plan's old value. Root cause: applyRaceState() -- the single entry point
for every GET /api/race/state reply, used by BOTH the very first load and
scheduleRefresh()'s 4s poll -- used to unconditionally rebuild
state.rows from the server's class_plan and re-render the editor. A poll
landing mid-edit clobbered whatever the coach had just typed.

The fix adds a state.rowsDirty flag. Row mutation handlers (e.g.
updateSegmentTargetWatts, the handler wired to the watts input's
oninput/onchange) set it. applyRaceState only rebuilds state.rows /
plan-rows while the flag is false; the plan preview and summary tiles
still always track the server. loadRaceStateAndRows() clears the flag
and reapplies the state -- used at the points where the server should
legitimately regain authority: after a successful plan save and after a
reset.

This file exercises the real functions (extracted from the page by the
same brace-depth technique tests/unit/hub/test_class_admin_page.py and
tests/unit/hub/test_class_admin_target_watts.py use) under `node -e`
against a stubbed DOM, and asserts on actual state / rendered HTML -- not
on source text.
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


def _read_class_admin() -> str:
    return (STATIC_DIR / "classAdmin.html").read_text(encoding="utf-8")


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


_BRACKET_PAIRS = {"{": "}", "[": "]", "(": ")"}
_BRACKET_CLOSERS = {close: open_ for open_, close in _BRACKET_PAIRS.items()}


def _matching_close(source: str, open_idx: int) -> int:
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


# Deliberately paren-aware (unlike the naive "first { after the marker"
# version other test files in this directory use): a couple of the
# functions this file needs to extract for the end-to-end poll/action
# tests below -- fetchJson(url, options = {}), adminHeaders(extra = {})
# -- take a default OBJECT-LITERAL parameter. The naive version finds the
# "{}" inside the parameter list itself and returns a truncated,
# unparseable fragment. This walks past the matching close paren of the
# parameter list first, so the "{" it then finds is genuinely the
# function body.
def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    paren_open = start + len(marker) - 1
    paren_close = _matching_close(source, paren_open)
    brace_open = source.index("{", paren_close)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


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
# Common harness: pulls every real function this scenario touches straight
# out of the page, plus a minimal $()/mockElements DOM. `renderAll` here is
# a local stand-in wired only to the render functions this file cares about
# (renderPlanEditor / renderPlanPreview / renderSummaryState) -- the real
# renderAll also drives station status, class history and i18n, which are
# unrelated to the dirty-flag mechanism under test.
# ---------------------------------------------------------------------------


def _run_scenario(steps_js: str) -> dict:
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
    load_race_state_and_rows_fn = _strip_js_comments(
        _extract_function(source, "loadRaceStateAndRows")
    )
    update_segment_target_watts_fn = _strip_js_comments(
        _extract_function(source, "updateSegmentTargetWatts")
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
        + "const state = { race: {}, rows: [], rowsDirty: false };\n"
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
        + load_race_state_and_rows_fn
        + "\n"
        + update_segment_target_watts_fn
        + "\n"
        + "function renderAll() {\n"
        + "  renderPlanEditor();\n"
        + "  renderPlanPreview();\n"
        + "  renderSummaryState();\n"
        + "}\n"
        + steps_js
        + "\n"
        + "console.log(JSON.stringify({\n"
        + "  rows: state.rows,\n"
        + "  rowsDirty: state.rowsDirty,\n"
        + "  planRowsHtml: mockElements['plan-rows'].innerHTML,\n"
        + "  planPreviewHtml: mockElements['plan-preview'].innerHTML,\n"
        + "  totalDurationText: mockElements['summary-total-duration'].textContent,\n"
        + "  summaryStateText: mockElements['summary-state'].textContent,\n"
        + "}));\n"
    )
    return json.loads(_run_node(script))


PLAN_A = json.dumps(
    {
        "state": "IDLE",
        "class_plan": {
            "segments": [
                {"kind": "warmup", "duration_sec": 90},
                {"kind": "work", "duration_sec": 300, "target_watts": 150},
            ]
        },
    }
)

PLAN_B = json.dumps(
    {
        "state": "RUNNING",
        "class_plan": {
            "segments": [
                {"kind": "warmup", "duration_sec": 600},
                {"kind": "work", "duration_sec": 1200, "target_watts": 999},
            ]
        },
    }
)


# ---------------------------------------------------------------------------
# 1. First load shows the saved plan.
# ---------------------------------------------------------------------------


def test_first_load_shows_the_saved_plan():
    result = _run_scenario(f"applyRaceState({PLAN_A});\n")
    assert result["rows"] == [
        {"kind": "warmup", "durationSec": 90},
        {"kind": "work", "durationSec": 300, "targetWatts": 150},
    ]
    assert 'value="150"' in result["planRowsHtml"]


# ---------------------------------------------------------------------------
# 2. Untouched console: a poll with no prior edits still tracks the hub.
# ---------------------------------------------------------------------------


def test_poll_updates_rows_when_editor_has_not_been_touched():
    result = _run_scenario(
        f"applyRaceState({PLAN_A});\n" f"applyRaceState({PLAN_B});\n"
    )
    assert result["rowsDirty"] is False
    assert result["rows"] == [
        {"kind": "warmup", "durationSec": 600},
        {"kind": "work", "durationSec": 1200, "targetWatts": 999},
    ]
    assert 'value="999"' in result["planRowsHtml"]


# ---------------------------------------------------------------------------
# 3. The reported bug: a typed value survives a poll landing mid-edit, and
# the rendered rows are genuinely not rebuilt (the coach's in-progress
# 250 stays on screen, the server's stale 150 -- or the newer poll's 999 --
# never overwrites it).
# ---------------------------------------------------------------------------


def test_typed_value_survives_a_poll_that_lands_mid_edit():
    result = _run_scenario(
        f"applyRaceState({PLAN_A});\n"
        # The coach is mid-keystroke on row 1's watts input.
        "updateSegmentTargetWatts(1, '250');\n"
        # The 4s poll lands with a state carrying a completely different
        # saved plan.
        f"applyRaceState({PLAN_B});\n"
    )
    assert result["rowsDirty"] is True
    assert result["rows"][1]["targetWatts"] == 250
    assert 'value="250"' in result["planRowsHtml"]
    assert 'value="999"' not in result["planRowsHtml"]


# ---------------------------------------------------------------------------
# 4. The poll must keep updating the parts that are NOT the frozen editor
# rows -- proving the fix does not work by disabling or slowing the
# refresh. renderSummaryState() reads state.race directly (never
# state.rows), so the race-state pill must move on to PLAN_B's RUNNING
# even while the rows stay frozen on the coach's edit. The plan-preview
# timeline is derived from state.rows on purpose (it previews what is
# about to be saved), so it correctly stays on the frozen 06:30 rather
# than jumping to the server's unrelated PLAN_B duration.
# ---------------------------------------------------------------------------


def test_poll_still_updates_summary_state_tile_while_rows_are_frozen():
    result = _run_scenario(
        f"applyRaceState({PLAN_A});\n"
        "updateSegmentTargetWatts(1, '250');\n"
        f"applyRaceState({PLAN_B});\n"
    )
    # Rows -- and the preview derived from them -- stayed frozen on the
    # coach's edit (PLAN_A's 90+300=390s, not PLAN_B's 1800s).
    assert 'value="250"' in result["planRowsHtml"]
    assert result["totalDurationText"] == "06:30"
    # But the summary tile, which reads state.race directly, moved on to
    # PLAN_B's RUNNING -- proving the poll itself was not disabled.
    assert result["summaryStateText"] == "T[state.running]"


# ---------------------------------------------------------------------------
# 5. A successful save (or a reset) makes the server authoritative again:
# the dirty flag clears and the editor reflects exactly what the server
# now reports -- this is the load-side counterpart to POST
# /api/class/configure and POST /api/race/reset calling
# loadRaceStateAndRows() instead of a bare applyRaceState()/state.race
# assignment.
# ---------------------------------------------------------------------------


def test_explicit_save_reload_clears_dirty_and_restores_server_authority():
    result = _run_scenario(
        f"applyRaceState({PLAN_A});\n"
        "updateSegmentTargetWatts(1, '250');\n"
        # Server echoes back the saved plan after POST /api/class/configure.
        f"loadRaceStateAndRows({PLAN_B});\n"
    )
    assert result["rowsDirty"] is False
    assert result["rows"] == [
        {"kind": "warmup", "durationSec": 600},
        {"kind": "work", "durationSec": 1200, "targetWatts": 999},
    ]
    assert 'value="999"' in result["planRowsHtml"]
    assert 'value="250"' not in result["planRowsHtml"]


def test_a_poll_after_the_reload_can_still_update_rows_again():
    # loadRaceStateAndRows must not just clear the flag once and leave the
    # editor stuck -- a subsequent ordinary poll (applyRaceState) must be
    # able to move the rows again, exactly like a fresh console.
    result = _run_scenario(
        f"applyRaceState({PLAN_A});\n"
        "updateSegmentTargetWatts(1, '250');\n"
        f"loadRaceStateAndRows({PLAN_B});\n"
        f"applyRaceState({PLAN_A});\n"
    )
    assert result["rowsDirty"] is False
    assert result["rows"] == [
        {"kind": "warmup", "durationSec": 90},
        {"kind": "work", "durationSec": 300, "targetWatts": 150},
    ]


# ---------------------------------------------------------------------------
# 6. End-to-end poll/action wiring. Every test above drives applyRaceState /
# loadRaceStateAndRows directly -- which pins the READ-side guard (the
# "if (!state.rowsDirty)" branch) but nothing above pins WHICH of those two
# loaders each real caller is wired to. That wiring is a single identifier
# at each call site (refreshState / savePlan / resetClass), exactly the
# kind of edit a future "these two loaders look redundant" cleanup would
# make silently: swap refreshState's applyRaceState for loadRaceStateAndRows
# and the venue bug returns in full, with every test above still green.
#
# So these tests extract and run the real refreshState/savePlan/resetClass
# end to end, stubbing only the network boundary (fetch) and the confirm()
# dialog -- not the loaders they call.
# ---------------------------------------------------------------------------


def _run_action_scenario(steps_js: str, fetch_response_js: str) -> dict:
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
    load_race_state_and_rows_fn = _strip_js_comments(
        _extract_function(source, "loadRaceStateAndRows")
    )
    update_segment_target_watts_fn = _strip_js_comments(
        _extract_function(source, "updateSegmentTargetWatts")
    )
    admin_headers_fn = _strip_js_comments(_extract_function(source, "adminHeaders"))
    set_message_fn = _strip_js_comments(_extract_function(source, "setMessage"))
    build_segments_payload_fn = _strip_js_comments(
        _extract_function(source, "buildSegmentsPayload")
    )
    # The real source declares these `async function ...` -- the marker
    # _extract_function searches for starts at "function", so "async " is
    # stripped from the extracted text. They call `await` internally, so
    # it has to be put back or the generated script is a SyntaxError.
    fetch_json_fn = "async " + _strip_js_comments(
        _extract_function(source, "fetchJson")
    )
    refresh_state_fn = "async " + _strip_js_comments(
        _extract_function(source, "refreshState")
    )
    save_plan_fn = "async " + _strip_js_comments(_extract_function(source, "savePlan"))
    reset_class_fn = "async " + _strip_js_comments(
        _extract_function(source, "resetClass")
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
        # The real page only calls window.confirm from resetClass -- always
        # answer yes so the reset path under test actually runs.
        + "const window = { confirm: () => true };\n"
        # The network boundary is the only thing stubbed here -- the real
        # fetchJson/refreshState/savePlan/resetClass all run untouched.
        # Ignores the url/options entirely and always hands back
        # fetch_response_js; each test below only ever drives one request.
        + "async function fetch(url, options) {\n"
        + "  return {\n"
        + "    ok: true,\n"
        + f"    text: async () => JSON.stringify({fetch_response_js}),\n"
        + "  };\n"
        + "}\n"
        + default_plan_rows_const
        + "\n"
        + "const state = { race: {}, rows: [], rowsDirty: false, adminToken: '' };\n"
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
        + load_race_state_and_rows_fn
        + "\n"
        + update_segment_target_watts_fn
        + "\n"
        + admin_headers_fn
        + "\n"
        + set_message_fn
        + "\n"
        + build_segments_payload_fn
        + "\n"
        + fetch_json_fn
        + "\n"
        + refresh_state_fn
        + "\n"
        + save_plan_fn
        + "\n"
        + reset_class_fn
        + "\n"
        + "function renderAll() {\n"
        + "  renderPlanEditor();\n"
        + "  renderPlanPreview();\n"
        + "  renderSummaryState();\n"
        + "}\n"
        + "(async () => {\n"
        + steps_js
        + "\n"
        + "console.log(JSON.stringify({\n"
        + "  rows: state.rows,\n"
        + "  rowsDirty: state.rowsDirty,\n"
        + "  planRowsHtml: mockElements['plan-rows'].innerHTML,\n"
        + "}));\n"
        + "})();\n"
    )
    return json.loads(_run_node(script))


def test_poll_path_refreshstate_cannot_clear_the_dirty_flag():
    # This is the exact edit a future "these two loaders look redundant"
    # cleanup would make: refreshState calling loadRaceStateAndRows instead
    # of applyRaceState. Driving the real refreshState (not a stand-in) is
    # what pins the call site itself, not just the guard it is supposed to
    # respect.
    result = _run_action_scenario(
        f"applyRaceState({PLAN_A});\n"
        "updateSegmentTargetWatts(1, '250');\n"
        "await refreshState();\n",
        PLAN_B,
    )
    # Asserting the flag survived is the part that actually catches a
    # loadRaceStateAndRows swap -- a rebuild that happened to reproduce the
    # same 250 markup by coincidence would still pass a rows-only check.
    assert result["rowsDirty"] is True
    assert result["rows"][1]["targetWatts"] == 250
    assert 'value="250"' in result["planRowsHtml"]
    assert 'value="999"' not in result["planRowsHtml"]


def test_save_plan_clears_dirty_and_reapplies_the_server_reply():
    result = _run_action_scenario(
        f"applyRaceState({PLAN_A});\n"
        "updateSegmentTargetWatts(1, '250');\n"
        "await savePlan();\n",
        PLAN_B,
    )
    assert result["rowsDirty"] is False
    assert result["rows"] == [
        {"kind": "warmup", "durationSec": 600},
        {"kind": "work", "durationSec": 1200, "targetWatts": 999},
    ]
    assert 'value="999"' in result["planRowsHtml"]
    assert 'value="250"' not in result["planRowsHtml"]


def test_reset_class_clears_dirty_and_reapplies_the_server_reply():
    result = _run_action_scenario(
        f"applyRaceState({PLAN_B});\n"
        "updateSegmentTargetWatts(1, '999');\n"
        "await resetClass();\n",
        PLAN_A,
    )
    assert result["rowsDirty"] is False
    assert result["rows"] == [
        {"kind": "warmup", "durationSec": 90},
        {"kind": "work", "durationSec": 300, "targetWatts": 150},
    ]
    assert 'value="150"' in result["planRowsHtml"]
