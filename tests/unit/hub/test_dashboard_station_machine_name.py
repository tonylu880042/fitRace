"""The hub already ships `node_display_name` (the machine name, e.g.
"Node141+BIKE_01") on every leaderboard/progress entry, but three of the
four dashboard leaderboard render modes never showed it: they used a
`station_number ? "Station N" : nodeDisplayName(node)` ternary, and a real
race always has a station number assigned, so the machine name never
rendered. A shared `stationLabel(node)` helper now joins both when both
exist, and the two member-chip render sites append the machine name via
`nodeDisplayName(member)` directly.

This module actually EXECUTES the extracted JS (not a grep/substring check)
by pulling `stationLabel`/`nodeDisplayName`'s function bodies out of
index.html's inline `<script>` via brace-depth matching (the same technique
as `_matching_brace_end` in tests/unit/hub/test_static_page_i18n.py),
stripping comments (`_strip_js_comments`, mirrored from
tests/unit/hub/test_operator_name_equipment_id_rung.py), and running the
result under `node`. It also asserts (against comment-stripped source, so a
comment can't satisfy the check) that each of the four render sites still
calls the shared helper -- so an unused helper cannot pass.

The two member-chip sites were originally covered ONLY by the source-text
assertion above (`"nodeDisplayName(member)" in fn`), which pins that the
call is *present* but not that its result actually reaches the rendered
HTML -- a `const machine = nodeDisplayName(member);` that is computed and
then never interpolated into the returned template still satisfies that
assertion, silently reintroducing the exact bug this module exists to
catch. `test_member_progress_chips_render_includes_machine_name` and
`test_team_leaderboard_member_chip_render_includes_machine_name` close that
gap by executing the real chip renderers under `node` and asserting the
machine name is present in the returned HTML string.
`renderMemberProgressChips` is a standalone pure function, extracted the
same way as `stationLabel`. `renderTeamLeaderboard`'s chip markup instead
lives inline as a `.map((member) => {...})` callback inside a much larger
function with DOM/state dependencies that can't run under plain `node`, so
only that callback -- not the whole function -- is extracted (again by
brace-depth matching, anchored on the unique `const members = (team.members
|| []).map((member) => {` line) and run with light stand-ins for the two
free variables it closes over (`teamKey`, `smoothMetricNumber`).
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


def _matching_brace_end(source: str, open_idx: int) -> int:
    """Return the index of the "}" that matches the "{" at open_idx,
    tracking string literals so braces inside quoted values don't throw off
    the depth count. Mirrors tests/unit/hub/test_static_page_i18n.py."""
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


def _run_station_label(node_obj_js: str) -> str:
    source = _read_index()
    node_display_name_fn = _strip_js_comments(
        _extract_function(source, "nodeDisplayName")
    )
    station_label_fn = _strip_js_comments(_extract_function(source, "stationLabel"))
    script = (
        'const t = (k) => ({"stations.station": "站位"})[k] || k;\n'
        + node_display_name_fn
        + "\n"
        + station_label_fn
        + "\n"
        + f"console.log(stationLabel({node_obj_js}));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return result.stdout.strip()


def test_station_label_includes_both_station_and_machine_name():
    out = _run_station_label(
        '{station_number: 2, node_display_name: "Node141+BIKE_01"}'
    )
    assert "2" in out
    assert "Node141+BIKE_01" in out


def test_station_label_is_machine_name_only_when_no_station_number():
    out = _run_station_label('{node_display_name: "Node141+BIKE_01"}')
    assert out == "Node141+BIKE_01"


def test_station_label_falls_back_to_node_id_with_no_display_name():
    out = _run_station_label('{station_number: 3, node_id: "fitrace-edge-01-bike-01"}')
    assert "3" in out
    assert "fitrace-edge-01-bike-01" in out
    assert "undefined" not in out
    assert "--" not in out


def test_race_track_leaderboard_calls_station_label_helper():
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "renderRaceTrackLeaderboard"))
    assert "stationLabel(node)" in fn


def test_sprint_board_leaderboard_calls_station_label_helper():
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "renderSprintBoardLeaderboard"))
    assert "stationLabel(node)" in fn


def test_member_progress_chips_calls_node_display_name_helper():
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "renderMemberProgressChips"))
    assert "nodeDisplayName(member)" in fn


def test_team_leaderboard_member_chips_calls_node_display_name_helper():
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "renderTeamLeaderboard"))
    assert "nodeDisplayName(member)" in fn


def _run_member_progress_chips(members_js: str) -> str:
    source = _read_index()
    node_display_name_fn = _strip_js_comments(
        _extract_function(source, "nodeDisplayName")
    )
    render_fn = _strip_js_comments(
        _extract_function(source, "renderMemberProgressChips")
    )
    script = (
        'const escapeHtml = (v) => String(v ?? "");\n'
        "const metricNumber = (value, fallback = 0) => {\n"
        "  const n = Number(value);\n"
        "  return Number.isFinite(n) ? n : fallback;\n"
        "};\n"
        + node_display_name_fn
        + "\n"
        + render_fn
        + "\n"
        + f"console.log(renderMemberProgressChips({members_js}));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return result.stdout


def test_member_progress_chips_render_includes_machine_name():
    """Executes the real renderMemberProgressChips() under node -- proves
    the machine name actually reaches the output HTML, not just that
    nodeDisplayName(member) is called somewhere in the source."""
    html = _run_member_progress_chips(
        '[{station_number: 1, athlete_name: "Marcus", '
        'node_display_name: "Node141+BIKE_01", progress_percent: 36}]'
    )
    assert "Node141+BIKE_01" in html
    assert "S1" in html
    assert "Marcus" in html


def _extract_team_member_chip_arrow_fn(source: str) -> str:
    """Return just the `(member) => {...}` callback passed to
    `(team.members || []).map(...)` inside renderTeamLeaderboard, found by
    brace-depth matching from a unique anchor line -- NOT the whole
    enclosing function, which depends on the DOM and other closured state
    that can't run under plain `node`."""
    outer_anchor = "const members = (team.members || []).map("
    start = source.index(outer_anchor)
    arrow_marker = "(member) => "
    arrow_start = source.index(arrow_marker, start)
    brace_open = source.index("{", arrow_start)
    brace_end = _matching_brace_end(source, brace_open)
    return "(member) => " + source[brace_open : brace_end + 1]


