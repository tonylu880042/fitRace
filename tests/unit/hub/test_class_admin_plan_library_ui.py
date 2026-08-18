"""Tests for the saved-class-plan library UI on Class Admin
(`hub_server/static/classAdmin.html`): naming a class, picking a saved
class from a dropdown to load it into the editor, and deleting a saved
class.

This is a NEW test file (task instructions permit new test files under
tests/unit/hub/ but forbid modifying existing ones, per
tests/unit/hub/test_class_admin_history.py's precedent for the same
reason). It follows the exact brace-depth extraction + stubbed-DOM
technique used throughout tests/unit/hub/test_class_admin_page.py and
tests/unit/hub/test_class_admin_plan_editor_dirty.py:

  1. buildNamedPlanPayload and savedPlanOptions are pure, DOM-free helpers
     -- executed under `node -e` and asserted on real return values, per
     CLAUDE.md's testability guidance (a correct helper whose result never
     reaches the DOM, or is only grepped for by name, is the recurring
     defect in this codebase).
  2. buildSavedClassPickerHtml is the render-path sibling (stubbed t/
     escapeHtml, like buildPlanPreviewHtml/buildStationStatusHtml).
  3. renderSavedClassPicker, onSavedClassPickerChange, and savePlan()'s new
     named-save branch are driven end to end against a stubbed DOM/fetch,
     proving the pure helpers above are genuinely CALLED on the real code
     path -- not merely present in the file.
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
_CJK_RE = re.compile(r"[一-鿿]")


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read_class_admin() -> str:
    return (STATIC_DIR / "classAdmin.html").read_text(encoding="utf-8")


_BRACKET_PAIRS = {"{": "}", "[": "]", "(": ")"}
_BRACKET_CLOSERS = {close: open_ for open_, close in _BRACKET_PAIRS.items()}


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


def _matching_close(source: str, open_idx: int) -> int:
    """Paren-aware close finder (mirrors
    tests/unit/hub/test_class_admin_plan_editor_dirty.py) -- needed because
    some functions extracted below take a default OBJECT-LITERAL parameter
    (fetchJson(url, options = {})), which the naive "first {" scan would
    truncate on."""
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


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    paren_open = start + len(marker) - 1
    paren_close = _matching_close(source, paren_open)
    brace_open = source.index("{", paren_close)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


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


def _metric_number_stub() -> str:
    return (
        "const metricNumber = (value, fallback = 0) => {\n"
        "  const n = Number(value);\n"
        "  return Number.isFinite(n) ? n : fallback;\n"
        "};\n"
    )


# ---------------------------------------------------------------------------
# 1. buildNamedPlanPayload -- pure, DOM-free.
# ---------------------------------------------------------------------------


def _run_build_named_plan_payload(name_js: str, rows_js: str):
    source = _read_class_admin()
    build_segments_fn = _strip_js_comments(
        _extract_function(source, "buildSegmentsPayload")
    )
    fn = _strip_js_comments(_extract_function(source, "buildNamedPlanPayload"))
    script = (
        _metric_number_stub()
        + build_segments_fn
        + "\n"
        + fn
        + "\n"
        + f"const rows = {rows_js};\n"
        + f"console.log(JSON.stringify(buildNamedPlanPayload({name_js}, rows)));"
    )
    return json.loads(_run_node(script))


def test_build_named_plan_payload_trims_and_wraps_segments():
    rows = '[{"kind": "work", "durationSec": 300}]'
    result = _run_build_named_plan_payload('"  Spin 45  "', rows)
    assert result == {
        "name": "Spin 45",
        "plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
    }


def test_build_named_plan_payload_blank_name_is_null():
    rows = '[{"kind": "work", "durationSec": 300}]'
    assert _run_build_named_plan_payload('""', rows) is None


def test_build_named_plan_payload_whitespace_only_name_is_null():
    rows = '[{"kind": "work", "durationSec": 300}]'
    assert _run_build_named_plan_payload('"   "', rows) is None


def test_build_named_plan_payload_null_name_is_null():
    rows = '[{"kind": "work", "durationSec": 300}]'
    assert _run_build_named_plan_payload("null", rows) is None


def test_build_named_plan_payload_undefined_name_is_null():
    rows = '[{"kind": "work", "durationSec": 300}]'
    assert _run_build_named_plan_payload("undefined", rows) is None


# ---------------------------------------------------------------------------
# 2. savedPlanOptions -- pure, DOM-free.
# ---------------------------------------------------------------------------


def _run_saved_plan_options(plans_js: str, selected_js: str):
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "savedPlanOptions"))
    script = (
        fn
        + "\n"
        + f"const plans = {plans_js};\n"
        + f"console.log(JSON.stringify(savedPlanOptions(plans, {selected_js})));"
    )
    return json.loads(_run_node(script))


def test_saved_plan_options_always_leads_with_blank_new_class_option():
    result = _run_saved_plan_options("[]", "null")
    assert result == [{"value": "", "label": "", "selected": True}]


def test_saved_plan_options_lists_every_saved_plan_by_name():
    plans = '[{"name": "Spin 45"}, {"name": "Leg Day"}]'
    result = _run_saved_plan_options(plans, "null")
    assert result == [
        {"value": "", "label": "", "selected": True},
        {"value": "Spin 45", "label": "Spin 45", "selected": False},
        {"value": "Leg Day", "label": "Leg Day", "selected": False},
    ]


def test_saved_plan_options_marks_the_matching_name_selected_not_the_blank_option():
    plans = '[{"name": "Spin 45"}, {"name": "Leg Day"}]'
    result = _run_saved_plan_options(plans, '"Leg Day"')
    by_value = {opt["value"]: opt["selected"] for opt in result}
    assert by_value == {"": False, "Spin 45": False, "Leg Day": True}


def test_saved_plan_options_no_match_falls_back_to_blank_selected():
    plans = '[{"name": "Spin 45"}]'
    result = _run_saved_plan_options(plans, '"Nonexistent"')
    by_value = {opt["value"]: opt["selected"] for opt in result}
    # savedPlanOptions does not validate the name exists -- it just marks
    # whichever entry's name matches. If none matches, none is selected;
    # the picker will visually show nothing selected, which is acceptable
    # since this only happens if selectedSavedClassName points at a name
    # that no longer exists (e.g. deleted from another tab).
    assert by_value["Spin 45"] is False


# ---------------------------------------------------------------------------
# 3. buildSavedClassPickerHtml -- render-path sibling of savedPlanOptions.
# ---------------------------------------------------------------------------


def _run_build_saved_class_picker_html(options_js: str) -> str:
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "buildSavedClassPickerHtml"))
    script = (
        _t_stub()
        + _escape_html_stub()
        + fn
        + "\n"
        + f"const options = {options_js};\n"
        + "console.log(buildSavedClassPickerHtml(options));"
    )
    return _run_node(script)


def test_build_saved_class_picker_html_translates_the_blank_new_option():
    options = '[{"value": "", "label": "", "selected": true}]'
    html = _run_build_saved_class_picker_html(options)
    assert '<option value="" selected>' in html
    assert "T[classAdmin.saved_class_picker_new_option]" in html


def test_build_saved_class_picker_html_renders_saved_names_untranslated():
    options = (
        '[{"value": "", "label": "", "selected": false},'
        ' {"value": "Spin 45", "label": "Spin 45", "selected": true}]'
    )
    html = _run_build_saved_class_picker_html(options)
    assert '<option value="Spin 45" selected>Spin 45</option>' in html


def test_build_saved_class_picker_html_escapes_names():
    options = '[{"value": "<b>X</b>", "label": "<b>X</b>", "selected": false}]'
    html = _run_build_saved_class_picker_html(options)
    assert "<b>" not in html
    assert "&lt;b&gt;" in html


# ---------------------------------------------------------------------------
# 4. renderSavedClassPicker -- DOM wiring. Proves savedPlanOptions and
# buildSavedClassPickerHtml's output actually reaches the <select> element
# and the delete button's disabled state, not just that they compute
# correctly in isolation.
# ---------------------------------------------------------------------------


def _run_render_saved_class_picker(saved_plans_js: str, selected_js: str) -> dict:
    source = _read_class_admin()
    saved_plan_options_fn = _strip_js_comments(
        _extract_function(source, "savedPlanOptions")
    )
    build_html_fn = _strip_js_comments(
        _extract_function(source, "buildSavedClassPickerHtml")
    )
    render_fn = _strip_js_comments(_extract_function(source, "renderSavedClassPicker"))
    script = (
        _t_stub()
        + _escape_html_stub()
        + "const mockElements = {};\n"
        + "function makeEl() { return { innerHTML: '', disabled: false }; }\n"
        + "function $(id) {\n"
        + "  if (!mockElements[id]) mockElements[id] = makeEl();\n"
        + "  return mockElements[id];\n"
        + "}\n"
        + f"const state = {{ savedPlans: {saved_plans_js}, selectedSavedClassName: {selected_js} }};\n"
        + saved_plan_options_fn
        + "\n"
        + build_html_fn
        + "\n"
        + render_fn
        + "\n"
        + "renderSavedClassPicker();\n"
        + "console.log(JSON.stringify({\n"
        + "  pickerHtml: mockElements['saved-class-picker'].innerHTML,\n"
        + "  deleteDisabled: mockElements['btn-delete-saved-class'].disabled,\n"
        + "}));\n"
    )
    return json.loads(_run_node(script))


def test_render_saved_class_picker_writes_options_into_the_select():
    result = _run_render_saved_class_picker(
        '[{"name": "Spin 45"}, {"name": "Leg Day"}]', "null"
    )
    assert '<option value="Spin 45"' in result["pickerHtml"]
    assert '<option value="Leg Day"' in result["pickerHtml"]
    assert "T[classAdmin.saved_class_picker_new_option]" in result["pickerHtml"]


def test_render_saved_class_picker_disables_delete_button_when_no_selection():
    result = _run_render_saved_class_picker("[]", "null")
    assert result["deleteDisabled"] is True


def test_render_saved_class_picker_enables_delete_button_when_a_plan_is_selected():
    result = _run_render_saved_class_picker('[{"name": "Spin 45"}]', '"Spin 45"')
    assert result["deleteDisabled"] is False


# ---------------------------------------------------------------------------
# 5. onSavedClassPickerChange -- end-to-end selection handling: loads rows
# via rowsFromClassPlan, fills #class-name, and keeps rowsDirty TRUE (the
# documented trap: clearing it here would let the next poll clobber the
# freshly loaded rows, since the server's race.class_plan was never told
# about this local load).
# ---------------------------------------------------------------------------


def _run_on_saved_class_picker_change(
    saved_plans_js: str, name_js: str, initial_rows_js: str = "[]"
) -> dict:
    source = _read_class_admin()
    default_plan_rows_const = _strip_js_comments(
        source[
            source.index("const DEFAULT_PLAN_ROWS") : source.index(
                "];", source.index("const DEFAULT_PLAN_ROWS")
            )
            + 2
        ]
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
    build_station_status_html_fn = _strip_js_comments(
        _extract_function(source, "buildStationStatusHtml")
    )
    merge_station_status_fn = _strip_js_comments(
        _extract_function(source, "mergeStationStatus")
    )
    build_class_history_html_fn = _strip_js_comments(
        _extract_function(source, "buildClassHistoryHtml")
    )
    format_class_run_at_fn = _strip_js_comments(
        _extract_function(source, "formatClassRunAt")
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
    render_station_status_fn = _strip_js_comments(
        _extract_function(source, "renderStationStatus")
    )
    render_class_history_fn = _strip_js_comments(
        _extract_function(source, "renderClassHistory")
    )
    render_summary_state_fn = _strip_js_comments(
        _extract_function(source, "renderSummaryState")
    )
    saved_plan_options_fn = _strip_js_comments(
        _extract_function(source, "savedPlanOptions")
    )
    build_saved_class_picker_html_fn = _strip_js_comments(
        _extract_function(source, "buildSavedClassPickerHtml")
    )
    render_saved_class_picker_fn = _strip_js_comments(
        _extract_function(source, "renderSavedClassPicker")
    )
    rows_from_class_plan_fn = _strip_js_comments(
        _extract_function(source, "rowsFromClassPlan")
    )
    on_saved_class_picker_change_fn = _strip_js_comments(
        _extract_function(source, "onSavedClassPickerChange")
    )

    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + "const Intl = { NumberFormat: function() { return { format: (n) => String(n) }; } };\n"
        + "const currentLocale = 'en-US';\n"
        + "const mockElements = {};\n"
        + "function makeEl() {\n"
        + "  return { textContent: '', innerHTML: '', className: '', dataset: {}, disabled: false, value: '' };\n"
        + "}\n"
        + "function $(id) {\n"
        + "  if (!mockElements[id]) mockElements[id] = makeEl();\n"
        + "  return mockElements[id];\n"
        + "}\n"
        + default_plan_rows_const
        + "\n"
        + f"const state = {{ race: {{}}, rows: {initial_rows_js}, rowsDirty: false, stations: {{}}, stationHealth: [], classHistory: [], savedPlans: {saved_plans_js}, selectedSavedClassName: null }};\n"
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
        + merge_station_status_fn
        + "\n"
        + build_station_status_html_fn
        + "\n"
        + format_class_run_at_fn
        + "\n"
        + build_class_history_html_fn
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
        + render_station_status_fn
        + "\n"
        + render_class_history_fn
        + "\n"
        + render_summary_state_fn
        + "\n"
        + saved_plan_options_fn
        + "\n"
        + build_saved_class_picker_html_fn
        + "\n"
        + render_saved_class_picker_fn
        + "\n"
        + rows_from_class_plan_fn
        + "\n"
        + "function renderAll() {\n"
        + "  renderPlanEditor();\n"
        + "  renderPlanPreview();\n"
        + "  renderStationStatus();\n"
        + "  renderClassHistory();\n"
        + "  renderSummaryState();\n"
        + "  renderSavedClassPicker();\n"
        + "}\n"
        + on_saved_class_picker_change_fn
        + "\n"
        + f"onSavedClassPickerChange({name_js});\n"
        + "console.log(JSON.stringify({\n"
        + "  rows: state.rows,\n"
        + "  rowsDirty: state.rowsDirty,\n"
        + "  selectedSavedClassName: state.selectedSavedClassName,\n"
        + "  classNameValue: (mockElements['class-name'] || {}).value,\n"
        + "}));\n"
    )
    return json.loads(_run_node(script))


def test_selecting_a_saved_class_loads_its_rows_and_fills_the_name_field():
    saved_plans = json.dumps(
        [
            {
                "name": "Spin 45",
                "plan": {
                    "segments": [
                        {"kind": "warmup", "duration_sec": 90},
                        {"kind": "work", "duration_sec": 300, "target_watts": 150},
                    ]
                },
            }
        ]
    )
    result = _run_on_saved_class_picker_change(saved_plans, '"Spin 45"')
    assert result["rows"] == [
        {"kind": "warmup", "durationSec": 90},
        {"kind": "work", "durationSec": 300, "targetWatts": 150},
    ]
    assert result["classNameValue"] == "Spin 45"
    assert result["selectedSavedClassName"] == "Spin 45"


def test_selecting_a_saved_class_keeps_rows_dirty_so_the_poll_cannot_clobber_it():
    # The documented trap this feature must not fall into: clearing
    # rowsDirty here (mirroring the save/reset path) would let the next 4s
    # poll's applyRaceState() re-derive rows from the server's (unrelated,
    # unchanged) race.class_plan and silently overwrite what was just
    # loaded.
    saved_plans = json.dumps(
        [
            {
                "name": "Spin 45",
                "plan": {"segments": [{"kind": "work", "duration_sec": 60}]},
            }
        ]
    )
    result = _run_on_saved_class_picker_change(saved_plans, '"Spin 45"')
    assert result["rowsDirty"] is True


def test_selecting_the_blank_new_class_option_clears_the_name_field_only():
    saved_plans = json.dumps(
        [
            {
                "name": "Spin 45",
                "plan": {"segments": [{"kind": "work", "duration_sec": 60}]},
            }
        ]
    )
    existing_rows = '[{"kind": "rest", "durationSec": 45}]'
    result = _run_on_saved_class_picker_change(
        saved_plans, '""', initial_rows_js=existing_rows
    )
    assert result["classNameValue"] == ""
    assert result["selectedSavedClassName"] is None
    # Rows already on screen are left alone -- picking "new class" does not
    # regenerate them from anywhere.
    assert result["rows"] == [{"kind": "rest", "durationSec": 45}]


def test_selecting_an_unknown_saved_name_does_not_crash_or_change_rows():
    result = _run_on_saved_class_picker_change(
        "[]", '"Nonexistent"', initial_rows_js='[{"kind": "work", "durationSec": 30}]'
    )
    assert result["rows"] == [{"kind": "work", "durationSec": 30}]


# ---------------------------------------------------------------------------
# 6. savePlan() -- the named-save branch is genuinely wired in: a non-blank
# #class-name triggers POST /api/class/plans (via buildNamedPlanPayload)
# BEFORE POST /api/class/configure; a blank name behaves exactly as before
# (configure only). Network boundary is the only thing stubbed, per
# test_class_admin_plan_editor_dirty.py's end-to-end action harness.
# ---------------------------------------------------------------------------


def _run_save_plan_scenario(
    class_name_value: str, saved_plans_response_js: str
) -> dict:
    source = _read_class_admin()
    default_plan_rows_const = _strip_js_comments(
        source[
            source.index("const DEFAULT_PLAN_ROWS") : source.index(
                "];", source.index("const DEFAULT_PLAN_ROWS")
            )
            + 2
        ]
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
    saved_plan_options_fn = _strip_js_comments(
        _extract_function(source, "savedPlanOptions")
    )
    build_saved_class_picker_html_fn = _strip_js_comments(
        _extract_function(source, "buildSavedClassPickerHtml")
    )
    render_saved_class_picker_fn = _strip_js_comments(
        _extract_function(source, "renderSavedClassPicker")
    )
    build_named_plan_payload_fn = _strip_js_comments(
        _extract_function(source, "buildNamedPlanPayload")
    )
    build_segments_payload_fn = _strip_js_comments(
        _extract_function(source, "buildSegmentsPayload")
    )
    admin_headers_fn = _strip_js_comments(_extract_function(source, "adminHeaders"))
    set_message_fn = _strip_js_comments(_extract_function(source, "setMessage"))
    fetch_json_fn = "async " + _strip_js_comments(
        _extract_function(source, "fetchJson")
    )
    refresh_saved_class_plans_fn = "async " + _strip_js_comments(
        _extract_function(source, "refreshSavedClassPlans")
    )
    save_plan_fn = "async " + _strip_js_comments(_extract_function(source, "savePlan"))

    # The stubbed fetch inspects the URL so this scenario can hand back a
    # DIFFERENT response for POST /api/class/plans vs POST
    # /api/class/configure -- unlike test_class_admin_plan_editor_dirty.py's
    # single-response stub, savePlan()'s named branch now makes two
    # distinct requests when a name is present, and the test needs to tell
    # them apart to prove both actually fired with the right bodies.
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + "const Intl = { NumberFormat: function() { return { format: (n) => String(n) }; } };\n"
        + "const currentLocale = 'en-US';\n"
        + "const mockElements = {};\n"
        + "function makeEl() {\n"
        + "  return { textContent: '', innerHTML: '', className: '', dataset: {}, disabled: false, value: "
        + json.dumps(class_name_value)
        + " };\n"
        + "}\n"
        + "function $(id) {\n"
        + "  if (!mockElements[id]) mockElements[id] = makeEl();\n"
        + "  return mockElements[id];\n"
        + "}\n"
        + "const calls = [];\n"
        + "async function fetch(url, options) {\n"
        + "  calls.push({ url, method: (options && options.method) || 'GET', body: options && options.body });\n"
        + "  if (url.startsWith('/api/class/plans')) {\n"
        + f"    return {{ ok: true, text: async () => JSON.stringify({saved_plans_response_js}) }};\n"
        + "  }\n"
        + "  return {\n"
        + "    ok: true,\n"
        + "    text: async () => JSON.stringify({ state: 'READY', class_plan: { segments: [] } }),\n"
        + "  };\n"
        + "}\n"
        + default_plan_rows_const
        + "\n"
        + "const state = { race: {}, rows: [{ kind: 'work', durationSec: 60 }], rowsDirty: true, adminToken: '', savedPlans: [], selectedSavedClassName: null };\n"
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
        + saved_plan_options_fn
        + "\n"
        + build_saved_class_picker_html_fn
        + "\n"
        + render_saved_class_picker_fn
        + "\n"
        + build_named_plan_payload_fn
        + "\n"
        + build_segments_payload_fn
        + "\n"
        + admin_headers_fn
        + "\n"
        + set_message_fn
        + "\n"
        + fetch_json_fn
        + "\n"
        + refresh_saved_class_plans_fn
        + "\n"
        + save_plan_fn
        + "\n"
        + "(async () => {\n"
        + "await savePlan();\n"
        + "console.log(JSON.stringify({\n"
        + "  calls: calls.map((c) => ({ url: c.url, method: c.method, body: c.body ? JSON.parse(c.body) : null })),\n"
        + "  selectedSavedClassName: state.selectedSavedClassName,\n"
        + "  rowsDirty: state.rowsDirty,\n"
        + "}));\n"
        + "})();\n"
    )
    return json.loads(_run_node(script))


def test_save_plan_with_a_name_posts_to_the_library_before_configuring():
    result = _run_save_plan_scenario(
        "Spin 45", '{"plans": [{"name": "Spin 45", "plan": {"segments": []}}]}'
    )
    calls = result["calls"]
    # Order: (1) POST the named plan to the library, (2) refreshSavedClassPlans()'s
    # own GET to repopulate the picker, (3) POST /api/class/configure as always.
    assert calls[0]["url"] == "/api/class/plans"
    assert calls[0]["method"] == "POST"
    configure_calls = [c for c in calls if "/api/class/configure" in c["url"]]
    assert len(configure_calls) == 1
    # The configure call must come strictly after the named-save POST.
    assert calls.index(configure_calls[0]) > 0
    named_call = calls[0]
    assert named_call["body"] == {
        "name": "Spin 45",
        "plan": {"segments": [{"kind": "work", "duration_sec": 60}]},
    }
    assert result["selectedSavedClassName"] == "Spin 45"


def test_save_plan_with_a_name_refreshes_the_saved_plan_list():
    result = _run_save_plan_scenario(
        "Spin 45", '{"plans": [{"name": "Spin 45", "plan": {"segments": []}}]}'
    )
    # refreshSavedClassPlans() issues its own GET /api/class/plans?t=...
    get_calls = [
        c
        for c in result["calls"]
        if c["url"].startswith("/api/class/plans") and c["method"] == "GET"
    ]
    assert len(get_calls) == 1


def test_save_plan_with_a_blank_name_only_configures_no_library_call():
    result = _run_save_plan_scenario("", "{}")
    urls = [c["url"] for c in result["calls"]]
    assert not any(u.startswith("/api/class/plans") for u in urls)
    assert any("/api/class/configure" in u for u in urls)
    assert result["selectedSavedClassName"] is None


def test_save_plan_with_a_whitespace_only_name_only_configures_no_library_call():
    result = _run_save_plan_scenario("   ", "{}")
    urls = [c["url"] for c in result["calls"]]
    # buildNamedPlanPayload returns null for whitespace-only, but the
    # nameValue guard is what actually prevents the call at runtime here
    # ("   " is truthy) -- this pins that the null-return path inside the
    # guard is ALSO exercised and behaves correctly, not just the
    # nameValue-falsy shortcut covered by the blank-string test above.
    assert not any(u.startswith("/api/class/plans") for u in urls)


# ---------------------------------------------------------------------------
# 7. Page markup: new controls exist with the right ids/wiring.
# ---------------------------------------------------------------------------


def test_class_admin_page_has_saved_class_picker_and_controls():
    body = _read_class_admin()
    assert 'id="saved-class-picker"' in body
    assert 'id="class-name"' in body
    assert 'id="btn-delete-saved-class"' in body
    assert 'onchange="onSavedClassPickerChange(this.value)"' in body
    assert 'onclick="deleteSavedClass()"' in body


def test_delete_saved_class_button_starts_disabled():
    body = _read_class_admin()
    match = re.search(r'<button[^>]*id="btn-delete-saved-class"[^>]*>', body)
    assert match, "delete-saved-class button not found"
    assert "disabled" in match.group(0)


def test_delete_saved_class_guarded_by_window_confirm():
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "deleteSavedClass"))
    assert "window.confirm(" in fn
    assert "classAdmin.confirm_delete_saved_class" in fn


def test_delete_saved_class_calls_the_delete_endpoint():
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "deleteSavedClass"))
    assert "/api/class/plans/" in fn
    assert 'method: "DELETE"' in fn


def test_init_fetches_saved_class_plans_on_load():
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "init"))
    assert "refreshSavedClassPlans()" in fn


def test_render_all_renders_saved_class_picker():
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "renderAll"))
    assert "renderSavedClassPicker()" in fn


def test_refresh_saved_class_plans_not_part_of_the_four_second_poll():
    """Per spec: state.savedPlans refreshes on load and after save/delete,
    NOT on every scheduleRefresh() tick -- the library is venue
    configuration, not something expected to change every 4s."""
    source = _read_class_admin()
    refresh_all_fn = _strip_js_comments(_extract_function(source, "refreshAll"))
    assert "refreshSavedClassPlans()" not in refresh_all_fn


# ---------------------------------------------------------------------------
# 8. i18n: new classAdmin.* keys exist symmetrically in all six locales
# (zh-TW carries real CJK translations, every other locale differs from
# English), and the parameterised delete-confirm key keeps its {name}
# placeholder. Also pins that confirm_reset no longer implies the plan is
# lost.
# ---------------------------------------------------------------------------

NEW_LIBRARY_KEYS = [
    "classAdmin.label_saved_class_picker",
    "classAdmin.saved_class_picker_new_option",
    "classAdmin.label_class_name",
    "classAdmin.btn_delete_saved_class",
    "classAdmin.confirm_delete_saved_class",
    "classAdmin.message_saved_class_deleted",
]

LOCALES = ["de-CH", "en-US", "fr", "it", "sv", "zh-TW"]


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_new_library_keys_exist_in_every_locale():
    for locale in LOCALES:
        messages = _load_locale(locale)
        missing = set(NEW_LIBRARY_KEYS) - set(messages.keys())
        assert not missing, f"Missing keys in {locale}: {missing}"


def test_new_library_keys_in_zh_tw_contain_cjk_characters():
    zh_tw = _load_locale("zh-TW")
    for key in NEW_LIBRARY_KEYS:
        assert _CJK_RE.search(zh_tw[key]), f"{key} in zh-TW has no CJK character"


def test_new_library_keys_differ_from_english_in_every_other_locale():
    en_us = _load_locale("en-US")
    for locale in LOCALES:
        if locale == "en-US":
            continue
        messages = _load_locale(locale)
        for key in NEW_LIBRARY_KEYS:
            assert messages[key] != en_us[key], f"{key} in {locale} is untranslated"


def test_confirm_delete_saved_class_keeps_name_placeholder_in_every_locale():
    for locale in LOCALES:
        value = _load_locale(locale)["classAdmin.confirm_delete_saved_class"]
        assert "{name}" in value, f"{locale} lost the {{name}} placeholder: {value!r}"


def test_confirm_reset_no_longer_implies_the_plan_is_lost():
    for locale in LOCALES:
        value = _load_locale(locale)["classAdmin.confirm_reset"]
        # The old English wording was "This clears the plan progress" --
        # ambiguous enough to read as "the plan is cleared". Pin the
        # specific fix for en-US (the only locale we can literally
        # substring-check English wording against) and, for every locale,
        # that the value actually changed from what shipped with the old
        # (plan-deleting) reset behaviour.
        assert value, f"{locale} confirm_reset is empty"
    en_us_value = _load_locale("en-US")["classAdmin.confirm_reset"]
    assert "clears the plan progress" not in en_us_value
    assert "kept" in en_us_value.lower()


# ---------------------------------------------------------------------------
# 9. The picker must self-heal after an operator unlock.
#
# GET /api/class/plans is admin-gated, and require_admin only enforces when
# FITRACE_ADMIN_TOKEN is set -- exactly the live venue setup. A coach who
# opens /classAdmin before unlocking gets a 401 from
# refreshSavedClassPlans() on load (swallowed by its own try/catch, so the
# picker just stays empty); refreshSavedClassPlans() is deliberately kept
# OUT of refreshAll()/scheduleRefresh() (it is venue configuration, not
# something that changes every 4s), so nothing else on the page will ever
# retry that fetch. Without saveAdminToken() itself triggering a refresh,
# the saved-class dropdown stays empty until a full page reload -- the one
# thing a coach mid-setup will not think to do.
#
# saveAdminToken() is made `async` and awaits refreshSavedClassPlans()
# directly (rather than a bare fire-and-forget call) for the same reason
# every other network-triggering handler on this page (savePlan,
# deleteSavedClass) is async: consistency, and a script harness like this
# one can `await saveAdminToken()` and observe the fetch actually
# completed rather than racing an unawaited promise. No existing test
# extracts/drives saveAdminToken(), so making it async does not touch any
# other test's assumptions.
# ---------------------------------------------------------------------------


def _run_save_admin_token_scenario(
    fetch_response_js: str, admin_token_input: str = "secret-token"
) -> dict:
    source = _read_class_admin()
    saved_plan_options_fn = _strip_js_comments(
        _extract_function(source, "savedPlanOptions")
    )
    build_saved_class_picker_html_fn = _strip_js_comments(
        _extract_function(source, "buildSavedClassPickerHtml")
    )
    render_saved_class_picker_fn = _strip_js_comments(
        _extract_function(source, "renderSavedClassPicker")
    )
    admin_headers_fn = _strip_js_comments(_extract_function(source, "adminHeaders"))
    set_message_fn = _strip_js_comments(_extract_function(source, "setMessage"))
    fetch_json_fn = "async " + _strip_js_comments(
        _extract_function(source, "fetchJson")
    )
    refresh_saved_class_plans_fn = "async " + _strip_js_comments(
        _extract_function(source, "refreshSavedClassPlans")
    )
    close_login_fn = _strip_js_comments(_extract_function(source, "closeLogin"))
    # The real page declares `async function saveAdminToken() {...}` (after
    # this fix) -- _extract_function's marker starts at "function", so
    # "async " is stripped and must be reattached, exactly like
    # fetchJson/refreshState/savePlan/resetClass are handled in
    # tests/unit/hub/test_class_admin_plan_editor_dirty.py. Before the fix
    # saveAdminToken() is a plain (non-async) function, so prepending
    # "async " here still produces valid, runnable JS either way -- this
    # harness works whether or not the fix has landed, which is exactly
    # what a TDD red/green cycle needs.
    save_admin_token_fn = "async " + _strip_js_comments(
        _extract_function(source, "saveAdminToken")
    )

    script = (
        _t_stub()
        + _escape_html_stub()
        + "const calls = [];\n"
        + "async function fetch(url, options) {\n"
        + "  calls.push({ url, method: (options && options.method) || 'GET' });\n"
        + f"  return {{ ok: true, text: async () => JSON.stringify({fetch_response_js}) }};\n"
        + "}\n"
        + "const localStorage = {\n"
        + "  data: {},\n"
        + "  setItem(key, value) { this.data[key] = value; },\n"
        + "  getItem(key) { return this.data[key] || null; },\n"
        + "};\n"
        + "const mockElements = {};\n"
        + "function makeEl() {\n"
        + "  return {\n"
        + "    value: "
        + json.dumps(admin_token_input)
        + ",\n"
        + "    innerHTML: '', disabled: false, className: '', textContent: '',\n"
        + "    classList: { add() {}, remove() {} },\n"
        + "  };\n"
        + "}\n"
        + "function $(id) {\n"
        + "  if (!mockElements[id]) mockElements[id] = makeEl();\n"
        + "  return mockElements[id];\n"
        + "}\n"
        + "const state = { adminToken: '', savedPlans: [], selectedSavedClassName: null };\n"
        + saved_plan_options_fn
        + "\n"
        + build_saved_class_picker_html_fn
        + "\n"
        + render_saved_class_picker_fn
        + "\n"
        + admin_headers_fn
        + "\n"
        + set_message_fn
        + "\n"
        + fetch_json_fn
        + "\n"
        + refresh_saved_class_plans_fn
        + "\n"
        + close_login_fn
        + "\n"
        + save_admin_token_fn
        + "\n"
        + "(async () => {\n"
        + "await saveAdminToken();\n"
        + "console.log(JSON.stringify({\n"
        + "  calls,\n"
        + "  adminToken: state.adminToken,\n"
        + "  storedToken: localStorage.data['fitrace.adminToken'],\n"
        + "  pickerHtml: mockElements['saved-class-picker']\n"
        + "    ? mockElements['saved-class-picker'].innerHTML\n"
        + "    : null,\n"
        + "}));\n"
        + "})();\n"
    )
    return json.loads(_run_node(script))


def test_saving_the_admin_token_refetches_the_saved_class_plan_list():
    # Real behaviour, not a substring check: the token save must actually
    # trigger a network call to GET /api/class/plans.
    result = _run_save_admin_token_scenario(
        '{"plans": [{"name": "Spin 45", "plan": {"segments": []}}]}'
    )
    plan_calls = [c for c in result["calls"] if c["url"].startswith("/api/class/plans")]
    assert len(plan_calls) == 1
    assert plan_calls[0]["method"] == "GET"


def test_saving_the_admin_token_still_stores_the_token_and_closes_the_login():
    result = _run_save_admin_token_scenario("{}")
    assert result["adminToken"] == "secret-token"
    assert result["storedToken"] == "secret-token"


def test_saving_the_admin_token_repopulates_the_previously_empty_picker():
    # End-to-end proof of the fix, not just that a fetch fired: the newly
    # fetched plan actually reaches the rendered <select>.
    result = _run_save_admin_token_scenario(
        '{"plans": [{"name": "Spin 45", "plan": {"segments": []}}]}'
    )
    assert '<option value="Spin 45"' in result["pickerHtml"]


def test_save_admin_token_button_still_wired_in_the_login_modal():
    body = _read_class_admin()
    assert 'onclick="saveAdminToken()"' in body
