"""Anonymous participation lets athlete_name be None, and the main
leaderboard / class board already fall back to showing the station
(Station N) before the generic Athlete label. Six render sites in
hub_server/static/index.html were missed and still rendered the literal
word Athlete for a nameless-but-stationed participant: the podium,
the podium reveal overlay, the record wall (when a station is available),
the member progress chip, the race_track leaderboard row, the sprint_board
leaderboard row, and the team leaderboard member chip.

This module EXECUTES the fixed expressions under node (not a grep/substring
check) by pulling the relevant function or callback bodies out of
index.html's inline <script> via brace/paren-depth matching -- the same
technique as _matching_brace_end in tests/unit/hub/test_static_page_i18n.py
and tests/unit/hub/test_dashboard_station_machine_name.py -- stripping
comments, and running the result under node. Each site is exercised with a
node that has no athlete_name but does have a station_number, asserting the
rendered output contains the localised station label rather than the
generic Athlete fallback, plus a companion case with neither name nor
station confirming the existing Athlete fallback still applies and nothing
renders as the literal strings null/undefined or an empty string.

The record wall (renderRecordWallRows) is fed two different shapes: entries
from GET /api/results/records never carry station_number (verified against
hub_server/usecases/race_results_query.py, which rebuilds each entry as a
fresh dict with only athlete_name, team_name, value, end_time_epoch_ms), so
that case is asserted to keep the pre-existing Athlete fallback. Entries
built from the latest-race slide (GET /api/results/races/{id}) do carry
station_number on the raw row, so that path is asserted to show the station
label.
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
    """Return the index of the close_ch that matches open_ch at open_idx,
    tracking string literals so brackets inside quoted values do not throw
    off the depth count."""
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


def _extract_block_arrow_after_anchor(source: str, anchor: str, param: str) -> str:
    """Return "(param) => { ... }" for a block-bodied arrow callback found
    right after a unique anchor string in the source."""
    start = source.index(anchor)
    arrow_marker = f"({param}) => "
    arrow_start = source.index(arrow_marker, start)
    brace_open = source.index("{", arrow_start)
    brace_end = _matching_bracket_end(source, brace_open, "{", "}")
    return f"({param}) => " + source[brace_open : brace_end + 1]


def _extract_paren_arrow_after_anchor(source: str, anchor: str, param: str) -> str:
    """Return "(param) => ({ ... })" for an implicit-return arrow callback
    (one whose body is a parenthesised object literal) found right after a
    unique anchor string in the source."""
    start = source.index(anchor)
    wrap_marker = f"({param}) => ("
    wrap_start = source.index(wrap_marker, start)
    paren_open = wrap_start + len(f"({param}) => ")
    paren_end = _matching_bracket_end(source, paren_open, "(", ")")
    return f"({param}) => " + source[paren_open : paren_end + 1]


def _t_stub() -> str:
    return (
        "const t = (key) => ({"
        '"stations.station": "Station",'
        '"stations.athlete": "Athlete"'
        "}[key] || key);\n"
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
    assert result.returncode == 0, f"node failed: {result.stderr}\nScript:\n{script}"
    return result.stdout


def _assert_never_null_undefined(html: str) -> None:
    assert "undefined" not in html
    assert "null" not in html


# -- Site 1: podium -----------------------------------------------------


def _run_render_podium(nodes_js: str) -> str:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "renderPodium"))
    node_display_name_fn = _strip_js_comments(
        _extract_function(source, "nodeDisplayName")
    )
    get_medal_meta_fn = _strip_js_comments(_extract_function(source, "getMedalMeta"))
    format_result_score_fn = _strip_js_comments(
        _extract_function(source, "formatResultScore")
    )
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + node_display_name_fn
        + "\n"
        + get_medal_meta_fn
        + "\n"
        + format_result_score_fn
        + "\n"
        + fn
        + "\n"
        + f'console.log(renderPodium({nodes_js}, "distance"));'
    )
    return _run_node(script)


def test_podium_shows_station_label_for_anonymous_finisher():
    html = _run_render_podium('[{station_number: 3, node_id: "n1"}]')
    assert "Station 3" in html
    _assert_never_null_undefined(html)


def test_podium_falls_back_to_athlete_label_with_no_station():
    html = _run_render_podium('[{node_id: "n1"}]')
    assert "Athlete" in html
    assert "Station" not in html
    _assert_never_null_undefined(html)


def test_podium_uses_real_name_when_present():
    html = _run_render_podium(
        '[{station_number: 3, athlete_name: "Marcus", node_id: "n1"}]'
    )
    assert "Marcus" in html
    assert "Station" not in html
    assert "Athlete" not in html


# -- Site 2: podium reveal overlay --------------------------------------


def _run_podium_overlay_cards(entries_js: str) -> str:
    source = _read_index()
    build_card_fn = _strip_js_comments(
        _extract_function(source, "buildPodiumOverlayCard")
    )
    cards_html_fn = _strip_js_comments(
        _extract_block_arrow_after_anchor(
            source, "const cardsHtml = topThree.map((entry, index) => {", "entry, index"
        )
    )
    format_result_score_fn = _strip_js_comments(
        _extract_function(source, "formatResultScore")
    )
    get_medal_meta_fn = _strip_js_comments(_extract_function(source, "getMedalMeta"))
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + get_medal_meta_fn
        + "\n"
        + format_result_score_fn
        + "\n"
        + build_card_fn
        + "\n"
        + "const isTeamMode = false;\n"
        + 'const raceType = "distance";\n'
        + f"const cardsHtml = {cards_html_fn};\n"
        + f"const topThree = {entries_js};\n"
        + 'console.log(topThree.map((entry, index) => cardsHtml(entry, index)).join(""));'
    )
    return _run_node(script)


def test_podium_overlay_card_shows_station_label_for_anonymous_finisher():
    html = _run_podium_overlay_cards('[{station_number: 2, node_id: "n1"}]')
    assert "Station 2" in html
    _assert_never_null_undefined(html)


def test_podium_overlay_card_falls_back_to_athlete_label_with_no_station():
    html = _run_podium_overlay_cards('[{node_id: "n1"}]')
    assert "Athlete" in html
    assert "Station" not in html
    _assert_never_null_undefined(html)


# -- Site 3: record wall --------------------------------------------------


def _run_record_wall_rows(entries_js: str) -> str:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "renderRecordWallRows"))
    format_value_fn = _strip_js_comments(
        _extract_function(source, "formatRecordEntryValue")
    )
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + format_value_fn
        + "\n"
        + fn
        + "\n"
        + f'console.log(renderRecordWallRows({entries_js}, "distance"));'
    )
    return _run_node(script)


def test_record_wall_row_shows_station_label_when_station_available():
    """Latest-race entries carry station_number on the raw leaderboard row
    (see race_results_query.py get_race), so the record wall should use it
    once a nameless finisher reaches this row."""
    html = _run_record_wall_rows("[{station_number: 4, value: 1000}]")
    assert "Station 4" in html
    _assert_never_null_undefined(html)


def test_record_wall_row_keeps_athlete_fallback_when_no_station_field():
    """All-time record entries from GET /api/results/records never carry
    station_number (race_results_query.py _top_three rebuilds a fresh dict
    with only athlete_name/team_name/value/end_time_epoch_ms), so this shape
    must keep falling back to the generic Athlete label -- there is no
    station to show."""
    html = _run_record_wall_rows("[{value: 1000}]")
    assert "Athlete" in html
    assert "Station" not in html
    _assert_never_null_undefined(html)


# -- Site 4: member progress chip -----------------------------------------


def _run_member_progress_chips(members_js: str) -> str:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "renderMemberProgressChips"))
    node_display_name_fn = _strip_js_comments(
        _extract_function(source, "nodeDisplayName")
    )
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + node_display_name_fn
        + "\n"
        + fn
        + "\n"
        + f"console.log(renderMemberProgressChips({members_js}));"
    )
    return _run_node(script)


def test_member_progress_chip_shows_station_label_for_anonymous_member():
    html = _run_member_progress_chips('[{station_number: 5, node_id: "n1"}]')
    assert "Station 5" in html
    _assert_never_null_undefined(html)


def test_member_progress_chip_falls_back_to_athlete_label_with_no_station():
    html = _run_member_progress_chips('[{node_id: "n1"}]')
    assert "Athlete" in html
    assert "Station" not in html
    _assert_never_null_undefined(html)


# -- Site 5: race_track leaderboard row ------------------------------------


def _run_race_track_row_name(node_js: str) -> str:
    source = _read_index()
    callback = _strip_js_comments(
        _extract_block_arrow_after_anchor(
            source, ": individualRows.map((node) => {", "node"
        )
    )
    station_label_fn = _strip_js_comments(_extract_function(source, "stationLabel"))
    node_display_name_fn = _strip_js_comments(
        _extract_function(source, "nodeDisplayName")
    )
    script = (
        _t_stub()
        + _metric_number_stub()
        + node_display_name_fn
        + "\n"
        + station_label_fn
        + "\n"
        + 'function formatResultScore() { return { value: "", label: "" }; }\n'
        + 'const raceType = "distance";\n'
        + f"const rowFn = {callback};\n"
        + f"console.log(JSON.stringify(rowFn({node_js}).name));"
    )
    return _run_node(script)


def test_race_track_row_shows_station_label_for_anonymous_node():
    out = _run_race_track_row_name('{station_number: 6, node_id: "n1"}')
    assert "Station 6" in out
    _assert_never_null_undefined(out)


def test_race_track_row_falls_back_to_athlete_label_with_no_station():
    out = _run_race_track_row_name('{node_id: "n1"}')
    assert "Athlete" in out
    assert "Station" not in out
    _assert_never_null_undefined(out)


# -- Site 6: sprint_board leaderboard row -----------------------------------


def _run_sprint_board_row_name(node_js: str) -> str:
    source = _read_index()
    callback = _strip_js_comments(
        _extract_paren_arrow_after_anchor(
            source, ": individualRows.map((node) => ({", "node"
        )
    )
    station_label_fn = _strip_js_comments(_extract_function(source, "stationLabel"))
    node_display_name_fn = _strip_js_comments(
        _extract_function(source, "nodeDisplayName")
    )
    script = (
        _t_stub()
        + _metric_number_stub()
        + node_display_name_fn
        + "\n"
        + station_label_fn
        + "\n"
        + 'function formatResultScore() { return { value: "", label: "" }; }\n'
        + 'const raceType = "distance";\n'
        + f"const rowFn = {callback};\n"
        + f"console.log(JSON.stringify(rowFn({node_js}).name));"
    )
    return _run_node(script)


def test_sprint_board_row_shows_station_label_for_anonymous_node():
    out = _run_sprint_board_row_name('{station_number: 7, node_id: "n1"}')
    assert "Station 7" in out
    _assert_never_null_undefined(out)


def test_sprint_board_row_falls_back_to_athlete_label_with_no_station():
    out = _run_sprint_board_row_name('{node_id: "n1"}')
    assert "Athlete" in out
    assert "Station" not in out
    _assert_never_null_undefined(out)


# -- Site 7: team leaderboard member chip -----------------------------------


def _extract_team_member_chip_arrow_fn(source: str) -> str:
    """Return just the (member) => {...} callback passed to
    (team.members || []).map(...) inside renderTeamLeaderboard, found by
    brace-depth matching from a unique anchor line -- NOT the whole
    enclosing function, which depends on the DOM and other closured state
    that cannot run under plain node. Mirrors the identical helper in
    tests/unit/hub/test_dashboard_station_machine_name.py."""
    outer_anchor = "const members = (team.members || []).map("
    return _extract_block_arrow_after_anchor(source, outer_anchor, "member")


def _run_team_member_chip(member_js: str) -> str:
    source = _read_index()
    node_display_name_fn = _strip_js_comments(
        _extract_function(source, "nodeDisplayName")
    )
    chip_fn = _strip_js_comments(_extract_team_member_chip_arrow_fn(source))
    script = (
        _t_stub()
        + _escape_html_stub()
        + 'const teamKey = "team-1";\n'
        + "function smoothMetricNumber(key, target) { return Number(target) || 0; }\n"
        + node_display_name_fn
        + "\n"
        + f"const teamMemberChip = {chip_fn};\n"
        + f"console.log(teamMemberChip({member_js}));"
    )
    return _run_node(script)


def test_team_leaderboard_chip_shows_station_label_for_anonymous_member():
    html = _run_team_member_chip('{station_number: 8, node_id: "n1"}')
    assert "Station 8" in html
    _assert_never_null_undefined(html)


def test_team_leaderboard_chip_falls_back_to_athlete_label_with_no_station():
    html = _run_team_member_chip('{node_id: "n1"}')
    assert "Athlete" in html
    assert "Station" not in html
    _assert_never_null_undefined(html)
