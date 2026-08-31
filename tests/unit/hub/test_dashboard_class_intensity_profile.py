"""Tests for rendering the class board timeline on the dashboard
(`hub_server/static/index.html`) as an INTENSITY PROFILE -- height derived
from each segment's `target_watts`, mirroring the rule already implemented
in `hub_server/static/classAdmin.html`'s plan preview
(`computeBlockHeightPercents`, nested inside `computePlanSummary`).

Before this feature every timeline block was a fixed 12px tall inside a
16px-tall flex strip; only WIDTH (via flex-grow: duration_sec) varied. The
coach can already see the shape of a plan while editing it in Class Admin --
this closes the gap so athletes see the same shape on the projector wall.

Per CLAUDE.md's testability guidance, this feature area has already produced
four defects of the same shape: a helper whose result never reaches the DOM,
an entry point never driven, an event attribute never asserted, a computed
height never emitted. To close that gap this module:

  1. Executes the PURE height calculation (`computeBlockHeightPercents`,
     nested inside `buildClassBoardHtml` for the same reason
     `classTargetStatus` is nested there -- see
     tests/unit/hub/test_dashboard_class_target_watts.py's module docstring)
     directly under `node -e`.
  2. Drives the real `buildClassBoardHtml` end to end and asserts on the
     GENERATED markup: heights appear in the emitted block `style`
     attributes, the tallest block is the highest target, an untargeted
     segment gets the baseline, a plan with no targets yields uniform
     heights, and the current-segment border / completed-segment dimming
     survive alongside the new height.
  3. Pins that classAdmin.html and index.html compute the IDENTICAL height
     for the identical input, so the editor and the wall cannot drift apart.
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


def _node_display_name_stub() -> str:
    return (
        "const nodeDisplayName = (node) => {\n"
        "  return node?.node_display_name || node?.display_name || node?.node_id || '--';\n"
        "};\n"
    )


def _intl_number_format_stub() -> str:
    return "const Intl = { NumberFormat: function(locale) { return { format: (n) => String(n) }; } };\n"


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


# ---------------------------------------------------------------------------
# 1. computeBlockHeightPercents on the dashboard -- the pure height rule,
# executed directly. Mirrors tests/unit/hub/test_class_admin_plan_preview_height.py
# case-for-case so drift between the two files shows up immediately.
# ---------------------------------------------------------------------------


def _run_compute_block_height_percents(targets_js: str) -> list:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "computeBlockHeightPercents"))
    script = (
        fn
        + "\n"
        + f"const targets = {targets_js};\n"
        + "console.log(JSON.stringify(computeBlockHeightPercents(targets)));"
    )
    return json.loads(_run_node(script))


def test_no_targets_at_all_gives_uniform_full_height():
    result = _run_compute_block_height_percents("[null, null, null]")
    assert result == [100, 100, 100]


def test_empty_targets_list_gives_empty_result():
    result = _run_compute_block_height_percents("[]")
    assert result == []


def test_single_segment_with_a_target_is_full_height():
    result = _run_compute_block_height_percents("[150]")
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


# ---------------------------------------------------------------------------
# 2. buildClassBoardHtml wiring: proves the computed height actually reaches
# the emitted <div style="..."> for each timeline block, alongside the
# pre-existing width/border/opacity cues -- not merely that the pure helper
# above returns the right numbers.
# ---------------------------------------------------------------------------


def _run_build_class_board_html(session_data_js: str, clock_js: str) -> str:
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
    return _run_node(script)


def _timeline_block_styles(html: str) -> list:
    # Timeline blocks are the only markup in buildClassBoardHtml carrying
    # flex-grow -- a robust, unambiguous selector for just these divs among
    # the badges/cards/effort-bars also present in the rendered board.
    return re.findall(r'<div style="(flex-grow:[^"]*)"></div>', html)


def test_timeline_blocks_carry_height_in_style_alongside_width():
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "warmup", "duration_sec": 300, "target_watts": 90},
                    {"kind": "work", "duration_sec": 600, "target_watts": 250},
                    {"kind": "cooldown", "duration_sec": 300, "target_watts": 110},
                ]
            },
            "leaderboard": {},
        }
    )
    clock = json.dumps(
        {
            "index": 0,
            "kind": "warmup",
            "segmentRemainingMs": 300000,
            "totalRemainingMs": 1200000,
            "finished": False,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    styles = _timeline_block_styles(html)
    assert len(styles) == 3
    for style in styles:
        assert re.search(r"flex-grow:\d", style)
        assert re.search(r"height:[0-9.]+%", style), style


def test_tallest_timeline_block_is_the_highest_target():
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "warmup", "duration_sec": 300, "target_watts": 90},
                    {"kind": "work", "duration_sec": 600, "target_watts": 250},
                    {"kind": "cooldown", "duration_sec": 300, "target_watts": 110},
                ]
            },
            "leaderboard": {},
        }
    )
    clock = json.dumps(
        {
            "index": 0,
            "kind": "warmup",
            "segmentRemainingMs": 300000,
            "totalRemainingMs": 1200000,
            "finished": False,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    styles = _timeline_block_styles(html)
    heights = [float(re.search(r"height:([0-9.]+)%", s).group(1)) for s in styles]
    assert heights[1] == max(heights)
    assert heights[1] == 100.0


def test_untargeted_segment_among_targeted_ones_gets_baseline_height():
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "warmup", "duration_sec": 300},
                    {"kind": "work", "duration_sec": 600, "target_watts": 250},
                ]
            },
            "leaderboard": {},
        }
    )
    clock = json.dumps(
        {
            "index": 0,
            "kind": "warmup",
            "segmentRemainingMs": 300000,
            "totalRemainingMs": 900000,
            "finished": False,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    styles = _timeline_block_styles(html)
    heights = [float(re.search(r"height:([0-9.]+)%", s).group(1)) for s in styles]
    assert heights[1] == 100.0
    assert 0 < heights[0] < heights[1]


def test_plan_with_no_targets_at_all_renders_uniform_heights():
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "warmup", "duration_sec": 300},
                    {"kind": "work", "duration_sec": 600},
                    {"kind": "cooldown", "duration_sec": 300},
                ]
            },
            "leaderboard": {},
        }
    )
    clock = json.dumps(
        {
            "index": 0,
            "kind": "warmup",
            "segmentRemainingMs": 300000,
            "totalRemainingMs": 1200000,
            "finished": False,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    styles = _timeline_block_styles(html)
    heights = [float(re.search(r"height:([0-9.]+)%", s).group(1)) for s in styles]
    assert heights == [100.0, 100.0, 100.0]


def test_current_segment_border_and_completed_dimming_survive_alongside_height():
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "warmup", "duration_sec": 300, "target_watts": 90},
                    {"kind": "work", "duration_sec": 600, "target_watts": 250},
                    {"kind": "cooldown", "duration_sec": 300, "target_watts": 110},
                ]
            },
            "leaderboard": {},
        }
    )
    # index 1 is current: segment 0 must be dimmed (completed), segment 1
    # must carry the current-segment highlight border, segment 2 neither.
    clock = json.dumps(
        {
            "index": 1,
            "kind": "work",
            "segmentRemainingMs": 600000,
            "totalRemainingMs": 900000,
            "finished": False,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    styles = _timeline_block_styles(html)
    assert len(styles) == 3

    assert "opacity:0.4" in styles[0]
    assert "2px solid var(--volt-yellow)" not in styles[0]

    assert "2px solid var(--volt-yellow)" in styles[1]
    assert "opacity:0.4" not in styles[1]
    assert re.search(r"height:[0-9.]+%", styles[1])

    assert "opacity:0.4" not in styles[2]
    assert "2px solid var(--volt-yellow)" not in styles[2]


# ---------------------------------------------------------------------------
# 3. Editor/wall parity: classAdmin.html's computeBlockHeightPercents (the
# reference implementation, nested inside computePlanSummary) and
# index.html's computeBlockHeightPercents (nested inside buildClassBoardHtml)
# must return the IDENTICAL array for the identical input -- so the coach's
# preview and the projector wall can never show a different profile for the
# same plan.
# ---------------------------------------------------------------------------


def _run_class_admin_compute_block_height_percents(targets_js: str) -> list:
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "computeBlockHeightPercents"))
    script = (
        fn
        + "\n"
        + f"const targets = {targets_js};\n"
        + "console.log(JSON.stringify(computeBlockHeightPercents(targets)));"
    )
    return json.loads(_run_node(script))


def test_dashboard_and_class_admin_compute_the_same_height_for_the_same_plan():
    cases = [
        "[null, null, null]",
        "[150]",
        "[200, 200, 200]",
        "[200, null]",
        "[50, 100]",
        "[1, 2000]",
        "[90, 250, 110]",
    ]
    for targets_js in cases:
        dashboard_result = _run_compute_block_height_percents(targets_js)
        class_admin_result = _run_class_admin_compute_block_height_percents(targets_js)
        assert dashboard_result == class_admin_result, (
            f"dashboard and classAdmin disagree for targets={targets_js}: "
            f"dashboard={dashboard_result}, classAdmin={class_admin_result}"
        )
