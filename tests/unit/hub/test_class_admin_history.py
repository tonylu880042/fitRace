"""Tests for the compact class-history list on Class Admin
(`hub_server/static/classAdmin.html`), added by commit 2 of class history.

A finished class is now persisted server-side (see
tests/unit/hub/test_class_result_store.py and GET /api/class/history). This
module covers the classAdmin.html side: the pure date/time formatter, the
pure HTML-building render-path helper (executed under `node -e` with stubs,
per CLAUDE.md's testability guidance -- a correct helper whose result never
reaches the DOM is the recurring defect here), and the DOM-wiring function
that actually assigns the built HTML to the page.

This is a NEW test file rather than an addition to the existing
tests/unit/hub/test_class_admin_page.py, since the task instructions permit
new test files under tests/unit/hub/ but forbid modifying existing ones.
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


def _t_stub() -> str:
    return (
        "const t = (key, params = {}) => {\n"
        "  if (params && Object.keys(params).length) {\n"
        "    return `T[${key}|${JSON.stringify(params)}]`;\n"
        "  }\n"
        "  return `T[${key}]`;\n"
        "};\n"
    )


def _intl_number_format_stub() -> str:
    return "const Intl = { NumberFormat: function(locale) { return { format: (n) => String(n) }; } };\n"


# ---------------------------------------------------------------------------
# 1. formatClassRunAt -- pure, deterministic, UTC-based, never a twelve-hour
#    clock (this page has an existing convention against that -- see
#    test_class_admin_page.py's toLocaleTimeString/hour12 guard tests).
# ---------------------------------------------------------------------------


def _run_format_class_run_at(epoch_ms) -> str:
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "formatClassRunAt"))
    script = (
        _metric_number_stub()
        + fn
        + f"\nconsole.log(formatClassRunAt({json.dumps(epoch_ms)}));"
    )
    return _run_node(script)


def test_format_class_run_at_renders_iso_like_utc_string():
    # 2024-01-15T10:30:00Z
    epoch_ms = 1705314600000
    assert _run_format_class_run_at(epoch_ms) == "2024-01-15 10:30 UTC"


def test_format_class_run_at_zero_pads_single_digit_fields():
    # 2024-03-02T04:05:00Z
    epoch_ms = 1709352300000
    assert _run_format_class_run_at(epoch_ms) == "2024-03-02 04:05 UTC"


def test_format_class_run_at_null_or_zero_is_placeholder():
    assert _run_format_class_run_at(None) == "--"
    assert _run_format_class_run_at(0) == "--"


def test_format_class_run_at_never_uses_twelve_hour_clock():
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "formatClassRunAt"))
    assert "toLocaleString" not in fn
    assert "hour12" not in fn


# ---------------------------------------------------------------------------
# 2. buildClassHistoryHtml -- pure render-path helper.
# ---------------------------------------------------------------------------


def _run_build_class_history_html(classes_js: str) -> str:
    source = _read_class_admin()
    format_class_run_at_fn = _strip_js_comments(
        _extract_function(source, "formatClassRunAt")
    )
    format_duration_fn = _strip_js_comments(
        _extract_function(source, "formatDurationClock")
    )
    fn = _strip_js_comments(_extract_function(source, "buildClassHistoryHtml"))
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _intl_number_format_stub()
        + "const currentLocale = 'en-US';\n"
        + format_class_run_at_fn
        + "\n"
        + format_duration_fn
        + "\n"
        + fn
        + "\n"
        + f"const classes = {classes_js};\n"
        + "console.log(buildClassHistoryHtml(classes));"
    )
    return _run_node(script)


def test_build_class_history_html_empty_list_shows_empty_state():
    html = _run_build_class_history_html("[]")
    assert "T[classAdmin.no_history]" in html


def test_build_class_history_html_renders_formatted_when_and_duration():
    entry = {
        "start_time_epoch_ms": 1705314600000,  # 2024-01-15T10:30:00Z
        "duration_ms": 185000,  # 3:05
        "participant_count": 4,
    }
    html = _run_build_class_history_html(json.dumps([entry]))
    assert "2024-01-15 10:30 UTC" in html
    assert "03:05" in html


def test_build_class_history_html_renders_translated_participant_count():
    entry = {
        "start_time_epoch_ms": 1705314600000,
        "duration_ms": 60000,
        "participant_count": 7,
    }
    html = _run_build_class_history_html(json.dumps([entry]))
    assert "T[classAdmin.history_participants|" in html
    # The whole t() result is HTML-escaped, so the JSON params fragment
    # appears with &quot; in place of the literal double quote.
    assert "count&quot;:&quot;7" in html


def test_build_class_history_html_renders_one_row_per_entry_in_order():
    entries = [
        {
            "start_time_epoch_ms": 1705314600000,
            "duration_ms": 60000,
            "participant_count": 1,
        },
        {
            "start_time_epoch_ms": 1705318200000,
            "duration_ms": 120000,
            "participant_count": 2,
        },
    ]
    html = _run_build_class_history_html(json.dumps(entries))
    first_pos = html.index("10:30")
    second_pos = html.index("11:30")
    assert first_pos < second_pos
    assert html.count("history-row") == 2


# ---------------------------------------------------------------------------
# 3. renderClassHistory -- DOM wiring. Proves the built HTML actually
# reaches the class-history-list element, not merely that
# buildClassHistoryHtml is correct in isolation (the "computed but never
# assigned" failure mode this project keeps losing review rounds to).
# ---------------------------------------------------------------------------


def _run_render_class_history(class_history_js: str) -> dict:
    source = _read_class_admin()
    format_class_run_at_fn = _strip_js_comments(
        _extract_function(source, "formatClassRunAt")
    )
    format_duration_fn = _strip_js_comments(
        _extract_function(source, "formatDurationClock")
    )
    build_fn = _strip_js_comments(_extract_function(source, "buildClassHistoryHtml"))
    render_fn = _strip_js_comments(_extract_function(source, "renderClassHistory"))
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _intl_number_format_stub()
        + "const currentLocale = 'en-US';\n"
        + "const mockElements = {};\n"
        + "function makeEl() { return { innerHTML: '' }; }\n"
        + "function $(id) {\n"
        + "  if (!mockElements[id]) mockElements[id] = makeEl();\n"
        + "  return mockElements[id];\n"
        + "}\n"
        + f"const state = {{ classHistory: {class_history_js} }};\n"
        + format_class_run_at_fn
        + "\n"
        + format_duration_fn
        + "\n"
        + build_fn
        + "\n"
        + render_fn
        + "\n"
        + "renderClassHistory();\n"
        + "console.log(JSON.stringify({ html: mockElements['class-history-list'].innerHTML }));"
    )
    return json.loads(_run_node(script))


def test_render_class_history_writes_built_html_into_the_dom():
    entries = [
        {
            "start_time_epoch_ms": 1705314600000,
            "duration_ms": 60000,
            "participant_count": 3,
        },
    ]
    result = _run_render_class_history(json.dumps(entries))
    assert "2024-01-15 10:30 UTC" in result["html"]
    assert "T[classAdmin.history_participants|" in result["html"]


def test_render_class_history_empty_state_reaches_the_dom():
    result = _run_render_class_history("[]")
    assert "T[classAdmin.no_history]" in result["html"]


def test_render_class_history_defaults_to_empty_list_when_state_field_missing():
    source = _read_class_admin()
    format_class_run_at_fn = _strip_js_comments(
        _extract_function(source, "formatClassRunAt")
    )
    format_duration_fn = _strip_js_comments(
        _extract_function(source, "formatDurationClock")
    )
    build_fn = _strip_js_comments(_extract_function(source, "buildClassHistoryHtml"))
    render_fn = _strip_js_comments(_extract_function(source, "renderClassHistory"))
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _intl_number_format_stub()
        + "const currentLocale = 'en-US';\n"
        + "const mockElements = {};\n"
        + "function makeEl() { return { innerHTML: '' }; }\n"
        + "function $(id) {\n"
        + "  if (!mockElements[id]) mockElements[id] = makeEl();\n"
        + "  return mockElements[id];\n"
        + "}\n"
        + "const state = {};\n"
        + format_class_run_at_fn
        + "\n"
        + format_duration_fn
        + "\n"
        + build_fn
        + "\n"
        + render_fn
        + "\n"
        + "renderClassHistory();\n"
        + "console.log(JSON.stringify({ html: mockElements['class-history-list'].innerHTML }));"
    )
    result = json.loads(_run_node(script))
    assert "T[classAdmin.no_history]" in result["html"]


# ---------------------------------------------------------------------------
# 4. Networking + wiring into refreshAll/renderAll.
# ---------------------------------------------------------------------------


def test_refresh_all_fetches_class_history():
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "refreshAll"))
    assert "refreshClassHistory()" in fn


def test_render_all_renders_class_history():
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "renderAll"))
    assert "renderClassHistory()" in fn


def test_class_history_endpoint_used_by_refresh_function():
    source = _read_class_admin()
    fn = _strip_js_comments(_extract_function(source, "refreshClassHistory"))
    assert "/api/class/history" in fn
    assert "state.classHistory" in fn


# ---------------------------------------------------------------------------
# 5. Page markup and i18n.
# ---------------------------------------------------------------------------


def test_class_admin_page_has_history_list_container():
    body = _read_class_admin()
    assert 'id="class-history-list"' in body


CLASS_HISTORY_KEYS = [
    "classAdmin.panel_history",
    "classAdmin.no_history",
    "classAdmin.history_participants",
]

LOCALES = ["de-CH", "en-US", "fr", "it", "sv", "zh-TW"]


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_class_history_keys_exist_in_every_locale():
    for locale in LOCALES:
        messages = _load_locale(locale)
        missing = set(CLASS_HISTORY_KEYS) - set(messages.keys())
        assert not missing, f"Missing class history keys in {locale}: {missing}"


def test_class_history_keys_in_zh_tw_contain_cjk_characters():
    zh_tw = _load_locale("zh-TW")
    for key in CLASS_HISTORY_KEYS:
        value = zh_tw[key]
        assert _CJK_RE.search(value), f"{key} in zh-TW has no CJK character: {value!r}"


def test_class_history_keys_differ_from_english_in_every_other_locale():
    en_us = _load_locale("en-US")
    for locale in LOCALES:
        if locale == "en-US":
            continue
        messages = _load_locale(locale)
        for key in CLASS_HISTORY_KEYS:
            assert messages[key] != en_us[key], (
                f"{key} in {locale} is untranslated (identical to en-US): "
                f"{messages[key]!r}"
            )


def test_class_history_participants_placeholder_present_and_consistent_across_locales():
    en_us_value = _load_locale("en-US")["classAdmin.history_participants"]
    assert "{count}" in en_us_value
    for locale in LOCALES:
        value = _load_locale(locale)["classAdmin.history_participants"]
        assert "{count}" in value, f"{locale} lost the {{count}} placeholder: {value!r}"
