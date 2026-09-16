"""The dashboard idle record wall (hub_server/static/index.html) replaces
its 10s top-3 carousel with ONE static "overall standings" page while the
race is IDLE in race mode and GET /api/results/standings returns a
non-empty ranking (see RaceResultsQuery.get_standings in
hub_server/usecases/race_results_query.py): every heat of the current
event's category ranked together, not just the top 3 of the latest heat.

This executes the REAL, unmodified `buildOverallStandingsView` and
`renderStandingsRows` functions pulled out of index.html's inline <script>
via brace-depth matching -- the same technique as test_record_wall_relay.py
-- under node, never a source-text grep, so deleting the real rendering
wiring turns this red instead of being satisfied by a nearby comment.
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
        '    "record_wall.overall_standings": "Overall Standings",\n'
        '    "record_wall.race_count": "{count} races",\n'
        '    "record_wall.type_distance": "Distance",\n'
        '    "record_wall.division_men": "Men",\n'
        '    "record_wall.division_women": "Women",\n'
        '    "record_wall.relay_legs": "{legs}-Leg Relay",\n'
        '    "record_wall.dnf": "DNF",\n'
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


def _build_view(standings_js: str) -> str:
    source = _strip_js_comments(_read_index())
    fns = "\n".join(
        _extract_function(source, name)
        for name in (
            "recordWallTypeLabel",
            "recordWallDivisionLabel",
            "formatRecordEntryValue",
            "formatRecordProgressValue",
            "formatRecordWallEntryValue",
            "renderStandingsRows",
            "buildOverallStandingsView",
        )
    )
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + fns
        + "\n"
        + f"console.log(JSON.stringify(buildOverallStandingsView({standings_js})));"
    )
    return _run_node(script)


_SIX_ROW_STANDINGS = """
{
  race_type: "distance",
  label: "1000 m",
  relay_legs: 2,
  race_count: 3,
  sections: [
    {
      division: null,
      rows: [
        {rank: 1, athlete_name: "Echo", team_name: "Echo Team", division: null, finished: true, value: 40000, station_number: 1, relay_members: ["E1","E2"], race_start_epoch_ms: 5000},
        {rank: 2, athlete_name: "Charlie", team_name: "Charlie Team", division: null, finished: true, value: 45000, station_number: 1, relay_members: ["C1","C2"], race_start_epoch_ms: 3000},
        {rank: 3, athlete_name: "Bravo", team_name: "Bravo Team", division: null, finished: true, value: 50000, station_number: 2, relay_members: ["B1","B2"], race_start_epoch_ms: 1000},
        {rank: 4, athlete_name: "Foxtrot", team_name: "Foxtrot Team", division: null, finished: true, value: 55000, station_number: 2, relay_members: ["F1","F2"], race_start_epoch_ms: 5000},
        {rank: 5, athlete_name: "Alpha", team_name: "Alpha Team", division: null, finished: true, value: 60000, station_number: 1, relay_members: ["A1","A2"], race_start_epoch_ms: 1000},
        {rank: 6, athlete_name: "Delta", team_name: "Delta Team", division: null, finished: true, value: 70000, station_number: 2, relay_members: ["D1","D2"], race_start_epoch_ms: 3000}
      ]
    }
  ]
}
"""


def test_standings_view_renders_all_rows_in_rank_order():
    html = _build_view(_SIX_ROW_STANDINGS)
    assert html is not None
    body = json.loads(html)
    names_in_order = ["Echo", "Charlie", "Bravo", "Foxtrot", "Alpha", "Delta"]
    positions = [body.index(name) for name in names_in_order]
    assert positions == sorted(positions)
    for rank in range(1, 7):
        assert f"#{rank}" in body


def test_standings_view_shows_race_count_and_heading():
    html = _build_view(_SIX_ROW_STANDINGS)
    body = json.loads(html)
    assert "Overall Standings" in body
    assert "Distance" in body
    assert "1000 m" in body
    assert "2-Leg Relay" in body
    assert "3 races" in body


def test_standings_view_shows_team_name_when_different_from_athlete_name():
    html = _build_view(_SIX_ROW_STANDINGS)
    body = json.loads(html)
    assert "Echo Team" in body


def test_standings_view_dnf_row_shows_dnf_and_progress():
    standings_js = """
{
  race_type: "distance",
  label: "1000 m",
  relay_legs: null,
  race_count: 1,
  sections: [
    {
      division: null,
      rows: [
        {rank: 1, athlete_name: "Alice", team_name: null, division: null, finished: true, value: 60000, station_number: 1, relay_members: null, race_start_epoch_ms: 1000},
        {rank: 2, athlete_name: "Bob", team_name: null, division: null, finished: false, value: 900, station_number: 2, relay_members: null, race_start_epoch_ms: 1000}
      ]
    }
  ]
}
"""
    html = _build_view(standings_js)
    body = json.loads(html)
    assert "DNF" in body
    assert "900" in body


def test_standings_view_has_one_section_per_division_in_order():
    standings_js = """
{
  race_type: "distance",
  label: "500 m",
  relay_legs: null,
  race_count: 1,
  sections: [
    {division: null, rows: [{rank: 1, athlete_name: "Noel", team_name: null, division: null, finished: true, value: 58000, station_number: 3, relay_members: null, race_start_epoch_ms: 1000}]},
    {division: "men", rows: [{rank: 1, athlete_name: "Marco", team_name: null, division: "men", finished: true, value: 55000, station_number: 2, relay_members: null, race_start_epoch_ms: 1000}]},
    {division: "women", rows: [{rank: 1, athlete_name: "Wendy", team_name: null, division: "women", finished: true, value: 60000, station_number: 1, relay_members: null, race_start_epoch_ms: 1000}]}
  ]
}
"""
    html = _build_view(standings_js)
    body = json.loads(html)
    assert "Noel" in body
    assert "Marco" in body
    assert "Wendy" in body
    assert "Men" in body
    assert "Women" in body
    # Section order: Noel's null-division section has no division label, and
    # it must precede the "Men" and "Women" section titles.
    assert body.index("Noel") < body.index("Men") < body.index("Wendy")


def test_standings_view_returns_null_when_race_type_is_missing():
    html = _build_view("{race_type: null, sections: [], race_count: 0}")
    assert json.loads(html) is None


def test_standings_view_returns_null_when_sections_empty():
    html = _build_view(
        '{race_type: "distance", label: "1000 m", relay_legs: null, race_count: 0, sections: []}'
    )
    assert json.loads(html) is None
