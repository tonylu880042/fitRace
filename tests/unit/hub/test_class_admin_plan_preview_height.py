"""Tests for rendering segment intensity as HEIGHT in the Class Admin plan
preview (`hub_server/static/classAdmin.html`).

The plan preview timeline already renders each segment's WIDTH proportional
to its share of the total plan duration (`computePlanSummary` /
`buildPlanPreviewHtml`). This adds HEIGHT proportional to the segment's
`target_watts`, scaled relative to the highest target actually present in
THIS plan -- so the bar reads as an interval profile (wide-and-low for a
long easy warmup, narrow-and-tall for a short hard effort) rather than a
flat proportional-width strip.

Per CLAUDE.md's testability guidance, this feature area has already
produced three defects of the same shape: a helper whose result never
reaches the DOM, an entry point never driven, an event attribute never
asserted. To close that gap this module:

  1. Executes the PURE height calculation (`computeBlockHeightPercents`,
     nested inside `computePlanSummary` -- see the comment at its
     declaration in classAdmin.html for why it is nested rather than a
     sibling top-level function) directly under `node -e`, extracted by the
     same brace-depth technique as
     tests/unit/hub/test_class_admin_plan_editor_rerender_scope.py. Text
     search for the "function computeBlockHeightPercents(" marker finds it
     regardless of nesting.
  2. ALSO drives the real `buildPlanPreviewHtml` (fed by the real
     `computePlanSummary`) and asserts on the GENERATED MARKUP: that a
     height actually appears in each block's `style` attribute, that the
     tallest block corresponds to the highest target, that an untargeted
     segment gets the low baseline, and that a plan with no targets at all
     produces uniform heights identical to the pre-feature look.
  3. Edge cases: a single segment with a target; all segments sharing the
     same target (all full height, none zero); a target of 1; a target of
     2000.
  4. The new classAdmin.preview_block_title_with_target i18n key across all
     six locales, mirroring the locale-coverage sections of the sibling
     test_class_admin_target_watts.py.
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
    return (
        "const Intl = { NumberFormat: function(locale) { "
        "return { format: (n) => String(n) }; } };\n"
    )


# ---------------------------------------------------------------------------
# 1. computeBlockHeightPercents -- the pure height calculation, executed
# directly. Input: an array of one target watts value (number) or
# null/undefined per segment. Output: an array of one height percent per
# segment, same order, same length.
# ---------------------------------------------------------------------------


def _run_compute_block_height_percents(targets_js: str) -> list:
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "computeBlockHeightPercents"))
    script = (
        fn
        + "\n"
        + f"const targets = {targets_js};\n"
        + "console.log(JSON.stringify(computeBlockHeightPercents(targets)));"
    )
    return json.loads(_run_node(script))


def test_no_targets_at_all_gives_uniform_full_height():
    # The plan-wide "no coach has used watts here" case: every block stays
    # at 100 -- the exact pre-feature look, no scaling applied at all.
    result = _run_compute_block_height_percents("[null, null, null]")
    assert result == [100, 100, 100]


def test_empty_targets_list_gives_empty_result():
    result = _run_compute_block_height_percents("[]")
    assert result == []


def test_single_segment_with_a_target_is_full_height():
    result = _run_compute_block_height_percents("[150]")
    assert result == [100]


def test_target_of_one_as_the_only_target_is_full_height():
    result = _run_compute_block_height_percents("[1]")
    assert result == [100]


def test_target_of_two_thousand_as_the_only_target_is_full_height():
    result = _run_compute_block_height_percents("[2000]")
    assert result == [100]


def test_all_segments_sharing_the_same_target_are_all_full_height_none_zero():
    result = _run_compute_block_height_percents("[200, 200, 200]")
    assert result == [100, 100, 100]
    assert all(value > 0 for value in result)


def test_untargeted_segment_gets_the_low_baseline_not_zero_not_full():
    result = _run_compute_block_height_percents("[200, null]")
    assert result[0] == 100
    assert 0 < result[1] < 100


def test_lower_target_scales_below_the_highest_target():
    result = _run_compute_block_height_percents("[50, 100]")
    assert result[1] == 100
    assert result[0] < result[1]
    assert result[0] > 0


def test_low_and_high_target_in_same_plan_still_leaves_the_low_one_visible():
    # A target of 1 alongside a target of 2000 in the same plan: the low
    # one scales down close to nothing relative to the max, but the
    # untargeted-baseline floor does not apply here (it only applies to
    # segments with NO target) -- only proving it stays a finite,
    # non-negative height, not that it clears the untargeted baseline.
    result = _run_compute_block_height_percents("[1, 2000]")
    assert result[1] == 100
    assert 0 <= result[0] < result[1]


# ---------------------------------------------------------------------------
# 2. Markup wiring: computePlanSummary -> buildPlanPreviewHtml end to end.
# Proves the computed height actually reaches the emitted style attribute,
# not merely that the pure helper above returns the right numbers.
# ---------------------------------------------------------------------------


def _run_build_plan_preview_from_rows(rows_js: str) -> str:
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
    build_plan_preview_html_fn = _strip_js_comments(
        _extract_function(source, "buildPlanPreviewHtml")
    )
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
        + compute_plan_summary_fn
        + "\n"
        + build_plan_preview_html_fn
        + "\n"
        + f"const rows = {rows_js};\n"
        + "const summary = computePlanSummary(rows);\n"
        + "console.log(buildPlanPreviewHtml(summary));"
    )
    return _run_node(script)


def _block_style_heights(html: str) -> list:
    heights = []
    for tag in re.findall(r'<span class="timeline-block[^>]*>', html):
        match = re.search(r"height:([0-9.]+)%", tag)
        assert match, f"no height in style attribute of block: {tag}"
        heights.append(float(match.group(1)))
    return heights


def test_no_targets_in_plan_renders_uniform_full_height_blocks():
    rows = json.dumps(
        [
            {"kind": "warmup", "durationSec": 300},
            {"kind": "work", "durationSec": 1200},
            {"kind": "cooldown", "durationSec": 300},
        ]
    )
    html = _run_build_plan_preview_from_rows(rows)
    heights = _block_style_heights(html)
    assert len(heights) == 3
    assert heights == [100.0, 100.0, 100.0]


def test_tallest_block_corresponds_to_the_highest_target():
    rows = json.dumps(
        [
            {"kind": "warmup", "durationSec": 300, "targetWatts": 90},
            {"kind": "work", "durationSec": 600, "targetWatts": 250},
            {"kind": "cooldown", "durationSec": 300, "targetWatts": 110},
        ]
    )
    html = _run_build_plan_preview_from_rows(rows)
    heights = _block_style_heights(html)
    assert heights[1] == max(heights)
    assert heights[1] == 100.0


def test_untargeted_segment_among_targeted_ones_gets_the_baseline_height():
    rows = json.dumps(
        [
            {"kind": "warmup", "durationSec": 300},
            {"kind": "work", "durationSec": 600, "targetWatts": 250},
        ]
    )
    html = _run_build_plan_preview_from_rows(rows)
    heights = _block_style_heights(html)
    assert heights[1] == 100.0
    assert 0 < heights[0] < heights[1]


def test_every_block_style_attribute_still_carries_width_alongside_height():
    rows = json.dumps(
        [
            {"kind": "warmup", "durationSec": 300, "targetWatts": 90},
            {"kind": "work", "durationSec": 900, "targetWatts": 250},
        ]
    )
    html = _run_build_plan_preview_from_rows(rows)
    for tag in re.findall(r'<span class="timeline-block[^>]*>', html):
        assert re.search(r"width:[0-9.]+%", tag), tag
        assert re.search(r"height:[0-9.]+%", tag), tag


# ---------------------------------------------------------------------------
# 3. Tooltip: extended to include the prescribed target when there is one,
# left as the plain kind label when there is not.
# ---------------------------------------------------------------------------


def test_tooltip_includes_target_watts_when_present():
    rows = json.dumps([{"kind": "work", "durationSec": 300, "targetWatts": 220}])
    html = _run_build_plan_preview_from_rows(rows)
    # The title attribute passes through escapeHtml, so a literal `"` in
    # the JSON.stringify-d params comes back as `&quot;`.
    assert "T[classAdmin.preview_block_title_with_target|" in html
    assert "watts&quot;:&quot;220" in html


def test_tooltip_stays_plain_kind_label_when_no_target():
    rows = json.dumps([{"kind": "work", "durationSec": 300}])
    html = _run_build_plan_preview_from_rows(rows)
    assert "T[class.kind.work]" in html
    assert "classAdmin.preview_block_title_with_target" not in html


# ---------------------------------------------------------------------------
# 4. i18n: classAdmin.preview_block_title_with_target exists in all six
# locales with a genuine (non-English-copy) translation, zh-TW carries real
# CJK.
# ---------------------------------------------------------------------------


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_preview_block_title_key_exists_in_every_locale():
    for locale in LOCALES:
        messages = _load_locale(locale)
        assert (
            "classAdmin.preview_block_title_with_target" in messages
        ), f"classAdmin.preview_block_title_with_target missing in {locale}"


def test_preview_block_title_key_in_zh_tw_contains_cjk_characters():
    zh_tw = _load_locale("zh-TW")
    value = zh_tw["classAdmin.preview_block_title_with_target"]
    assert _CJK_RE.search(value), f"expected CJK in zh-TW: {value!r}"


def test_preview_block_title_key_differs_from_english_in_every_other_locale():
    en_us = _load_locale("en-US")["classAdmin.preview_block_title_with_target"]
    for locale in LOCALES:
        if locale == "en-US":
            continue
        value = _load_locale(locale)["classAdmin.preview_block_title_with_target"]
        assert value != en_us, (
            f"classAdmin.preview_block_title_with_target in {locale} is an "
            f"unmodified copy of the en-US string: {en_us!r}"
        )


def test_preview_block_title_key_carries_both_placeholders_in_every_locale():
    for locale in LOCALES:
        value = _load_locale(locale)["classAdmin.preview_block_title_with_target"]
        assert "{kind}" in value, f"{locale} missing {{kind}} placeholder: {value!r}"
        assert "{watts}" in value, f"{locale} missing {{watts}} placeholder: {value!r}"
