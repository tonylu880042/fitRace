"""Regression test for the dashboard's (hub_server/static/index.html)
podium when a distance/calories race is stopped with unfinished runners.

Problem (reproduced on real hardware): an individual 500m distance race
stopped with 2 runners finished and 2 not (93.2% / 79.7% progress, no
finished_time_ms) put the unfinished 93.2% runner on the #3 bronze podium
card, because renderPodium()/renderTeamPodium()/showPodiumOverlay() all
just took the first three ranked entries via `.slice(0, 3)` without
checking whether they had actually finished.

Fix: in target races (distance/calories) only finished entries are
podium-eligible -- individual: finished_time_ms != null; team:
team_finished === true. If only 2 finished, 2 cards render; if 0
finished, no podium renders. Time/max_power/watts races are unchanged
(every competitor has a score there).

This extracts the real renderPodium(), renderTeamPodium() and
showPodiumOverlay() (with their real helpers) out of the page's inline
<script> and runs them under node, per the technique established in
tests/unit/hub/test_dashboard_readiness_notice.py. Asserting on the
returned/rendered HTML's actual card count and rank labels -- not a
substring near a comment -- means reverting the finished-filter is caught.
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _stripped_script() -> str:
    source = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _extract(name_start: str, name_end: str) -> str:
    script = _stripped_script()
    start = script.index(name_start)
    end = script.index(name_end, start)
    return script[start:end]


def _extract_podium_block() -> str:
    """getMedalMeta through renderTeamPodium (up to, not including,
    aggregateTeamLeaderboard). Grabbing the real bodies means a dropped
    finished-filter is exercised, not just pattern-matched."""
    return _extract("function getMedalMeta", "function aggregateTeamLeaderboard")


def _extract_overlay_block() -> str:
    """buildPodiumOverlayCard through showPodiumOverlay (up to, not
    including, recordWallTypeLabel)."""
    return _extract("function buildPodiumOverlayCard", "function recordWallTypeLabel")


def _extract_metric_number() -> str:
    script = _stripped_script()
    start = script.index("function metricNumber(value, fallback = 0)")
    end = script.index("\n    }\n", start) + len("\n    }\n")
    return script[start:end]


def _run_node(js_source: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(js_source)
        tmp_file.flush()
        tmp_path = tmp_file.name
    try:
        result = subprocess.run(
            ["node", tmp_path], capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0, f"node failed: {result.stderr}"
        return result.stdout
    finally:
        Path(tmp_path).unlink()


_STUB_PREFIX = """
function t(key, params = {}) {
  let value = key;
  Object.entries(params).forEach(([name, replacement]) => {
    value = value.replaceAll(`{${name}}`, String(replacement));
  });
  return value;
}
function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
function nodeDisplayName(node) {
  return node?.node_display_name || node?.display_name || node?.node_id || "--";
}
"""


# ---------------------------------------------------------------------------
# renderPodium (individual)
# ---------------------------------------------------------------------------


def _render_podium(nodes, race_type):
    metric_number = _extract_metric_number()
    body = _extract_podium_block()
    harness = f"""
{metric_number}
{_STUB_PREFIX}
{body}

const result = renderPodium({json.dumps(nodes)}, {json.dumps(race_type)});
console.log(JSON.stringify({{ result }}));
"""
    output = _run_node(harness)
    return json.loads(output.strip().splitlines()[-1])["result"]


def test_podium_functions_are_actually_defined():
    body = _extract_podium_block()
    assert "function renderPodium" in body
    assert "function renderTeamPodium" in body


def test_unfinished_runner_excluded_from_distance_podium():
    nodes = [
        {"athlete_name": "A", "finished_time_ms": 134076, "progress_percent": 100},
        {"athlete_name": "B", "finished_time_ms": 150790, "progress_percent": 100},
        {"athlete_name": "C", "finished_time_ms": None, "progress_percent": 93.2},
        {"athlete_name": "D", "finished_time_ms": None, "progress_percent": 79.7},
    ]
    html = _render_podium(nodes, "distance")
    assert html.count("podium-card") == 2
    assert 'podium-name">A' in html
    assert 'podium-name">B' in html
    assert 'podium-name">C' not in html
    assert 'podium-name">D' not in html
    assert "place-3" not in html


def test_all_unfinished_distance_race_renders_no_podium():
    nodes = [
        {"athlete_name": "A", "finished_time_ms": None, "progress_percent": 40},
        {"athlete_name": "B", "finished_time_ms": None, "progress_percent": 20},
    ]
    html = _render_podium(nodes, "distance")
    assert html == ""


def test_calories_race_also_filters_unfinished():
    nodes = [
        {"athlete_name": "A", "finished_time_ms": 90000, "progress_percent": 100},
        {"athlete_name": "B", "finished_time_ms": None, "progress_percent": 60},
    ]
    html = _render_podium(nodes, "calories")
    assert html.count("podium-card") == 1
    assert 'podium-name">A' in html
    assert 'podium-name">B' not in html


def test_time_race_type_unaffected_unfinished_field_ignored():
    # Time/max_power/watts races: everyone has a score -- finished_time_ms
    # is irrelevant here and must NOT be used to filter.
    nodes = [
        {"athlete_name": "A", "finished_time_ms": None, "distance_m": 1200},
        {"athlete_name": "B", "finished_time_ms": None, "distance_m": 900},
        {"athlete_name": "C", "finished_time_ms": None, "distance_m": 700},
    ]
    html = _render_podium(nodes, "time")
    assert html.count("podium-card") == 3


# ---------------------------------------------------------------------------
# renderTeamPodium
# ---------------------------------------------------------------------------


def _render_team_podium(teams, race_type):
    metric_number = _extract_metric_number()
    body = _extract_podium_block()
    harness = f"""
{metric_number}
{_STUB_PREFIX}
{body}

