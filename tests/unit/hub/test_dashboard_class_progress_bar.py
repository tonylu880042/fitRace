"""Tests for the overall class PROGRESS BAR drawn under the intensity
profile on the dashboard (`hub_server/static/index.html`).

The intensity profile (one block per segment, width by duration, height by
target watts) only ever tells an athlete about the shape of the plan and
which segment is current -- it says nothing about how far through the WHOLE
class they are as a single continuous position. This bar fills left to
right in proportion to elapsed time over the plan's total duration, derived
from the same `classClockAt` fields the big countdown above already uses
(`totalRemainingMs`, `finished`) plus the plan's total duration -- so the
bar can never disagree with the countdown.

Per CLAUDE.md's testability guidance this feature area has already produced
five defects of the same shape: a helper whose result never reached the
DOM, an entry point never driven, an event attribute never asserted, a
computed height never emitted, a claim of identical implementations that
was not actually identical. To close that gap this module:

  1. Executes the PURE fraction calculation (`computeClassProgressPercent`,
     nested inside `buildClassBoardHtml` for the same reason
     `computeBlockHeightPercents` is nested there -- see
     tests/unit/hub/test_dashboard_class_intensity_profile.py's module
     docstring) directly under `node -e`.
  2. Drives the real `buildClassBoardHtml` end to end and asserts the fill
     width actually appears in the generated markup, and that the
     intensity profile above it is unchanged -- heights, the
     current-segment border and completed-segment dimming still emitted.
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
# 1. computeClassProgressPercent -- the pure fraction rule, executed
# directly. It needs metricNumber, so the stub is supplied same as the
# real file provides it at top level.
# ---------------------------------------------------------------------------


def _run_compute_class_progress_percent(
    plan_total_ms, total_remaining_ms, finished
) -> float:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "computeClassProgressPercent"))
    script = (
        _metric_number_stub()
        + fn
        + "\n"
        + f"console.log(computeClassProgressPercent({plan_total_ms}, {total_remaining_ms}, {json.dumps(finished)}));"
    )
    return float(_run_node(script))


def test_zero_elapsed_reads_empty():
    result = _run_compute_class_progress_percent(1200000, 1200000, False)
    assert result == 0


def test_mid_plan_reads_the_matching_fraction():
    result = _run_compute_class_progress_percent(1200000, 600000, False)
    assert result == 50


def test_exactly_at_the_end_reads_full():
    result = _run_compute_class_progress_percent(1200000, 0, False)
    assert result == 100


def test_past_the_end_clamps_to_full_never_exceeds():
    result = _run_compute_class_progress_percent(1200000, 0, True)
    assert result == 100


def test_single_segment_plan_scales_the_same_way():
    result = _run_compute_class_progress_percent(300000, 225000, False)
    assert result == 25


# ---------------------------------------------------------------------------
# 2. buildClassBoardHtml wiring: proves the computed fraction actually
# reaches the emitted progress-bar fill width, and that the intensity
# profile above it (heights, current-segment border, completed dimming)
# is unchanged by this feature.
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


def _progress_fill_width(html: str) -> float:
    match = re.search(r'class="class-progress-fill" style="[^"]*width:([0-9.]+)%', html)
    assert match, html
    return float(match.group(1))


def _timeline_block_styles(html: str) -> list:
    return re.findall(r'<div style="(flex-grow:[^"]*)"></div>', html)


_THREE_SEGMENT_PLAN = {
    "class_plan": {
        "segments": [
            {"kind": "warmup", "duration_sec": 300, "target_watts": 90},
            {"kind": "work", "duration_sec": 600, "target_watts": 250},
            {"kind": "cooldown", "duration_sec": 300, "target_watts": 110},
        ]
    },
    "leaderboard": {},
}


def test_progress_fill_width_appears_and_matches_elapsed_fraction():
    session_data = json.dumps(_THREE_SEGMENT_PLAN)
    clock = json.dumps(
        {
            "index": 1,
            "kind": "work",
            "segmentRemainingMs": 300000,
            "totalRemainingMs": 600000,
            "finished": False,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    assert _progress_fill_width(html) == 50


def test_progress_bar_is_empty_before_the_class_starts():
    session_data = json.dumps(_THREE_SEGMENT_PLAN)
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
    assert _progress_fill_width(html) == 0


def test_progress_bar_is_full_and_capped_once_the_plan_is_finished():
    session_data = json.dumps(_THREE_SEGMENT_PLAN)
    clock = json.dumps(
        {
            "index": 2,
            "kind": "cooldown",
            "segmentRemainingMs": 0,
            "totalRemainingMs": 0,
            "finished": True,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    assert _progress_fill_width(html) == 100


def test_intensity_profile_above_the_bar_is_unchanged():
    session_data = json.dumps(_THREE_SEGMENT_PLAN)
    # index 1 is current: segment 0 must be dimmed (completed), segment 1
    # must carry the current-segment highlight border, segment 2 neither --
    # same fixture and assertions as the pre-existing intensity profile
    # suite, run again here to pin that this feature did not disturb them.
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
    assert re.search(r"height:[0-9.]+%", styles[0])

    assert "2px solid var(--volt-yellow)" in styles[1]
    assert "opacity:0.4" not in styles[1]
    assert re.search(r"height:[0-9.]+%", styles[1])

    assert "opacity:0.4" not in styles[2]
    assert "2px solid var(--volt-yellow)" not in styles[2]
