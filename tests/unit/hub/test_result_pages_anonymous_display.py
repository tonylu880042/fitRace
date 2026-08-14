"""Anonymous-finisher display fallback on the two public results pages.

result.html and results.html render race_results_query.py's output for the
public (no auth) athlete-result page and the QR results wall. Both pages
predate the six-locale i18n system and keep their own small en/zh inline
dictionaries -- this module does NOT touch that (out of scope per the task),
it only pins that a nameless finisher (`athlete_name: null`, a real
`station_number`) renders as "Station N" / "站位 N" instead of an empty
string, `null`, or the literal word "Athlete", in both of the two languages
these pages actually support.

Functions are extracted from the real page source with the same
brace-depth-matching technique as tests/unit/hub/test_dashboard_class_board.py
and executed under `node -e` so this asserts on the real rendered output, not
on the presence of a helper function alone.
"""

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


def _run_athlete_display_name(page: str, athlete_js: str, lang: str) -> str:
    """Execute athleteDisplayName from the given page under node."""
    source = _read(page)
    fn = _strip_js_comments(_extract_function(source, "athleteDisplayName"))
    script = (
        fn
        + "\n"
        + f"const athlete = {athlete_js};\n"
        + f"console.log(athleteDisplayName(athlete, {lang!r}));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# -- result.html: athleteDisplayName + renderResult usage -----------------


def test_result_html_uses_real_name_when_present():
    assert (
        _run_athlete_display_name(
            "result.html", '{"athlete_name": "Alice", "station_number": 3}', "en"
        )
        == "Alice"
    )


def test_result_html_falls_back_to_station_en_when_name_is_null():
    out = _run_athlete_display_name(
        "result.html", '{"athlete_name": null, "station_number": 3}', "en"
    )
    assert out == "Station 3"


def test_result_html_falls_back_to_station_zh_when_name_is_null():
    out = _run_athlete_display_name(
        "result.html", '{"athlete_name": null, "station_number": 3}', "zh"
    )
    assert out == "站位 3"


def test_result_html_never_renders_null_or_athlete_literal_when_station_present():
    out = _run_athlete_display_name(
        "result.html", '{"athlete_name": null, "station_number": 3}', "en"
    )
    assert out not in ("", "null", "Athlete")
    assert "3" in out


def _run_render_result_athlete_name(athlete_js: str, lang: str) -> str:
    """Execute the real renderResult() against a stub DOM/document and
    return what it wrote into the #athlete-name element -- proving the
    fallback is actually wired into the render path, not just present as a
    standalone helper."""
    source = _read("result.html")
    display_name_fn = _strip_js_comments(
        _extract_function(source, "athleteDisplayName")
    )
    render_fn = _strip_js_comments(_extract_function(source, "renderResult"))

    script = (
        _escape_html_stub() + display_name_fn + "\n" + "class FakeEl {\n"
        "  constructor() { this.textContent = ''; this.innerHTML = ''; this.style = {}; this.classList = { add(){}, remove(){} }; this.children = []; }\n"
        "  appendChild(child) { this.children.push(child); }\n"
        "}\n"
        + "const elements = {};\n"
        + "function el(id) { if (!elements[id]) elements[id] = new FakeEl(); return elements[id]; }\n"
        + "const document = {\n"
        "  documentElement: { dataset: { lang: " + repr(lang) + " } },\n"
        "  getElementById: (id) => el(id),\n"
        "};\n"
        + "const window = {};\n"
        + "function getRaceTypeLabel() { return ''; }\n"
        + "function formatDate() { return ''; }\n"
        + "function formatNumber() { return ''; }\n"
        + "function formatTime() { return ''; }\n"
        + "function createMetric() { return new FakeEl(); }\n"
        + render_fn
        + "\n"
        + f"const data = {{ athlete: {athlete_js}, race: {{}}, total_athletes: 3 }};\n"
        + "renderResult(data);\n"
        + "console.log(JSON.stringify(el('athlete-name').textContent));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip().strip('"')


def test_render_result_writes_station_fallback_into_athlete_name_element():
    out = _run_render_result_athlete_name(
        '{"athlete_name": null, "station_number": 3}', "en"
    )
    assert out == "Station 3"


def test_render_result_writes_real_name_into_athlete_name_element():
    out = _run_render_result_athlete_name(
        '{"athlete_name": "Bob", "station_number": 3}', "en"
    )
    assert out == "Bob"


# -- results.html: athleteDisplayName + renderResults card usage ----------


def test_results_html_falls_back_to_station_en_when_name_is_null():
    out = _run_athlete_display_name(
        "results.html", '{"athlete_name": null, "station_number": 3}', "en"
    )
    assert out == "Station 3"


def test_results_html_falls_back_to_station_zh_when_name_is_null():
    out = _run_athlete_display_name(
        "results.html", '{"athlete_name": null, "station_number": 3}', "zh"
    )
    assert out == "站位 3"


def test_results_html_never_renders_null_or_athlete_literal_when_station_present():
    out = _run_athlete_display_name(
        "results.html", '{"athlete_name": null, "station_number": 3}', "en"
    )
    assert out not in ("", "null", "Athlete")
    assert "3" in out


def _run_render_results_cards(data_js: str, lang: str) -> str:
    """Execute the real renderResults() against a stub DOM and return the
    aggregated innerHTML of the results grid, proving the card markup
    actually contains the station fallback for a nameless finisher."""
    source = _read("results.html")
    display_name_fn = _strip_js_comments(
        _extract_function(source, "athleteDisplayName")
    )
    render_fn = _strip_js_comments(_extract_function(source, "renderResults"))

    script = (
        _escape_html_stub() + display_name_fn + "\n" + "class FakeEl {\n"
        "  constructor() { this.innerHTML = ''; this.textContent = ''; this.className = ''; this.id = ''; this.style = {}; this.children = []; }\n"
        "  appendChild(child) { this.children.push(child); this.innerHTML += child.innerHTML; }\n"
        "}\n"
        + "const elements = {};\n"
        + "function el(id) { if (!elements[id]) elements[id] = new FakeEl(); return elements[id]; }\n"
        + "const document = {\n"
        "  documentElement: { dataset: { lang: " + repr(lang) + " } },\n"
        "  getElementById: (id) => el(id),\n"
        "  createElement: (tag) => new FakeEl(),\n"
        "};\n"
        + "const window = { location: { origin: 'http://localhost' } };\n"
        + "function getRaceTypeLabel() { return ''; }\n"
        + "function getMetricLabel() { return ''; }\n"
        + "function formatMetricValue() { return { value: '', unit: '' }; }\n"
        + "function setTimeout() {}\n"
        + render_fn
        + "\n"
        + f"const data = {data_js};\n"
        + "renderResults(data);\n"
        + "console.log(el('results-container').innerHTML);"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout


def test_render_results_card_shows_station_for_nameless_finisher():
    data_js = (
        '{"race_type": "distance", "athlete_count": 1, "results": '
        '[{"athlete_name": null, "station_number": 3, "rank": 1}]}'
    )
    html = _run_render_results_cards(data_js, "en")
    assert "Station 3" in html
    assert ">null<" not in html


def test_render_results_card_shows_real_name_when_present():
    data_js = (
        '{"race_type": "distance", "athlete_count": 1, "results": '
        '[{"athlete_name": "Cara", "station_number": 3, "rank": 1}]}'
    )
    html = _run_render_results_cards(data_js, "en")
    assert "Cara" in html
