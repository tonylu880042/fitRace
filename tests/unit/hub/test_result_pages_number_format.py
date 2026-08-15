"""Locale-aware number formatting on the two public results pages.

result.html and results.html show numeric metrics (distance, calories,
power, athlete counts, rank) to the reader. Both pages already localise
text and dates through the six-locale t()/messages system (see
test_result_pages_i18n.py) and the anonymous-finisher name fallback (see
test_result_pages_anonymous_display.py), but the numbers themselves were
built with plain Number#toLocaleString() calls that took no locale
argument -- so they always rendered with whatever locale the JS runtime
defaults to, not the page's own selected locale. A de-CH reader would see
1,000 (US-style comma grouping), which reads as one-point-zero to someone
whose own language uses a comma as the decimal separator.

Mirrors the brace-depth extraction technique used by
test_result_pages_anonymous_display.py and test_result_pages_i18n.py so a
failure here means the real page source stopped formatting numbers for
the reader's locale, not that a description of it changed.
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


def _escape_html_stub() -> str:
    return (
        "function escapeHtml(str) {\n"
        "  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', \"'\": '&#039;' };\n"
        "  return String(str || '').replace(/[&<>\"']/g, m => map[m]);\n"
        "}\n"
    )


def _read(page: str) -> str:
    return (STATIC_DIR / page).read_text(encoding="utf-8")


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout


# -- 1. formatNumber (result.html) is a standalone, locale-aware formatter --


def _run_format_number(value_js: str, locale_js: str, decimals_js: str = "") -> str:
    source = _read("result.html")
    fn = _strip_js_comments(_extract_function(source, "formatNumber"))
    args = f"{value_js}, {locale_js}"
    if decimals_js:
        args += f", {decimals_js}"
    script = fn + "\n" + f"console.log(formatNumber({args}));"
    return _run_node(script).strip()


def test_format_number_renders_us_comma_grouping_for_en_us():
    assert _run_format_number("1000", '"en-US"') == "1,000"


def test_format_number_renders_swiss_apostrophe_grouping_for_de_ch():
    out = _run_format_number("1000", '"de-CH"')
    assert out == "1’000"
    assert "," not in out


def test_format_number_same_input_renders_differently_per_locale():
    en = _run_format_number("1000", '"en-US"')
    de = _run_format_number("1000", '"de-CH"')
    assert en == "1,000"
    assert de != en


def test_format_number_preserves_the_requested_decimal_places():
    de = _run_format_number("1234.56", '"de-CH"', "1")
    fr = _run_format_number("1234.56", '"fr"', "1")
    # decimals is 1 in both calls -- exactly one digit after the decimal
    # mark, regardless of which character each locale uses for it.
    assert de == "1’234.6"
    assert fr == "1 234,6"
    assert "." in de
    assert "," in fr


def test_format_number_missing_or_non_numeric_value_renders_placeholder():
    assert _run_format_number("NaN", '"en-US"') == "--"
    assert _run_format_number("undefined", '"en-US"') == "--"
    out = _run_format_number("undefined", '"de-CH"')
    assert out == "--"
    assert "NaN" not in out


# -- 2. formatMetricValue (results.html) is a standalone, locale-aware -----
#    formatter for the same distance/calories/power axis.


def _run_format_metric_value(athlete_js: str, race_type_js: str, locale_js: str):
    source = _read("results.html")
    fn = _strip_js_comments(_extract_function(source, "formatMetricValue"))
    script = (
        fn
        + "\n"
        + "console.log(JSON.stringify(formatMetricValue("
        + f"{athlete_js}, {race_type_js}, {locale_js})));"
    )
    return json.loads(_run_node(script))


def test_format_metric_value_distance_uses_locale_grouping():
    en = _run_format_metric_value('{"distance_m": 1000}', '"distance"', '"en-US"')
    de = _run_format_metric_value('{"distance_m": 1000}', '"distance"', '"de-CH"')
    assert en["value"] == "1,000"
    assert de["value"] == "1’000"
    assert en["unit"] == "m"
    assert de["unit"] == "m"


def test_format_metric_value_time_is_not_touched_by_locale():
    # Time stays a clock string ("min:sec") -- it must not gain a thousands
    # separator just because a locale argument now exists on this function.
    en = _run_format_metric_value('{"finished_time_ms": 95000}', '"time"', '"en-US"')
    de = _run_format_metric_value('{"finished_time_ms": 95000}', '"time"', '"de-CH"')
    assert en["value"] == "1:35"
    assert de["value"] == "1:35"


# -- 3. formatTime (result.html) stays clock-style regardless of locale ----


def _run_format_time(ms_js: str) -> str:
    source = _read("result.html")
    fn = _strip_js_comments(_extract_function(source, "formatTime"))
    script = fn + "\n" + f"console.log(formatTime({ms_js}));"
    return _run_node(script).strip()


def test_format_time_produces_clock_value_with_no_thousands_separator():
    assert _run_format_time("95000") == "1:35"
    long_duration = _run_format_time("3660000")
    assert long_duration == "61:00"
    assert "," not in long_duration
    assert "’" not in long_duration


# -- 4. renderResult (result.html) actually wires the locale-aware value --
#    into the metrics grid, not just present as a standalone helper. Uses
#    the same stub set as
#    test_result_pages_anonymous_display.py::_run_render_result_athlete_name,
#    except formatNumber is the REAL extracted function (not a stub) so a
#    regression that stops passing the page locale through is caught here.


def _run_render_result_metrics(athlete_js: str, race_js: str, locale: str):
    source = _read("result.html")
    display_name_fn = _strip_js_comments(
        _extract_function(source, "athleteDisplayName")
    )
    format_number_fn = _strip_js_comments(_extract_function(source, "formatNumber"))
    render_fn = _strip_js_comments(_extract_function(source, "renderResult"))

    script = (
        _escape_html_stub()
        + display_name_fn
        + "\n"
        + format_number_fn
        + "\n"
        + "class FakeEl {\n"
        "  constructor() { this.textContent = ''; this.innerHTML = ''; this.style = {}; this.classList = { add(){}, remove(){} }; this.children = []; }\n"
        "  appendChild(child) { this.children.push(child); }\n"
        "}\n"
        + "const elements = {};\n"
        + "function el(id) { if (!elements[id]) elements[id] = new FakeEl(); return elements[id]; }\n"
        + "const document = {\n"
        "  documentElement: { dataset: { lang: " + repr(locale) + " } },\n"
        "  getElementById: (id) => el(id),\n"
        "};\n"
        + "const window = {};\n"
        + "function getRaceTypeLabel() { return ''; }\n"
        + "function formatDate() { return ''; }\n"
        + "function formatTime() { return ''; }\n"
        + "const capturedMetrics = [];\n"
        + "function createMetric(labelKey, value, unit) { capturedMetrics.push({ labelKey, value, unit }); return new FakeEl(); }\n"
        + render_fn
        + "\n"
        + f"const data = {{ athlete: {athlete_js}, race: {race_js}, total_athletes: 12345 }};\n"
        + "renderResult(data);\n"
        + "console.log(JSON.stringify({ metrics: capturedMetrics, rank: el('rank').textContent }));"
    )
    return json.loads(_run_node(script))


def test_render_result_wires_locale_aware_distance_into_metric_grid():
    athlete_js = (
        '{"distance_m": 1000, "calories": 500, "max_power_watts": 300, '
        '"rank": 2, "station_number": 1}'
    )
    race_js = '{"race_type": "distance"}'

    en = _run_render_result_metrics(athlete_js, race_js, "en-US")
    de = _run_render_result_metrics(athlete_js, race_js, "de-CH")

    distance_en = next(
        m for m in en["metrics"] if m["labelKey"] == "race_type.distance"
    )
    distance_de = next(
        m for m in de["metrics"] if m["labelKey"] == "race_type.distance"
    )
    assert distance_en["value"] == "1,000"
    assert distance_de["value"] == "1’000"


def test_render_result_wires_locale_aware_rank_and_total_athletes():
    athlete_js = (
        '{"distance_m": 1000, "calories": 500, "max_power_watts": 300, '
        '"rank": 2, "station_number": 1}'
    )
    race_js = '{"race_type": "distance"}'

    en = _run_render_result_metrics(athlete_js, race_js, "en-US")
    de = _run_render_result_metrics(athlete_js, race_js, "de-CH")

    # total_athletes is 12345 in both calls -- the rank cell text must show
    # the locale-correct grouping for that count, not a bare comma.
    assert "12,345" in en["rank"]
    assert "12’345" in de["rank"]
    assert "12,345" not in de["rank"]


# -- 5. renderResults (results.html) wires the locale-aware value into the -
#    card markup and the athlete-count display, mirroring
#    test_result_pages_anonymous_display.py::_run_render_results_cards
#    except formatMetricValue/getRaceTypeLabel/getMetricLabel are the REAL
#    extracted functions rather than stubs.


def _run_render_results_wiring(data_js: str, locale: str) -> dict:
    source = _read("results.html")
    display_name_fn = _strip_js_comments(
        _extract_function(source, "athleteDisplayName")
    )
    race_type_fn = _strip_js_comments(_extract_function(source, "getRaceTypeLabel"))
    metric_label_fn = _strip_js_comments(_extract_function(source, "getMetricLabel"))
    format_metric_fn = _strip_js_comments(
        _extract_function(source, "formatMetricValue")
    )
    render_fn = _strip_js_comments(_extract_function(source, "renderResults"))

    script = (
        "const t = (key) => key;\n"
        + _escape_html_stub()
        + display_name_fn
        + "\n"
        + race_type_fn
        + "\n"
        + metric_label_fn
        + "\n"
        + format_metric_fn
        + "\n"
        + "class FakeEl {\n"
        "  constructor() { this.innerHTML = ''; this.textContent = ''; this.className = ''; this.id = ''; this.style = {}; this.children = []; }\n"
        "  appendChild(child) { this.children.push(child); this.innerHTML += child.innerHTML; }\n"
        "}\n"
        + "const elements = {};\n"
        + "function el(id) { if (!elements[id]) elements[id] = new FakeEl(); return elements[id]; }\n"
        + "const document = {\n"
        "  documentElement: { dataset: { lang: " + repr(locale) + " } },\n"
        "  getElementById: (id) => el(id),\n"
        "  createElement: (tag) => new FakeEl(),\n"
        "};\n"
        + "const window = { location: { origin: 'http://localhost' } };\n"
        + "function setTimeout() {}\n"
        + render_fn
        + "\n"
        + f"const data = {data_js};\n"
        + "renderResults(data);\n"
        + "console.log(JSON.stringify({ html: el('results-container').innerHTML, count: el('athlete-count-display').textContent }));"
    )
    return json.loads(_run_node(script))


def test_render_results_wires_locale_aware_distance_into_card_html():
    data_js = (
        '{"race_type": "distance", "athlete_count": 1234, "results": '
        '[{"athlete_name": "Zoe", "station_number": 1, "rank": 1, "distance_m": 12345}]}'
    )
    en = _run_render_results_wiring(data_js, "en-US")
    de = _run_render_results_wiring(data_js, "de-CH")

    assert "12,345" in en["html"]
    assert "12’345" in de["html"]
    assert "12,345" not in de["html"]


def test_render_results_wires_locale_aware_athlete_count():
    data_js = (
        '{"race_type": "distance", "athlete_count": 1234, "results": '
        '[{"athlete_name": "Zoe", "station_number": 1, "rank": 1, "distance_m": 500}]}'
    )
    en = _run_render_results_wiring(data_js, "en-US")
    de = _run_render_results_wiring(data_js, "de-CH")

    assert en["count"] == "1,234"
    assert de["count"] == "1’234"
