"""Tests for the per-segment target-power (watts) editing added to Class
Admin's plan editor (`hub_server/static/classAdmin.html`).

Per CLAUDE.md's testability guidance, the recurring defect in this codebase
is a correct pure helper whose result never reaches the DOM/payload, caught
only by a test that greps the source for a function name. To close that gap
this module:

  1. Executes the real pure helpers (buildSegmentsPayload, rowsFromClassPlan,
     repeatSegmentGroup, buildPlanEditorHtml) under `node -e`, extracted from
     the page by the same brace-depth technique
     tests/unit/hub/test_class_admin_page.py uses, and asserts on their
     actual return values / rendered HTML -- not on source text.
  2. Also drives the render path (buildPlanEditorHtml) to prove a saved
     target actually lands in the input's `value="..."` attribute, and that
     a target-less plan leaves the watts input genuinely blank (`value=""`),
     not "null" or "undefined" as a literal string.
  3. Covers the new classAdmin.placeholder_target_watts i18n key across all
     six locales, mirroring section 9/11 of test_class_admin_page.py and
     test_dashboard_class_board.py.

This file does not modify tests/unit/hub/test_class_admin_page.py. Several
of that file's tests pin the OLD row/payload shape with exact dict equality
for rows/payloads that carry no target -- the implementation is written so
those cases produce byte-for-byte the same shape they always did (the
targetWatts / target_watts key is only added when a target is actually
present), which is exercised directly here too.
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

LOCALES = ["de-CH", "en-US", "fr", "it", "sv", "zh-TW"]


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


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


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


# ---------------------------------------------------------------------------
# 1. buildSegmentsPayload -- carries target_watts only where entered.
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


def test_build_segments_payload_carries_target_watts_where_entered():
    rows = '[{"kind": "work", "durationSec": 300, "targetWatts": 180}]'
    result = _run_build_segments_payload(rows)
    assert result == {
        "segments": [{"kind": "work", "duration_sec": 300, "target_watts": 180}]
    }


def test_build_segments_payload_omits_target_watts_when_null():
    rows = '[{"kind": "work", "durationSec": 300, "targetWatts": null}]'
    result = _run_build_segments_payload(rows)
    assert "target_watts" not in result["segments"][0]


def test_build_segments_payload_omits_target_watts_when_blank_string():
    # The watts input reports an empty string when the coach leaves it
    # blank -- must be treated exactly like null, not coerced to 0.
    rows = '[{"kind": "work", "durationSec": 300, "targetWatts": ""}]'
    result = _run_build_segments_payload(rows)
    assert "target_watts" not in result["segments"][0]


def test_build_segments_payload_omits_target_watts_key_entirely_absent():
    # A row that never had targetWatts set at all (e.g. a legacy DEFAULT_
    # PLAN_ROWS entry) must behave the same as an explicit null/blank.
    rows = '[{"kind": "warmup", "durationSec": 300}]'
    result = _run_build_segments_payload(rows)
    assert result == {"segments": [{"kind": "warmup", "duration_sec": 300}]}


def test_build_segments_payload_mixed_rows_only_targeted_ones_carry_watts():
    rows = (
        '[{"kind": "warmup", "durationSec": 300, "targetWatts": 90},'
        ' {"kind": "rest", "durationSec": 60, "targetWatts": null}]'
    )
    result = _run_build_segments_payload(rows)
    assert result["segments"][0]["target_watts"] == 90
    assert "target_watts" not in result["segments"][1]


def test_build_segments_payload_coerces_string_target_watts_to_number():
    rows = '[{"kind": "work", "durationSec": 300, "targetWatts": "220"}]'
    result = _run_build_segments_payload(rows)
    assert result["segments"][0]["target_watts"] == 220
    assert isinstance(result["segments"][0]["target_watts"], int)


# ---------------------------------------------------------------------------
# 2. repeatSegmentGroup -- "repeat this group x N" must carry the target
# along with kind and duration into every copy.
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


def test_repeat_segment_group_preserves_target_watts_in_every_copy():
    rows = (
        '[{"kind": "work", "durationSec": 40, "targetWatts": 200},'
        ' {"kind": "rest", "durationSec": 20, "targetWatts": null}]'
    )
    result = _run_repeat_segment_group(rows, 0, 1, 3)
    assert result == [
        {"kind": "work", "durationSec": 40, "targetWatts": 200},
        {"kind": "rest", "durationSec": 20, "targetWatts": None},
        {"kind": "work", "durationSec": 40, "targetWatts": 200},
        {"kind": "rest", "durationSec": 20, "targetWatts": None},
        {"kind": "work", "durationSec": 40, "targetWatts": 200},
        {"kind": "rest", "durationSec": 20, "targetWatts": None},
    ]


# ---------------------------------------------------------------------------
# 3. rowsFromClassPlan -- the load path. A saved target populates the row;
# a target-less segment leaves the row without a targetWatts key (matching
# the shape tests/unit/hub/test_class_admin_page.py already pins for that
# case).
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


def test_rows_from_class_plan_carries_target_watts_when_present():
    class_plan = (
        '{"segments": [{"kind": "work", "duration_sec": 300, "target_watts": 150}]}'
    )
    result = _run_rows_from_class_plan(class_plan)
    assert result == [{"kind": "work", "durationSec": 300, "targetWatts": 150}]


def test_rows_from_class_plan_leaves_target_watts_key_absent_when_none():
    class_plan = (
        '{"segments": [{"kind": "warmup", "duration_sec": 300, "target_watts": null}]}'
    )
    result = _run_rows_from_class_plan(class_plan)
    assert result == [{"kind": "warmup", "durationSec": 300}]
    assert "targetWatts" not in result[0]


def test_rows_from_class_plan_leaves_target_watts_key_absent_when_missing():
    # Backward compatibility: the exact legacy payload shape (no
    # target_watts key at all on the wire).
    class_plan = '{"segments": [{"kind": "warmup", "duration_sec": 300}]}'
    result = _run_rows_from_class_plan(class_plan)
    assert result == [{"kind": "warmup", "durationSec": 300}]


def test_rows_from_class_plan_mixed_plan_only_targeted_segments_carry_watts():
    class_plan = json.dumps(
        {
            "segments": [
                {"kind": "warmup", "duration_sec": 300, "target_watts": 90},
                {"kind": "work", "duration_sec": 1200},
            ]
        }
    )
    result = _run_rows_from_class_plan(class_plan)
    assert result[0]["targetWatts"] == 90
    assert "targetWatts" not in result[1]


# ---------------------------------------------------------------------------
# 4. buildPlanEditorHtml -- the render path. A row with a target renders it
# into the watts input's value attribute; a row without one renders a
# genuinely blank value (`value=""`), never the literal string "null" or
# "undefined".
# ---------------------------------------------------------------------------


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


def test_build_plan_editor_html_renders_target_watts_value():
    rows = '[{"kind": "work", "durationSec": 300, "targetWatts": 180}]'
    html = _run_build_plan_editor_html(rows)
    assert 'value="180"' in html
    assert "updateSegmentTargetWatts(0" in html


def test_build_plan_editor_html_leaves_target_watts_input_blank_when_absent():
    rows = '[{"kind": "warmup", "durationSec": 300}]'
    html = _run_build_plan_editor_html(rows)
    # The seconds input renders value="300" -- pull out just the watts
    # input (the one wired to updateSegmentTargetWatts) and check IT is
    # blank, rather than asserting an absence of "value" globally.
    watts_input_match = re.search(
        r'<input[^>]*onchange="updateSegmentTargetWatts\(0, this\.value\)"[^>]*>', html
    )
    assert watts_input_match, f"watts input not found in: {html}"
    watts_input = watts_input_match.group(0)
    assert 'value=""' in watts_input
    assert "null" not in watts_input
    assert "undefined" not in watts_input


def test_build_plan_editor_html_leaves_target_watts_input_blank_when_null():
    rows = '[{"kind": "warmup", "durationSec": 300, "targetWatts": null}]'
    html = _run_build_plan_editor_html(rows)
    watts_input_match = re.search(
        r'<input[^>]*onchange="updateSegmentTargetWatts\(0, this\.value\)"[^>]*>', html
    )
    assert watts_input_match
    assert 'value=""' in watts_input_match.group(0)


def test_build_plan_editor_html_watts_input_wired_to_correct_row_index():
    rows = (
        '[{"kind": "warmup", "durationSec": 300, "targetWatts": 90},'
        ' {"kind": "work", "durationSec": 1200, "targetWatts": 220}]'
    )
    html = _run_build_plan_editor_html(rows)
    assert "updateSegmentTargetWatts(0, this.value)" in html
    assert "updateSegmentTargetWatts(1, this.value)" in html
    first_row_pos = html.index("updateSegmentTargetWatts(0")
    second_row_pos = html.index("updateSegmentTargetWatts(1")
    # The 90 must appear before the 220 in source order (row 0 before row 1).
    assert html.index('value="90"') < second_row_pos
    assert first_row_pos < html.index('value="220"')


def test_build_plan_editor_html_watts_placeholder_uses_translated_label():
    rows = '[{"kind": "work", "durationSec": 300}]'
    html = _run_build_plan_editor_html(rows)
    assert "T[classAdmin.placeholder_target_watts]" in html


# ---------------------------------------------------------------------------
# 5. Full load-path integration: a fresh GET /api/race/state reply carrying
# target_watts must end up rendered in plan-rows innerHTML, and a plan
# without any targets must leave every watts input blank. This exercises
# applyRaceState end to end, the same way
# test_apply_race_state_loads_the_saved_plan_into_the_editor_not_the_default
# does in test_class_admin_page.py, but for the new field.
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
        + "const Intl = { NumberFormat: function(locale) { return { format: (n) => String(n) }; } };\n"
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
        + "  planRowsHtml: mockElements['plan-rows'].innerHTML,\n"
        + "}));\n"
    )
    return json.loads(_run_node(script))


def test_apply_race_state_with_targets_populates_watts_inputs():
    race_state = json.dumps(
        {
            "state": "IDLE",
            "class_plan": {
                "segments": [
                    {"kind": "warmup", "duration_sec": 300, "target_watts": 100},
                    {"kind": "work", "duration_sec": 1200, "target_watts": 220},
                ]
            },
        }
    )
    result = _run_apply_race_state(race_state)
    assert result["rows"] == [
        {"kind": "warmup", "durationSec": 300, "targetWatts": 100},
        {"kind": "work", "durationSec": 1200, "targetWatts": 220},
    ]
    assert 'value="100"' in result["planRowsHtml"]
    assert 'value="220"' in result["planRowsHtml"]


def test_apply_race_state_with_no_targets_leaves_all_watts_inputs_blank():
    race_state = json.dumps(
        {
            "state": "IDLE",
            "class_plan": {
                "segments": [
                    {"kind": "warmup", "duration_sec": 300},
                    {"kind": "work", "duration_sec": 1200},
                    {"kind": "cooldown", "duration_sec": 300},
                ]
            },
        }
    )
    result = _run_apply_race_state(race_state)
    for row in result["rows"]:
        assert "targetWatts" not in row
    html = result["planRowsHtml"]
    watts_inputs = re.findall(
        r'<input[^>]*onchange="updateSegmentTargetWatts\(\d+, this\.value\)"[^>]*>',
        html,
    )
    assert len(watts_inputs) == 3
    for watts_input in watts_inputs:
        assert 'value=""' in watts_input


# ---------------------------------------------------------------------------
# 6. i18n: classAdmin.placeholder_target_watts exists in all six locales
# with a genuine (non-English-copy) translation, zh-TW carries real CJK.
# ---------------------------------------------------------------------------


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_placeholder_target_watts_key_exists_in_every_locale():
    for locale in LOCALES:
        messages = _load_locale(locale)
        assert (
            "classAdmin.placeholder_target_watts" in messages
        ), f"classAdmin.placeholder_target_watts missing in {locale}"


def test_placeholder_target_watts_key_in_zh_tw_contains_cjk_characters():
    zh_tw = _load_locale("zh-TW")
    value = zh_tw["classAdmin.placeholder_target_watts"]
    assert _CJK_RE.search(value), f"expected CJK in zh-TW: {value!r}"


def test_placeholder_target_watts_key_differs_from_english_in_every_other_locale():
    en_us = _load_locale("en-US")["classAdmin.placeholder_target_watts"]
    for locale in LOCALES:
        if locale == "en-US":
            continue
        value = _load_locale(locale)["classAdmin.placeholder_target_watts"]
        assert value != en_us, (
            f"classAdmin.placeholder_target_watts in {locale} is an "
            f"unmodified copy of the en-US string: {en_us!r}"
        )


# ---------------------------------------------------------------------------
# 7. Mutation-style regression: the grid gains a fourth column for the
# watts input (a purely visual assertion, but cheap insurance against the
# input being added to the markup without the CSS to actually show it).
# ---------------------------------------------------------------------------


def test_segment_row_grid_has_four_columns():
    source = _read_class_admin()
    match = re.search(
        r"\.segment-row\s*\{[^}]*grid-template-columns:\s*([^;]+);", source
    )
    assert match, "segment-row grid-template-columns rule not found"
    columns = match.group(1).strip()
    # Four space-separated column tracks: kind, duration, watts, actions.
    assert (
        len(re.findall(r"minmax\(|auto", columns)) == 4
    ), f"expected four column tracks, got: {columns!r}"