def _run_team_member_chip(member_js: str) -> str:
    source = _read_index()
    node_display_name_fn = _strip_js_comments(
        _extract_function(source, "nodeDisplayName")
    )
    chip_fn = _strip_js_comments(_extract_team_member_chip_arrow_fn(source))
    script = (
        'const escapeHtml = (v) => String(v ?? "");\n'
        'const teamKey = "team-1";\n'
        "const smoothMetricNumber = (key, target) => Number(target) || 0;\n"
        + node_display_name_fn
        + "\n"
        + f"const teamMemberChip = {chip_fn};\n"
        + f"console.log(teamMemberChip({member_js}));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return result.stdout


def test_team_leaderboard_member_chip_render_includes_machine_name():
    """Executes the real team-chip callback under node -- same gap-closing
    rationale as test_member_progress_chips_render_includes_machine_name."""
    html = _run_team_member_chip(
        '{station_number: 1, athlete_name: "Marcus", '
        'node_display_name: "Node141+BIKE_01", progress_percent: 36, '
        'node_id: "n1"}'
    )
    assert "Node141+BIKE_01" in html
    assert "S1" in html
    assert "Marcus" in html


def _equipment_tag_css_block() -> str:
    source = _read_index()
    start = source.index(".equipment-tag {")
    end = source.index("}", start)
    return source[start : end + 1]


def test_equipment_tag_font_size_is_readable_at_venue_distance():
    block = _equipment_tag_css_block()
    match = re.search(r"font-size:\s*([0-9.]+)rem", block)
    assert match, f"no font-size found in .equipment-tag block: {block}"
    assert float(match.group(1)) >= 0.9
