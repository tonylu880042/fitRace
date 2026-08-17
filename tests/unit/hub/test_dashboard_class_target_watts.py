"""Tests for showing the prescribed power and who is meeting it on the
class board (`hub_server/static/index.html`).

Per CLAUDE.md's testability guidance, the recurring defect in this codebase
is a correct pure helper whose result never reaches the DOM, caught only by
a test that greps the source for a function name. To close that gap this
module:

  1. Executes classTargetStatus -- the pure on/under/no-indicator decision
     -- under `node -e`, extracted from the page by the same brace-depth
     technique tests/unit/hub/test_dashboard_class_board.py uses. It is
     declared as a nested function INSIDE buildClassBoardHtml (so that
     function's existing extraction-only tests, which this module must not
     modify, keep working without a new stub) but the same
     source.index("function classTargetStatus(") marker technique finds
     and extracts it regardless of nesting, so it is independently
     unit-tested here too.
  2. Drives the actual render path (buildClassBoardHtml) with real
     leaderboard data and asserts the target actually lands in the hero
     band and the correct indicator lands on the correct station card by
     ACTUAL POSITION in the rendered HTML string -- not merely that the
     function is called somewhere.
  3. Covers the new class.target_watts / class.target_met / class.target_
     under i18n keys across all six locales.

This file does not modify tests/unit/hub/test_dashboard_class_board.py.
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
    # Mirrors the informative stub in test_class_admin_target_watts.py:
    # plain T[key] with no params, T[key|{"name":"value"}] with params, so
    # a test can prove a parameterised label's params actually reached
    # t(), not merely that some key was passed.
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


# ---------------------------------------------------------------------------
# 1. classTargetStatus -- the pure decision, executed standalone.
# ---------------------------------------------------------------------------


def _run_class_target_status(power_watts_js: str, target_watts_js: str) -> str:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "classTargetStatus"))
    script = (
        fn
        + "\n"
        + f"console.log(classTargetStatus({power_watts_js}, {target_watts_js}));"
    )
    return _run_node(script)


def test_class_target_status_at_target_is_on():
    assert _run_class_target_status("200", "200") == "on"


def test_class_target_status_above_target_is_on():
    assert _run_class_target_status("250", "200") == "on"


def test_class_target_status_below_target_is_under():
    assert _run_class_target_status("150", "200") == "under"


def test_class_target_status_power_null_with_target_present_is_none():
    assert _run_class_target_status("null", "200") == "none"


def test_class_target_status_power_undefined_with_target_present_is_none():
    assert _run_class_target_status("undefined", "200") == "none"


def test_class_target_status_no_target_at_all_is_none():
    assert _run_class_target_status("50", "null") == "none"


def test_class_target_status_no_target_and_no_power_is_none():
    assert _run_class_target_status("undefined", "undefined") == "none"


def test_class_target_status_zero_power_below_positive_target_is_under():
    # Zero is a real, reported value (distinct from null/undefined) -- a
    # station reporting 0W with a target set must read as under, not none.
    assert _run_class_target_status("0", "150") == "under"


# ---------------------------------------------------------------------------
# 2. buildClassBoardHtml render path -- hero band.
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


def test_hero_shows_target_watts_when_segment_has_a_target():
    session_data = json.dumps(
        {
            "class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
            "leaderboard": {},
        }
    )
    clock = json.dumps(
        {
            "index": 0,
            "kind": "work",
            "segmentRemainingMs": 300000,
            "totalRemainingMs": 300000,
            "finished": False,
            "targetWatts": 180,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    # escapeHtml() runs on every t() result in this render path (matching
    # every other hero-band label), so the quotes in the stub's JSON
    # params come back HTML-entity-escaped.
    assert "T[class.target_watts|{&quot;watts&quot;:&quot;180&quot;}]" in html
    assert "class-hero-target" in html


def test_hero_has_no_target_markup_when_segment_has_no_target():
    session_data = json.dumps(
        {
            "class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
            "leaderboard": {},
        }
    )
    clock = json.dumps(
        {
            "index": 0,
            "kind": "work",
            "segmentRemainingMs": 300000,
            "totalRemainingMs": 300000,
            "finished": False,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    assert "class.target_watts" not in html
    assert "class-hero-target" not in html


# ---------------------------------------------------------------------------
# 3. buildClassBoardHtml render path -- per-station indicator, asserted by
# ACTUAL POSITION so this can only pass if the right athlete gets the right
# indicator, not merely that both classes appear somewhere in the page.
# ---------------------------------------------------------------------------


def _three_station_leaderboard() -> dict:
    return {
        "n1": {
            "node_id": "n1",
            "athlete_name": "Ash",
            "station_number": 1,
            "power_watts": 200,
            "instantaneous_speed_kph": 20,
            "distance_m": 400,
        },
        "n2": {
            "node_id": "n2",
            "athlete_name": "Blair",
            "station_number": 2,
            "power_watts": 100,
            "instantaneous_speed_kph": 15,
            "distance_m": 300,
        },
        # Casey never reports power at all -- a treadmill station. No
        # power_watts key whatsoever, not even null.
        "n3": {
            "node_id": "n3",
            "athlete_name": "Casey",
            "station_number": 3,
            "instantaneous_speed_kph": 10,
            "distance_m": 900,
        },
    }


def _run_three_station_board(target_watts: int) -> str:
    session_data = json.dumps(
        {
            "class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
            "leaderboard": _three_station_leaderboard(),
        }
    )
    clock = json.dumps(
        {
            "index": 0,
            "kind": "work",
            "segmentRemainingMs": 300000,
            "totalRemainingMs": 300000,
            "finished": False,
            "targetWatts": target_watts,
        }
    )
    return _run_build_class_board_html(session_data, clock)


def test_station_at_or_above_target_gets_on_target_indicator_at_its_own_card():
    # Target is 200: Ash (200W) is on target, Blair (100W) is under, Casey
    # reports no power at all and must show neither.
    html = _run_three_station_board(200)
    for name in ("Ash", "Blair", "Casey"):
        assert name in html, f"{name} missing from rendered class board HTML"

    ash_pos = html.index("Ash")
    blair_pos = html.index("Blair")
    casey_pos = html.index("Casey")
    assert ash_pos < blair_pos < casey_pos

    on_positions = [m.start() for m in re.finditer(r"class-target-on\b", html)]
    under_positions = [m.start() for m in re.finditer(r"class-target-under\b", html)]

    assert (
        len(on_positions) == 1
    ), f"expected exactly one on-target indicator, got {on_positions}"
    assert (
        len(under_positions) == 1
    ), f"expected exactly one under-target indicator, got {under_positions}"

    # The "on" indicator must sit within Ash's card (after Ash's name,
    # before Blair's), and the "under" indicator within Blair's card
    # (after Blair's name, before Casey's) -- never on the wrong athlete.
    assert ash_pos < on_positions[0] < blair_pos, (
        f"on-target indicator at {on_positions[0]} is not inside Ash's card "
        f"(Ash={ash_pos}, Blair={blair_pos})"
    )
    assert blair_pos < under_positions[0] < casey_pos, (
        f"under-target indicator at {under_positions[0]} is not inside "
        f"Blair's card (Blair={blair_pos}, Casey={casey_pos})"
    )


def test_station_reporting_no_power_never_gets_under_target_indicator():
    # The load-bearing negative case from the spec: a treadmill (no
    # power_watts at all) must never be flagged under-target, even though
    # every other station is below the target.
    html = _run_three_station_board(9999)
    casey_pos = html.index("Casey")
    # Nothing after Casey's name in the whole rest of the document may be
    # an under-target indicator belonging to Casey's card -- Casey is the
    # last station, so this is simply "no under-target indicator after
    # Casey's name at all".
    remainder_after_casey = html[casey_pos:]
    assert "class-target-under" not in remainder_after_casey
    assert "class-target-on" not in remainder_after_casey


def test_no_target_at_all_renders_no_indicators_anywhere():
    session_data = json.dumps(
        {
            "class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
            "leaderboard": _three_station_leaderboard(),
        }
    )
    clock = json.dumps(
        {
            "index": 0,
            "kind": "work",
            "segmentRemainingMs": 300000,
            "totalRemainingMs": 300000,
            "finished": False,
        }
    )
    html = _run_build_class_board_html(session_data, clock)
    assert "class-target-indicator" not in html
    assert "class-target-on" not in html
    assert "class-target-under" not in html


def test_on_target_indicator_uses_volt_yellow_and_under_uses_hazard_amber():
    html = _run_three_station_board(200)
    on_span = re.search(
        r'<span class="class-target-indicator class-target-on"[^>]*>', html
    )
    under_span = re.search(
        r'<span class="class-target-indicator class-target-under"[^>]*>', html
    )
    assert on_span and "var(--volt-yellow)" in on_span.group(0)
    assert under_span and "var(--hazard-amber)" in under_span.group(0)


# ---------------------------------------------------------------------------
# 4. classClockAt / toClockShape carry targetWatts through, but only when
# the segment actually has one (backward compatible with the existing
# exact-equality tests in test_dashboard_class_board.py, which assert the
# OLD key set for target-less segments).
# ---------------------------------------------------------------------------


def _run_class_clock_at(now_ms: int, start_ms: int, plan_js: str) -> dict:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "classClockAt"))
    script = (
        _metric_number_stub()
        + fn
        + "\n"
        + f"const plan = {plan_js};\n"
        + f"console.log(JSON.stringify(classClockAt({now_ms}, {start_ms}, plan)));"
    )
    return json.loads(_run_node(script))


def test_class_clock_at_exposes_target_watts_for_active_segment():
    plan = json.dumps(
        {
            "segments": [
                {"kind": "warmup", "duration_sec": 300, "target_watts": 90},
                {"kind": "work", "duration_sec": 1200, "target_watts": 220},
            ]
        }
    )
    result = _run_class_clock_at(400000, 0, plan)
    assert result["kind"] == "work"
    assert result["targetWatts"] == 220


def test_class_clock_at_omits_target_watts_key_when_segment_has_none():
    plan = json.dumps({"segments": [{"kind": "warmup", "duration_sec": 300}]})
    result = _run_class_clock_at(0, 0, plan)
    assert "targetWatts" not in result


def _run_to_clock_shape(server_segment_js: str) -> dict:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "toClockShape"))
    script = (
        fn
        + "\n"
        + f"const serverSegment = {server_segment_js};\n"
        + "console.log(JSON.stringify(toClockShape(serverSegment)));"
    )
    return json.loads(_run_node(script))


def test_to_clock_shape_carries_target_watts_when_present():
    server_segment = json.dumps(
        {
            "index": 0,
            "kind": "work",
            "segment_remaining_ms": 123456,
            "total_remaining_ms": 183456,
            "finished": False,
            "target_watts": 150,
        }
    )
    result = _run_to_clock_shape(server_segment)
    assert result["targetWatts"] == 150


def test_to_clock_shape_omits_target_watts_key_when_server_segment_has_none():
    # Exact legacy server payload shape -- no target_watts key at all.
    server_segment = '{"index": 0, "kind": "work", "segment_remaining_ms": 123456, "total_remaining_ms": 183456, "finished": false}'
    result = _run_to_clock_shape(server_segment)
    assert "targetWatts" not in result


# ---------------------------------------------------------------------------
# 5. Full renderClassBoard integration: server class_segment carrying
# target_watts ends up in the rendered DOM hero band, proving the value
# survives the WHOLE path (server dict -> toClockShape -> buildClassBoardHtml
# -> innerHTML), not just one link in the chain.
# ---------------------------------------------------------------------------


def _run_render_class_board(data_js: str) -> dict:
    source = _read_index()
    render_fn = _strip_js_comments(_extract_function(source, "renderClassBoard"))
    to_clock_shape_fn = _strip_js_comments(_extract_function(source, "toClockShape"))
    class_clock_at_fn = _strip_js_comments(_extract_function(source, "classClockAt"))
    build_class_board_html_fn = _strip_js_comments(
        _extract_function(source, "buildClassBoardHtml")
    )

    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _node_display_name_stub()
        + _intl_number_format_stub()
        + _format_clock_stub()
        + "const currentLocale = 'en-US';\n"
        + to_clock_shape_fn
        + "\n"
        + class_clock_at_fn
        + "\n"
        + build_class_board_html_fn
        + "\n"
        + render_fn
        + "\n"
        + "const container = { innerHTML: '' };\n"
        + "document = { getElementById: () => container };\n"
        + f"const data = {data_js};\n"
        + "renderClassBoard(data);\n"
        + "console.log(JSON.stringify({ innerHTML: container.innerHTML }));"
    )
    result = json.loads(_run_node(script))
    return result


def test_render_class_board_end_to_end_shows_server_target_in_hero():
    data = {
        "state": "RUNNING",
        "session_mode": "class",
        "class_plan": {
            "segments": [{"kind": "work", "duration_sec": 300, "target_watts": 175}]
        },
        "class_segment": {
            "index": 0,
            "kind": "work",
            "segment_remaining_ms": 123456,
            "total_remaining_ms": 183456,
            "finished": False,
            "target_watts": 175,
        },
        "leaderboard": {},
    }
    result = _run_render_class_board(json.dumps(data))
    html = result["innerHTML"]
    assert "T[class.target_watts|{&quot;watts&quot;:&quot;175&quot;}]" in html


def test_render_class_board_end_to_end_no_target_shows_no_hero_markup():
    data = {
        "state": "RUNNING",
        "session_mode": "class",
        "class_plan": {"segments": [{"kind": "work", "duration_sec": 300}]},
        "class_segment": {
            "index": 0,
            "kind": "work",
            "segment_remaining_ms": 123456,
            "total_remaining_ms": 183456,
            "finished": False,
        },
        "leaderboard": {},
    }
    result = _run_render_class_board(json.dumps(data))
    html = result["innerHTML"]
    assert "class-hero-target" not in html


# ---------------------------------------------------------------------------
# 6. i18n: class.target_watts / class.target_met / class.target_under exist
# in all six locales with genuine, non-English-copy translations, zh-TW
# carries real CJK, and the {watts} placeholder survives translation.
# ---------------------------------------------------------------------------

_NEW_KEYS = {"class.target_watts", "class.target_met", "class.target_under"}


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_new_target_keys_exist_in_all_locales():
    for locale in LOCALES:
        messages = _load_locale(locale)
        missing = _NEW_KEYS - set(messages.keys())
        assert not missing, f"Missing target keys in {locale}: {missing}"


def test_new_target_keys_in_zh_tw_contain_cjk_characters():
    zh_tw = _load_locale("zh-TW")
    for key in _NEW_KEYS:
        value = zh_tw[key]
        assert _CJK_RE.search(
            value
        ), f"{key} in zh-TW should contain CJK but is: {value}"


def test_new_target_keys_are_not_copies_of_english_across_locales():
    en_us = _load_locale("en-US")
    for locale in ("de-CH", "fr", "it", "sv"):
        messages = _load_locale(locale)
        for key in _NEW_KEYS:
            assert messages[key] != en_us[key], (
                f"{key} in {locale} is an unmodified copy of the en-US "
                f"string: {en_us[key]!r}"
            )


def test_target_watts_placeholder_present_and_consistent_across_locales():
    en_us_value = _load_locale("en-US")["class.target_watts"]
    assert "{watts}" in en_us_value
    for locale in LOCALES:
        value = _load_locale(locale)["class.target_watts"]
        assert "{watts}" in value, f"{locale} lost the {{watts}} placeholder: {value!r}"
