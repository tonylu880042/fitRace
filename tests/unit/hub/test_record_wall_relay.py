"""The record wall (hub_server/static/index.html, buildRecordWallSlides)
must append a translated relay-leg-count suffix to a record's title when
the record carries relay_legs, so a 1000 m individual record and a 1000 m
4-leg relay record read as two distinct slides rather than looking
identical (see RaceResultsQuery.get_records in
hub_server/usecases/race_results_query.py, which keys categories by
(race_type, label, division, relay_legs) for exactly this reason).

This executes the REAL, unmodified functions pulled out of index.html's
inline <script> via brace-depth matching -- the same technique as
test_record_wall_division.py -- under node, never a source-text grep, so
deleting the real relay-suffix wiring turns this red instead of being
satisfied by a nearby comment.
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


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _matching_bracket_end(
    source: str, open_idx: int, open_ch: str, close_ch: str
) -> int:
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
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching close bracket found")


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_bracket_end(source, brace_open, "{", "}")
    return source[start : brace_end + 1]


def _t_stub() -> str:
    return (
        "const t = (key, params) => {\n"
        "  const messages = {\n"
        '    "record_wall.title": "Record Wall",\n'
        '    "record_wall.type_distance": "Distance",\n'
        '    "record_wall.division_men": "Men",\n'
        '    "record_wall.division_women": "Women",\n'
        '    "record_wall.relay_legs": "{legs}-Leg Relay",\n'
        '    "stations.station": "Station",\n'
        '    "stations.athlete": "Athlete"\n'
        "  };\n"
        "  let value = messages[key] || key;\n"
        "  Object.entries(params || {}).forEach(([name, replacement]) => {\n"
        "    value = value.replaceAll(`{${name}}`, String(replacement));\n"
        "  });\n"
        "  return value;\n"
        "};\n"
    )


def _metric_number_stub() -> str:
    return (
        "function metricNumber(value, fallback) {\n"
        "  const fb = fallback === undefined ? 0 : fallback;\n"
        "  const n = Number(value);\n"
        "  return Number.isFinite(n) ? n : fb;\n"
        "}\n"
    )


def _escape_html_stub() -> str:
    return (
        "function escapeHtml(value) {\n"
        "  return String(value === null || value === undefined ? '' : value)\n"
        "    .replace(/&/g, '&amp;')\n"
        "    .replace(/</g, '&lt;')\n"
        "    .replace(/>/g, '&gt;');\n"
        "}\n"
    )


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def _build_slides(records_js: str) -> str:
    source = _strip_js_comments(_read_index())
    fns = "\n".join(
        _extract_function(source, name)
        for name in (
            "recordWallTypeLabel",
            "recordWallDivisionLabel",
            "formatRecordEntryValue",
            "renderRecordWallRows",
            "buildRecordWallSlides",
        )
    )
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + fns
        + "\n"
        + f"console.log(JSON.stringify(buildRecordWallSlides({records_js}, null)));"
    )
    output = _run_node(script)
    import json

    return json.loads(output)


def test_record_wall_title_shows_relay_legs_suffix():
    slides = _build_slides(
        '[{race_type: "distance", label: "1000 m", division: null, relay_legs: 4, entries: []}]'
    )
    assert "4-Leg Relay" in slides[0]
    assert "1000 m" in slides[0]


def test_record_wall_title_has_no_relay_suffix_when_unset():
    slides = _build_slides(
        '[{race_type: "distance", label: "1000 m", division: null, relay_legs: null, entries: []}]'
    )
    assert "Relay" not in slides[0]


def test_record_wall_splits_relay_and_individual_into_distinct_slides():
    slides = _build_slides(
        "["
        '{race_type: "distance", label: "1000 m", division: null, relay_legs: 4, entries: []},'
        '{race_type: "distance", label: "1000 m", division: null, relay_legs: null, entries: []}'
        "]"
    )
    assert len(slides) == 2
    assert slides[0] != slides[1]


def test_record_wall_title_combines_relay_and_division_suffixes():
    slides = _build_slides(
        '[{race_type: "distance", label: "500 m", division: "women", relay_legs: 2, entries: []}]'
    )
    assert "2-Leg Relay" in slides[0]
    assert "Women" in slides[0]
