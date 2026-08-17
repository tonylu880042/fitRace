"""Regression tests for the class-plan editor rebuilding its whole row list
(the `plan-rows` DOM container) while the coach is mid-keystroke on a value
input (`hub_server/static/classAdmin.html`).

Reported from the venue floor: a coach editing the class plan says focus
jumps out of the input every time they enter a digit, so they cannot type a
multi-digit target-watts value. Root cause: updateSegmentTargetWatts /
updateSegmentDuration called renderAll(), which calls renderPlanEditor(),
which regenerates every plan-rows row as fresh HTML -- destroying the
<input> the coach is typing into, along with its focus and caret. Both
handlers were wired to onchange AND oninput (commit a37293f, to keep the
model in sync with every keystroke), which made the full rebuild fire on
every keystroke rather than only on blur.

The fix: a VALUE edit (updateSegmentDuration, updateSegmentTargetWatts)
updates state.rows and refreshes only the views derived from it
(renderPlanPreview -- the timeline, the total-duration readout, the
segment count) without touching plan-rows. A ROW-SET change (addSegmentRow,
deleteSegmentRow, applyRepeatGroup) genuinely changes which rows exist, so
it still does a full renderPlanEditor(). updateSegmentKind is a <select>
change -- the row's colour stripe (segment-row-<kind>) depends on the kind
-- so it patches that one row element's className directly instead of
regenerating the whole editor.

This file exercises the real functions (extracted from the page by the
same brace-depth technique the sibling class-admin test files use) under
`node -e` against a stubbed DOM, counting writes to the plan-rows
container's innerHTML to prove a value edit never touches it while the
derived views do update, and that a row-set change still does.
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
# Harness: pulls every real function this scenario touches straight out of
# the page, plus a minimal $()/mockElements DOM. Unlike the sibling dirty
# flag tests, the plan-rows mock element tracks writes to its innerHTML in
# rowsRenderCount -- the whole point here is proving a value edit does not
# write to that specific container while add/delete/repeat still do. Each
# row also gets its own segment-row-<index> mock element, so
# updateSegmentKind's in-place className patch is directly observable.
#
# `renderAll` here is a local stand-in wired only to renderPlanEditor and
# renderPlanPreview -- the real renderAll also drives station status, class
# history and i18n, which are unrelated to the rebuild-scope question this
# file is about (same rationale as the sibling
# test_class_admin_plan_editor_dirty.py harness).
# ---------------------------------------------------------------------------


def _run_scenario(seed_rows_js: str, steps_js: str) -> dict:
    source = _read_class_admin()

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
    render_plan_editor_fn = _strip_js_comments(
        _extract_function(source, "renderPlanEditor")
    )
    render_plan_preview_fn = _strip_js_comments(
        _extract_function(source, "renderPlanPreview")
    )
    repeat_segment_group_fn = _strip_js_comments(
        _extract_function(source, "repeatSegmentGroup")
    )
    update_segment_duration_fn = _strip_js_comments(
        _extract_function(source, "updateSegmentDuration")
    )
    update_segment_target_watts_fn = _strip_js_comments(
        _extract_function(source, "updateSegmentTargetWatts")
    )
    update_segment_kind_fn = _strip_js_comments(
        _extract_function(source, "updateSegmentKind")
    )
    add_segment_row_fn = _strip_js_comments(_extract_function(source, "addSegmentRow"))
    delete_segment_row_fn = _strip_js_comments(
        _extract_function(source, "deleteSegmentRow")
    )
    apply_repeat_group_fn = _strip_js_comments(
        _extract_function(source, "applyRepeatGroup")
    )

    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _intl_number_format_stub()
        + "const currentLocale = 'en-US';\n"
        + "const mockElements = {};\n"
        + "let rowsRenderCount = 0;\n"
        # Each mock element tracks its own innerHTML through a real
        # getter/setter -- writes to plan-rows specifically bump
        # rowsRenderCount, which is the load-bearing signal in every test
        # below (the point is proving plan-rows is or is not rewritten).
        + "function makeEl(id) {\n"
        + "  let html = '';\n"
        + "  const el = { textContent: '', className: '', value: '', dataset: {}, disabled: false };\n"
        + "  Object.defineProperty(el, 'innerHTML', {\n"
        + "    get() { return html; },\n"
        + "    set(v) {\n"
        + "      html = v;\n"
        + "      if (id === 'plan-rows') rowsRenderCount += 1;\n"
        + "    },\n"
        + "  });\n"
        + "  return el;\n"
        + "}\n"
        + "function $(id) {\n"
        + "  if (!mockElements[id]) mockElements[id] = makeEl(id);\n"
        + "  return mockElements[id];\n"
        + "}\n"
        + f"const state = {{ race: {{}}, rows: {seed_rows_js}, rowsDirty: false }};\n"
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
        + render_plan_editor_fn
        + "\n"
        + render_plan_preview_fn
        + "\n"
        + repeat_segment_group_fn
        + "\n"
        + update_segment_duration_fn
        + "\n"
        + update_segment_target_watts_fn
        + "\n"
        + update_segment_kind_fn
        + "\n"
        + add_segment_row_fn
        + "\n"
        + delete_segment_row_fn
        + "\n"
        + apply_repeat_group_fn
        + "\n"
        + "function renderAll() {\n"
        + "  renderPlanEditor();\n"
        + "  renderPlanPreview();\n"
        + "}\n"
        # Simulate the initial mount, then zero the counter -- every
        # scenario below only cares about what happens from the handler
        # call onward.
        + "renderPlanEditor();\n"
        + "renderPlanPreview();\n"
        + "rowsRenderCount = 0;\n"
        + steps_js
        + "\n"
        + "console.log(JSON.stringify({\n"
        + "  rows: state.rows,\n"
        + "  rowsDirty: state.rowsDirty,\n"
        + "  rowsRenderCount: rowsRenderCount,\n"
        + "  planPreviewHtml: mockElements['plan-preview'].innerHTML,\n"
        + "  totalDurationText: mockElements['summary-total-duration'].textContent,\n"
        + "  row0ClassName: mockElements['segment-row-0'] ? mockElements['segment-row-0'].className : null,\n"
        + "}));\n"
    )
    return json.loads(_run_node(script))


SEED_ROWS = json.dumps(
    [
        {"kind": "warmup", "durationSec": 90, "targetWatts": None},
        {"kind": "work", "durationSec": 300, "targetWatts": 150},
    ]
)


# ---------------------------------------------------------------------------
# 1. The reported bug, pinned directly: a duration edit must not rewrite
# plan-rows at all, while the derived preview/total-duration readout DO
# reflect the new value -- proving the fix does not work by disabling the
# refresh path entirely.
# ---------------------------------------------------------------------------


def test_update_segment_duration_does_not_rewrite_plan_rows():
    result = _run_scenario(SEED_ROWS, "updateSegmentDuration(0, '45');\n")
    assert result["rowsRenderCount"] == 0
    assert result["rowsDirty"] is True
    assert result["rows"][0]["durationSec"] == 45
    # 45 + 300 = 345s = 05:45; the derived total-duration readout moved.
    assert result["totalDurationText"] == "05:45"


def test_update_segment_target_watts_does_not_rewrite_plan_rows():
    result = _run_scenario(SEED_ROWS, "updateSegmentTargetWatts(1, '275');\n")
    assert result["rowsRenderCount"] == 0
    assert result["rowsDirty"] is True
    assert result["rows"][1]["targetWatts"] == 275


def test_update_segment_target_watts_blank_clears_to_null_without_rewrite():
    result = _run_scenario(SEED_ROWS, "updateSegmentTargetWatts(1, '');\n")
    assert result["rowsRenderCount"] == 0
    assert result["rowsDirty"] is True
    assert result["rows"][1]["targetWatts"] is None


# ---------------------------------------------------------------------------
# 2. updateSegmentKind: a <select> change, not a text input, but it must
# still avoid a full plan-rows rebuild. It is allowed -- expected -- to
# patch its own row's className in place (the colour stripe depends on
# kind), and the derived preview must reflect the new kind too.
# ---------------------------------------------------------------------------


def test_update_segment_kind_does_not_rewrite_plan_rows():
    result = _run_scenario(SEED_ROWS, "updateSegmentKind(0, 'rest');\n")
    assert result["rowsRenderCount"] == 0
    assert result["rowsDirty"] is True
    assert result["rows"][0]["kind"] == "rest"


def test_update_segment_kind_patches_its_own_row_class_in_place():
    result = _run_scenario(SEED_ROWS, "updateSegmentKind(0, 'rest');\n")
    assert result["row0ClassName"] == "segment-row segment-row-rest"


def test_update_segment_kind_updates_the_derived_preview():
    before = _run_scenario(SEED_ROWS, "")
    after = _run_scenario(SEED_ROWS, "updateSegmentKind(0, 'rest');\n")
    assert "timeline-warmup" in before["planPreviewHtml"]
    assert "timeline-warmup" not in after["planPreviewHtml"]
    assert "timeline-rest" in after["planPreviewHtml"]


# ---------------------------------------------------------------------------
# 3. Complementary case: a genuine row-set change (add/delete/repeat) still
# does a full plan-rows rebuild -- the fix must not have simply disabled
# renderPlanEditor everywhere.
# ---------------------------------------------------------------------------


def test_add_segment_row_still_rewrites_plan_rows():
    result = _run_scenario(SEED_ROWS, "addSegmentRow();\n")
    assert result["rowsRenderCount"] == 1
    assert result["rowsDirty"] is True
    assert len(result["rows"]) == 3


def test_delete_segment_row_still_rewrites_plan_rows():
    result = _run_scenario(SEED_ROWS, "deleteSegmentRow(0);\n")
    assert result["rowsRenderCount"] == 1
    assert result["rowsDirty"] is True
    assert len(result["rows"]) == 1
    assert result["rows"][0]["kind"] == "work"


def test_apply_repeat_group_still_rewrites_plan_rows():
    steps = (
        "mockElements['repeat-from'] = { value: '1' };\n"
        "mockElements['repeat-to'] = { value: '2' };\n"
        "mockElements['repeat-times'] = { value: '2' };\n"
        "applyRepeatGroup();\n"
    )
    result = _run_scenario(SEED_ROWS, steps)
    assert result["rowsRenderCount"] == 1
    assert result["rowsDirty"] is True
    assert len(result["rows"]) == 4


# ---------------------------------------------------------------------------
# 4. Markup wiring: none of the tests above catch a missing oninput
# attribute, because every one of them calls the handlers directly
# (updateSegmentTargetWatts(1, "250")) rather than going through the
# rendered <input> element. oninput is load-bearing, not decorative: it is
# what puts a keystroke into state.rows the instant it happens. With only
# onchange wired, a value the coach is mid-typing never reaches state.rows
# (and so never sets rowsDirty) until the input loses focus. If the 4s
# refresh poll lands before that blur, applyRaceState() sees rowsDirty
# still false, treats the editor as untouched, and rebuilds plan-rows from
# the server plan -- wiping exactly what the coach was typing. That is the
# original reported bug, reachable again through a single event-attribute
# deletion even though every state-level test in this file (and the sibling
# dirty-flag file) stays green, because none of them render real markup and
# fire a real input event.
#
# These tests execute the real renderPlanEditor()/buildPlanEditorHtml()
# under node against a stubbed DOM, take the markup it actually produced,
# and assert on THAT string -- not on the page source -- since a source
# grep would also match a commented-out attribute (this repo has already
# been bitten by exactly that failure mode).
# ---------------------------------------------------------------------------


def _run_render_plan_editor_markup(rows_js: str) -> str:
    source = _read_class_admin()
    segment_kind_key_fn = _strip_js_comments(
        _extract_function(source, "segmentKindKey")
    )
    build_plan_editor_html_fn = _strip_js_comments(
        _extract_function(source, "buildPlanEditorHtml")
    )
    render_plan_editor_fn = _strip_js_comments(
        _extract_function(source, "renderPlanEditor")
    )

    script = (
        _t_stub()
        + _escape_html_stub()
        + "const mockElements = {};\n"
        + "function makeEl() { return { innerHTML: '' }; }\n"
        + "function $(id) {\n"
        + "  if (!mockElements[id]) mockElements[id] = makeEl();\n"
        + "  return mockElements[id];\n"
        + "}\n"
        + f"const state = {{ rows: {rows_js} }};\n"
        + segment_kind_key_fn
        + "\n"
        + build_plan_editor_html_fn
        + "\n"
        + render_plan_editor_fn
        + "\n"
        + "renderPlanEditor();\n"
        + "console.log(mockElements['plan-rows'].innerHTML);\n"
    )
    return _run_node(script)


WIRING_ROWS = json.dumps(
    [
        {"kind": "work", "durationSec": 300, "targetWatts": 150},
    ]
)


def _find_input_tag(html: str, handler_call: str) -> str:
    for tag in re.findall(r"<input[^>]*>", html):
        if handler_call in tag:
            return tag
    raise AssertionError(f"no <input> tag found calling {handler_call!r} in: {html}")


def test_duration_input_is_wired_to_oninput_and_onchange():
    html = _run_render_plan_editor_markup(WIRING_ROWS)
    tag = _find_input_tag(html, "updateSegmentDuration(0")
    assert 'oninput="updateSegmentDuration(0, this.value)"' in tag
    assert 'onchange="updateSegmentDuration(0, this.value)"' in tag


def test_target_watts_input_is_wired_to_oninput_and_onchange():
    html = _run_render_plan_editor_markup(WIRING_ROWS)
    tag = _find_input_tag(html, "updateSegmentTargetWatts(0")
    assert 'oninput="updateSegmentTargetWatts(0, this.value)"' in tag
    assert 'onchange="updateSegmentTargetWatts(0, this.value)"' in tag


def test_segment_kind_select_is_wired_to_onchange():
    html = _run_render_plan_editor_markup(WIRING_ROWS)
    select_match = re.search(r"<select[^>]*>", html)
    assert select_match, f"no <select> tag found in: {html}"
    assert 'onchange="updateSegmentKind(0, this.value)"' in select_match.group(0)
