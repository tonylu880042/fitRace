"""Regression test: Sprint Board card text clipping.

With 4 stations (e.g. at 1920x1080 and 1280x720) each Sprint Board card's
station/meta line ("站位 1 · FITRACE-EDGE-02-01") wraps in the middle of the
node id, and the node-id stat cell ("fitrace-edge-02-01") shows the value
hard-clipped mid-character with no ellipsis, because neither
.sprint-board-meta nor .sprint-stat strong ever declared overflow/
text-overflow -- .sprint-stat strong had white-space: nowrap alone, which
overflows the card instead of clipping.

Fix: both rules gain white-space: nowrap; overflow: hidden; text-overflow:
ellipsis, and the flex item that wraps the rank/name/meta/relay-leg block
inside .sprint-board-topline (previously an unstyled <div>, so its
automatic min-width: auto kept it at its content's full intrinsic width
and defeated any overflow rule on its children) gets a class
(.sprint-board-info) carrying min-width: 0 so the ellipsis can actually
engage.

This module extracts the real renderSprintBoardLeaderboard (and its real
collaborators) from index.html's inline <script> (comment-stripped, same
brace-depth technique used throughout tests/unit/hub/) and runs it under
node against a minimal fake container -- same accepted pattern as
tests/unit/hub/test_dashboard_race_board_density.py -- plus structural CSS
checks for the three rules involved.
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
    return source[start : _matching_brace_end(source, brace_open) + 1]


# ---------------------------------------------------------------------------
# renderSprintBoardLeaderboard: real collaborators, fake DOM (innerHTML
# set/get only), stubs for peripheral helpers -- same accepted pattern as
# tests/unit/hub/test_dashboard_race_board_density.py's _BOARD_FN_NAMES.
# ---------------------------------------------------------------------------

_FN_NAMES = [
    "isRunningEquipment",
    "formatPacePerKm",
    "paceBand",
    "fastestPaceNodeId",
    "raceBoardDensityTier",
    "metricNumber",
    "nodeDisplayName",
    "stationLabel",
    "relayLegLine",
    "formatResultScore",
    "sortLeaderboardNodes",
    "getRankedIndividualRows",
    "renderSprintBoardLeaderboard",
]


def _stubs() -> str:
    return (
        "const t = (key, params = {}) => { let value = `T[${key}]`; "
        "Object.entries(params).forEach(([name, replacement]) => { "
        "value = value.replaceAll(`{${name}}`, String(replacement)); }); "
        "return value; };\n"
        "const escapeHtml = (value) => String(value == null ? '' : value);\n"
        "let leaderboardSmoothValues = new Map();\n"
        "function smoothMetricNumber(key, targetValue) { const target = metricNumber(targetValue); leaderboardSmoothValues.set(key, target); return target; }\n"
        "function resetLeaderboardCardCache() {}\n"
        "function animateLeaderboardReorder() {}\n"
        "function triggerFinishCelebration() {}\n"
        "function renderLeaderboardEmptyState() { return ''; }\n"
        "function renderPodium() { return ''; }\n"
        "function renderTeamPodium() { return ''; }\n"
        "function isLeaderboardFinal() { return false; }\n"
        "function isTeamLeaderboardFinal() { return false; }\n"
        "function getRankedTeamRows() { return []; }\n"
        "function formatTeamScore() { return { value: '', label: '' }; }\n"
        "function teamPolicyLabel() { return ''; }\n"
        "function formatSprintSignal(row, raceType) { return { value: `${metricNumber(row.raw.power_watts).toFixed(0)}W`, label: 'power', band: 'none' }; }\n"
        "let leaderboardNodes = [];\n"
        "let teamLeaderboardRows = [];\n"
        "let leaderboardRankByNode = new Map();\n"
        "let currentState = 'IDLE';\n"
        "let currentConfig = { race_type: 'distance', competition_mode: 'individual' };\n"
    )


def _fake_dom() -> str:
    return r"""
function makeContainer() {
  let html = "";
  const container = {
    querySelectorAll() { return []; },
    querySelector() { return null; },
  };
  Object.defineProperty(container, "innerHTML", {
    get() { return html; },
    set(value) { html = value; },
  });
  return container;
}

const leaderboardContainer = makeContainer();
const document = {
  getElementById(id) { return id === "leaderboard-container" ? leaderboardContainer : null; },
  querySelectorAll() { return []; },
};
"""


def _extract_fns() -> str:
    source = _read_index()
    return "\n".join(
        _strip_js_comments(_extract_function(source, name)) for name in _FN_NAMES
    )


def _node(node_id="n1", station_number=1, node_display_name="FITRACE-EDGE-02-01"):
    return {
        "node_id": node_id,
        "node_display_name": node_display_name,
        "station_number": station_number,
        "athlete_name": f"Athlete {station_number}",
        "power_watts": 100,
        "instantaneous_speed_kph": 20,
        "distance_m": 300,
        "calories": 12.3,
        "progress_percent": 40,
    }


def _render_sprint_board(node) -> str:
    import json

    script = (
        _stubs()
        + _fake_dom()
        + _extract_fns()
        + f"\nrenderSprintBoardLeaderboard({json.dumps({node['node_id']: node})});"
        + "\nconsole.log(leaderboardContainer.innerHTML);"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout


def _first_card(html: str) -> str:
    match = re.search(
        r'<div class="sprint-board-card[^"]*"[^>]*>.*?(?=<div class="sprint-board-card|\Z)',
        html,
        re.DOTALL,
    )
    assert match, "no sprint-board-card rendered"
    return match.group(0)


def test_rendered_topline_info_wrapper_carries_min_width_reset_class():
    """The wrapper div holding rank/name/meta/relay-leg (a flex item inside
    .sprint-board-topline) must carry a class this test's CSS check can key
    off of -- an unstyled <div> keeps its automatic min-width: auto and
    stays at its content's full intrinsic width no matter what overflow
    rule its children declare."""
    card = _first_card(_render_sprint_board(_node()))
    assert re.search(
        r'<div class="sprint-board-info">\s*<div class="sprint-board-rank">',
        card,
    ), f"topline info wrapper is missing the sprint-board-info class:\n{card}"


def test_css_sprint_board_info_resets_min_width():
    css = _read_index()
    assert re.search(
        r"\.sprint-board-info\s*\{[^}]*min-width:\s*0", css
    ), ".sprint-board-info does not reset min-width to 0"


def test_css_sprint_board_meta_is_single_line_with_ellipsis():
    css = _read_index()
    match = re.search(r"\.sprint-board-meta\s*\{([^}]*)\}", css)
    assert match, ".sprint-board-meta rule not found"
    body = match.group(1)
    assert (
        "white-space: nowrap" in body
    ), f".sprint-board-meta is not forced to one line: {body}"
    assert (
        "overflow: hidden" in body
    ), f".sprint-board-meta does not clip overflow: {body}"
    assert (
        "text-overflow: ellipsis" in body
    ), f".sprint-board-meta does not ellipsize: {body}"


def test_css_sprint_stat_value_is_single_line_with_ellipsis():
    css = _read_index()
    match = re.search(r"\.sprint-stat strong\s*\{([^}]*)\}", css)
    assert match, ".sprint-stat strong rule not found"
    body = match.group(1)
    assert (
        "white-space: nowrap" in body
    ), f".sprint-stat strong is not forced to one line: {body}"
    assert (
        "overflow: hidden" in body
    ), f".sprint-stat strong does not clip overflow: {body}"
    assert (
        "text-overflow: ellipsis" in body
    ), f".sprint-stat strong does not ellipsize: {body}"