const result = renderTeamPodium({json.dumps(teams)}, {json.dumps(race_type)});
console.log(JSON.stringify({{ result }}));
"""
    output = _run_node(harness)
    return json.loads(output.strip().splitlines()[-1])["result"]


def test_unfinished_team_excluded_from_distance_podium():
    teams = [
        {
            "team_name": "Alpha",
            "team_finished": True,
            "score_value": 100,
            "score_label": "distance_m",
            "member_count": 2,
        },
        {
            "team_name": "Beta",
            "team_finished": False,
            "score_value": 80,
            "score_label": "distance_m",
            "member_count": 2,
        },
    ]
    html = _render_team_podium(teams, "distance")
    assert html.count("podium-card") == 1
    assert 'podium-name">Alpha' in html
    assert 'podium-name">Beta' not in html


# ---------------------------------------------------------------------------
# showPodiumOverlay (post-race overlay, DOM-driven)
# ---------------------------------------------------------------------------


def _make_overlay_dom_harness():
    return """
function makeEl() {
  return {
    _innerHTML: null,
    classList: { add() {}, remove() {} },
    set innerHTML(v) { this._innerHTML = v; },
    get innerHTML() { return this._innerHTML; },
    addEventListener() {},
  };
}
const overlayEl = makeEl();
let appended = false;
const document = {
  getElementById: (id) => (id === "podium-overlay" && appended ? overlayEl : null),
  createElement: () => overlayEl,
  body: { appendChild: () => { appended = true; } },
};
let rafCalls = 0;
function requestAnimationFrame(cb) { rafCalls += 1; cb(); }
let timeoutCalls = 0;
const window = {
  setTimeout: (fn, ms) => { timeoutCalls += 1; return 1; },
  clearTimeout: () => {},
};
let podiumOverlayTimer = null;
"""


def _show_podium_overlay(*, current_config, team_rows, node_rows):
    metric_number = _extract_metric_number()
    podium_body = _extract_podium_block()
    overlay_body = _extract_overlay_block()
    harness = f"""
{metric_number}
{_STUB_PREFIX}
{podium_body}
{_make_overlay_dom_harness()}
{overlay_body}

const currentConfig = {json.dumps(current_config)};
const teamLeaderboardRows = {json.dumps(team_rows)};
const leaderboardNodes = {json.dumps(node_rows)};

showPodiumOverlay();
console.log(JSON.stringify({{ html: overlayEl._innerHTML }}));
"""
    output = _run_node(harness)
    return json.loads(output.strip().splitlines()[-1])["html"]


def test_overlay_excludes_unfinished_individual_runners():
    node_rows = [
        {"athlete_name": "A", "finished_time_ms": 134076, "progress_percent": 100},
        {"athlete_name": "B", "finished_time_ms": 150790, "progress_percent": 100},
        {"athlete_name": "C", "finished_time_ms": None, "progress_percent": 93.2},
    ]
    html = _show_podium_overlay(
        current_config={"race_type": "distance", "competition_mode": "individual"},
        team_rows=[],
        node_rows=node_rows,
    )
    assert html.count("podium-overlay-card") == 2
    assert 'podium-overlay-name">A' in html
    assert 'podium-overlay-name">B' in html
    assert 'podium-overlay-name">C' not in html


def test_overlay_renders_nothing_when_no_one_finished():
    node_rows = [
        {"athlete_name": "A", "finished_time_ms": None, "progress_percent": 50},
    ]
    html = _show_podium_overlay(
        current_config={"race_type": "distance", "competition_mode": "individual"},
        team_rows=[],
        node_rows=node_rows,
    )
    assert html is None


def test_overlay_excludes_unfinished_team_in_team_mode():
    # Team-mode branch of the same overlay filter: an unfinished team must
    # not appear even though it is ranked ahead in teamLeaderboardRows.
    team_rows = [
        {
            "team_name": "Alpha",
            "team_finished": True,
            "score_value": 100,
            "score_label": "distance_m",
        },
        {
            "team_name": "Beta",
            "team_finished": False,
            "score_value": 80,
            "score_label": "distance_m",
        },
        {
            "team_name": "Gamma",
            "team_finished": True,
            "score_value": 60,
            "score_label": "distance_m",
        },
    ]
    html = _show_podium_overlay(
        current_config={"race_type": "distance", "competition_mode": "team"},
        team_rows=team_rows,
        node_rows=[],
    )
    assert html.count("podium-overlay-card") == 2
    assert 'podium-overlay-name">Alpha' in html
    assert 'podium-overlay-name">Gamma' in html
    assert 'podium-overlay-name">Beta' not in html


def test_overlay_renders_nothing_when_no_team_finished():
    team_rows = [
        {
            "team_name": "Alpha",
            "team_finished": False,
            "score_value": 100,
            "score_label": "distance_m",
        },
        {
            "team_name": "Beta",
            "team_finished": False,
            "score_value": 80,
            "score_label": "distance_m",
        },
    ]
    html = _show_podium_overlay(
        current_config={"race_type": "distance", "competition_mode": "team"},
        team_rows=team_rows,
        node_rows=[],
    )
    assert html is None
