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
