"""Tests for the new "changeover" segment kind reaching the UI, on both
Class Admin (`hub_server/static/classAdmin.html`) and the dashboard class
board (`hub_server/static/index.html`).

Per CLAUDE.md's testability guidance, the recurring defect in this feature
area is a correct helper whose result never reaches the DOM/payload. To
close that gap this module executes the real page functions under
`node -e` (same brace-depth extraction technique used throughout
tests/unit/hub/) and asserts on GENERATED MARKUP, not on source text:

  1. classAdmin.html: the plan-editor dropdown offers "changeover" as an
     option, and a changeover row gets its own colour stripe class,
     distinct from the other four kinds.
  2. classAdmin.html: the plan-preview timeline gives a changeover block
     its own `timeline-changeover` colour class.
  3. index.html: buildClassBoardHtml renders a changeover segment with its
     own colour in the intensity-profile timeline block (distinct from
     work/rest/warmup/cooldown), its own label in the hero, and an
     instruction line under the segment name telling athletes to move to
     the next machine.
  4. index.html: a rest segment also gets an instruction line (recovery
     wording); work/warmup/cooldown segments get none.
  5. A changeover segment with no target_watts is treated exactly like any
     other untargeted segment: baseline timeline height, no target
     indicator on station cards, no watts in the next-segment
     announcement.
  6. The new class.kind.changeover / class.instruction_rest /
     class.instruction_changeover i18n keys exist in all six locales with
     genuine, non-English-copy translations.
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
_CJK_RE = re.compile(r"[一-鿿぀-ゟ゠-ヿ가-힯]")

LOCALES = ["de-CH", "en-US", "fr", "it", "sv", "zh-TW"]


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read_class_admin() -> str:
    return (STATIC_DIR / "classAdmin.html").read_text(encoding="utf-8")


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
# 1 & 2. classAdmin.html -- dropdown option and colour stripes.
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


def test_plan_editor_dropdown_offers_changeover_option():
    rows = '[{"kind": "work", "durationSec": 300}]'
    html = _run_build_plan_editor_html(rows)
    assert '<option value="changeover"' in html


def test_plan_editor_row_gets_its_own_colour_stripe_class():
    rows = '[{"kind": "changeover", "durationSec": 45}]'
    html = _run_build_plan_editor_html(rows)
    assert 'class="segment-row segment-row-changeover"' in html
    # Distinct from the other four kinds
    for other in ("work", "rest", "warmup", "cooldown"):
        assert f'segment-row-{other}"' not in html


def test_plan_editor_changeover_option_is_selected_when_row_kind_matches():
    rows = '[{"kind": "changeover", "durationSec": 45}]'
    html = _run_build_plan_editor_html(rows)
    assert '<option value="changeover" selected>' in html


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


def _run_build_plan_preview_html(summary_js: str) -> str:
    source = _read_class_admin()
    segment_kind_key_fn = _strip_js_comments(
        _extract_function(source, "segmentKindKey")
    )
    fn = _strip_js_comments(_extract_function(source, "buildPlanPreviewHtml"))
    script = (
        _t_stub()
        + _escape_html_stub()
        + _intl_number_format_stub()
        + "function formatDurationClock(sec) { return String(sec); }\n"
        + "const currentLocale = 'en-US';\n"
        + segment_kind_key_fn
        + "\n"
        + fn
        + "\n"
        + f"const summary = {summary_js};\n"
        + "console.log(buildPlanPreviewHtml(summary));"
    )
    return _run_node(script)


def test_plan_preview_timeline_gets_its_own_colour_class_for_changeover():
    rows = json.dumps(
        [
            {"kind": "work", "durationSec": 300, "targetWatts": 200},
            {"kind": "changeover", "durationSec": 30},
        ]
    )
    summary = _run_compute_plan_summary(rows)
    html = _run_build_plan_preview_html(json.dumps(summary))
    assert 'class="timeline-block timeline-changeover"' in html


# ---------------------------------------------------------------------------
# 3 & 4 & 5. index.html -- buildClassBoardHtml end to end.
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
    return re.findall(r'<div style="(flex-grow:[^"]*)"></div>', html)


_CHANGEOVER_SESSION_DATA = json.dumps(
    {
        "class_plan": {
            "segments": [
                {"kind": "work", "duration_sec": 300, "target_watts": 200},
                {"kind": "changeover", "duration_sec": 45},
                {"kind": "work", "duration_sec": 300, "target_watts": 200},
            ]
        },
        "leaderboard": {
            "node-1": {
                "node_id": "node-1",
                "station_number": 1,
                "athlete_name": "Athlete One",
                "power_watts": 250,
            }
        },
    }
)


def _changeover_clock():
    return json.dumps(
        {
            "index": 1,
            "kind": "changeover",
            "segmentRemainingMs": 30000,
            "totalRemainingMs": 630000,
            "finished": False,
        }
    )


def test_changeover_timeline_block_has_its_own_colour_distinct_from_others():
    html = _run_build_class_board_html(_CHANGEOVER_SESSION_DATA, _changeover_clock())
    styles = _timeline_block_styles(html)
    assert len(styles) == 3
    background_colors = [
        re.search(r"background:([^;]+);", style).group(1) for style in styles
    ]
    # The changeover block (index 1) uses its own background colour var,
    # distinct from work's (0 and 2).
    assert background_colors[1] == "var(--changeover-violet)"
    assert background_colors[0] == "var(--volt-yellow)"
    assert background_colors[1] != background_colors[0]


def test_changeover_hero_shows_its_own_kind_label():
    html = _run_build_class_board_html(_CHANGEOVER_SESSION_DATA, _changeover_clock())
    assert "T[class.kind.changeover]" in html


def test_changeover_instruction_line_reaches_the_dom():
    html = _run_build_class_board_html(_CHANGEOVER_SESSION_DATA, _changeover_clock())
    assert 'class="class-hero-instruction"' in html
    assert "T[class.instruction_changeover]" in html


def test_rest_instruction_line_reaches_the_dom():
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "work", "duration_sec": 300},
                    {"kind": "rest", "duration_sec": 60},
                ]
            },
            "leaderboard": {},
        }
    )
    clock = json.dumps(
        {
            "index": 1,
            "kind": "rest",
            "segmentRemainingMs": 30000,
            "totalRemainingMs": 30000,
            "finished": False,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    assert 'class="class-hero-instruction"' in html
    assert "T[class.instruction_rest]" in html


def test_work_warmup_cooldown_segments_get_no_instruction_line():
    for kind in ("work", "warmup", "cooldown"):
        session_data = json.dumps(
            {
                "class_plan": {"segments": [{"kind": kind, "duration_sec": 300}]},
                "leaderboard": {},
            }
        )
        clock = json.dumps(
            {
                "index": 0,
                "kind": kind,
                "segmentRemainingMs": 30000,
                "totalRemainingMs": 30000,
                "finished": False,
            }
        )
        html = _run_build_class_board_html(session_data, clock)
        assert (
            'class="class-hero-instruction"' not in html
        ), f"{kind} must not render an instruction line"


def test_changeover_with_no_target_gets_baseline_height_not_zero_not_full():
    html = _run_build_class_board_html(_CHANGEOVER_SESSION_DATA, _changeover_clock())
    styles = _timeline_block_styles(html)
    heights = [float(re.search(r"height:([0-9.]+)%", s).group(1)) for s in styles]
    # Both work segments carry a target and are the max -> full height.
    assert heights[0] == 100.0
    assert heights[2] == 100.0
    # The untargeted changeover segment gets the low baseline, not zero.
    assert 0 < heights[1] < 100.0


def test_changeover_station_card_never_shows_a_target_indicator():
    html = _run_build_class_board_html(_CHANGEOVER_SESSION_DATA, _changeover_clock())
    assert "class-target-indicator" not in html


def test_changeover_next_segment_announcement_carries_no_watts():
    # Ten seconds or fewer remaining in the CURRENT segment (work, index 0)
    # with an untargeted changeover as the next segment -- the announcement
    # must show the changeover kind but no watts figure.
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "work", "duration_sec": 300, "target_watts": 200},
                    {"kind": "changeover", "duration_sec": 45},
                ]
            },
            "leaderboard": {},
        }
    )
    clock = json.dumps(
        {
            "index": 0,
            "kind": "work",
            "segmentRemainingMs": 5000,
            "totalRemainingMs": 305000,
            "finished": False,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    assert 'class="class-next-announcement" style="display:block;' in html
    assert "T[class.next_segment_announcement|" in html
    assert "T[class.kind.changeover]" in html
    assert (
        'class="class-next-announcement-watts" style="font-family:Oswald,sans-serif;font-size:1.75rem;font-weight:700;color:var(--hazard-amber);margin-top:0.25rem;"></div>'
        in html
    )


# ---------------------------------------------------------------------------
# 6. i18n coverage.
# ---------------------------------------------------------------------------


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as f:
        return json.load(f)


_NEW_KEYS = [
    "class.kind.changeover",
    "class.instruction_rest",
    "class.instruction_changeover",
]


def test_new_keys_exist_in_all_locales():
    for locale in LOCALES:
        messages = _load_locale(locale)
        for key in _NEW_KEYS:
            assert key in messages, f"Missing {key} in {locale}"


def test_new_keys_in_zh_tw_contain_cjk_characters():
    messages = _load_locale("zh-TW")
    for key in _NEW_KEYS:
        value = messages[key]
        assert _CJK_RE.search(value), f"{key} in zh-TW should contain CJK: {value}"


def test_new_keys_are_not_english_copies_across_locales():
    en_us = _load_locale("en-US")
    for locale in ("de-CH", "fr", "it", "sv"):
        messages = _load_locale(locale)
        for key in _NEW_KEYS:
            assert messages[key] != en_us[key], (
                f"{key} in {locale} is an unmodified copy of the en-US "
                f"string: {en_us[key]!r}"
            )
