"""Regression test: Race Track duplicated the word "progress" under an
unfinished score.

renderRaceTrackLeaderboard's per-row score sub-label renders
`${row.score.label} · ${row.finished}`. For the default (distance/
calories) race type, before a node finishes, formatResultScore's label is
t("metric.progress") ("進度" / "Progress") and row.finished was built from
t("leaderboard.percent_progress", { percent }) -- "{percent}% 進度" /
"{percent}% progress" -- which already contains the same word. Concatenated
together the sub-label read "進度 · 64.0% 進度" (en-US: "Progress · 64.0%
progress").

Fix: row.finished, for the still-running branch, is now just the bare
percentage ("64.0%") with no locale string baked in -- the word appears
exactly once, contributed solely by row.score.label. leaderboard.
percent_progress is otherwise unused in index.html (grepped), so its
locale entries and the key-existence check in test_i18n_locales.py /
test_dashboard_i18n.py are untouched.

This module extracts the real renderRaceTrackLeaderboard (and its real
collaborators) from index.html's inline <script> (comment-stripped, same
brace-depth technique used throughout tests/unit/hub/) and runs it under
node with a real interpolating t() backed by the actual locale JSON files
-- same accepted pattern as tests/unit/hub/test_dashboard_relay_stage_label.py
-- so a failure means the real translated sub-label came out wrong, not
merely that some substring exists somewhere in the source.
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


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as file:
        return json.load(file)


_T_STUB = """
function t(key, params = {}) {
  let value = MESSAGES[key] || key;
  Object.entries(params).forEach(([name, replacement]) => {
    value = value.replaceAll(`{${name}}`, String(replacement));
  });
  return value;
}
"""

_FN_NAMES = [
    "isRunningEquipment",
    "formatPacePerKm",
    "paceBand",
    "metricNumber",
    "nodeDisplayName",
    "stationLabel",
    "relayLegLine",
    "formatResultScore",
    "sortLeaderboardNodes",
    "getRankedIndividualRows",
    "raceBoardDensityTier",
    "renderRaceTrackLeaderboard",
]


def _stubs() -> str:
    return (
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
        "function renderMemberProgressChips() { return ''; }\n"
        "function getRankedTeamRows() { return []; }\n"
        "function formatTeamScore() { return { value: '', label: '' }; }\n"
        "function teamCompletionLabel() { return ''; }\n"
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


def _node(progress=64.0):
    return {
        "node_id": "n1",
        "station_number": 1,
        "athlete_name": "Athlete 1",
        "power_watts": 100,
        "instantaneous_speed_kph": 20,
        "distance_m": 300,
        "progress_percent": progress,
    }


def _render_race_track(locale: str, node: dict) -> str:
    messages = _load_locale(locale)
    script = (
        f"const MESSAGES = {json.dumps(messages)};\n"
        + _T_STUB
        + _stubs()
        + _fake_dom()
        + _extract_fns()
        + f"\nrenderRaceTrackLeaderboard({json.dumps({node['node_id']: node})});"
        + "\nconsole.log(leaderboardContainer.innerHTML);"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout


def _score_sublabel(html: str) -> str:
    match = re.search(
        r'<div class="race-track-score">.*?<div class="metric-lbl">(.*?)</div>',
        html,
        re.DOTALL,
    )
    assert match, f"race-track-score sub-label not found in:\n{html}"
    return match.group(1)


def test_race_track_progress_sublabel_says_progress_word_once_zh_tw():
    html = _render_race_track("zh-TW", _node(64.0))
    sublabel = _score_sublabel(html)
    assert (
        sublabel.count("進度") == 1
    ), f"expected the word 進度 exactly once, got: {sublabel!r}"
    assert (
        "64.0%" in sublabel
    ), f"expected the percentage in the sub-label: {sublabel!r}"


def test_race_track_progress_sublabel_says_progress_word_once_en_us():
    html = _render_race_track("en-US", _node(64.0))
    sublabel = _score_sublabel(html)
    assert (
        sublabel.lower().count("progress") == 1
    ), f"expected the word progress exactly once, got: {sublabel!r}"
    assert (
        "64.0%" in sublabel
    ), f"expected the percentage in the sub-label: {sublabel!r}"
