"""Two finish-time dashboard flows ranked a mixed race by one global
race_type: showPodiumOverlay (the full-screen RUNNING -> STOPPED reveal)
and buildRecordWallSlides' "latest race" slide (the idle record wall,
built from GET /api/results/races/{id}, whose race detail response for a
mixed race carries race_type: "mixed", groups: [{group_index, race_type,
target_value, duration_sec, equipment_types}, ...], and results ordered
group 0 then group 1 etc. with rank restarting at 1 per group -- see
hub_server/usecases/race_results_query.py's _rank_and_tag_mixed).

This pins:
  - showPodiumOverlay is a no-op for a mixed race (the sectioned
    leaderboard stays the final view instead of one bogus cross-group
    ranked podium);
  - buildRecordWallSlides emits one slide per group for a mixed
    latestRace, titled with the same heading text the leaderboard section
    uses (buildMixedGroupHeading) and scored by that group's own
    race_type, rather than one slide ranked/scored by "mixed";
  - the non-mixed record-wall slide path is unchanged.

Same brace-depth extraction / node-execution technique as
tests/unit/hub/test_dashboard_mixed_leaderboard.py and
tests/unit/hub/test_record_wall_dnf.py. No apostrophes in this file's
comments for the same reason noted there.
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


def _t_stub() -> str:
    return (
        "const t = (key) => ({"
        '"record_wall.title": "Record Wall",'
        '"record_wall.latest_race": "Latest Race",'
        '"record_wall.type_distance": "Distance",'
        '"record_wall.type_calories": "Calories",'
        '"record_wall.dnf": "DNF",'
        '"stations.station": "Station",'
        '"stations.athlete": "Athlete",'
        '"race_type.distance": "Distance",'
        '"race_type.calories": "Calories",'
        '"equipment_type.treadmill": "Treadmill",'
        '"equipment_type.rowing_machine": "Rowing Machine",'
        '"equipment_type.fan_bike": "Fan Bike"'
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
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def _record_wall_fns(source: str) -> str:
    return "\n".join(
        _strip_js_comments(_extract_function(source, name))
        for name in (
            "recordWallTypeLabel",
            "recordWallDivisionLabel",
            "formatRecordEntryValue",
            "formatRecordProgressValue",
            "formatRecordWallEntryValue",
            "renderRecordWallRows",
            "buildLatestRaceEntry",
            "formatMixedGroupTargetLabel",
            "buildMixedGroupHeading",
            "buildRecordWallSlides",
        )
    )


def _build_slides(records_js: str, latest_race_js: str) -> list:
    source = _strip_js_comments(_read_index())
    fns = _record_wall_fns(source)
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + fns
        + "\n"
        + f"console.log(JSON.stringify(buildRecordWallSlides({records_js}, {latest_race_js})));"
    )
    output = _run_node(script)
    return json.loads(output)


_MIXED_LATEST_RACE_JS = """{
  race_type: "mixed",
  groups: [
    {group_index: 0, race_type: "distance", target_value: 800, duration_sec: 0, equipment_types: ["treadmill"]},
    {group_index: 1, race_type: "calories", target_value: 30, duration_sec: 0, equipment_types: ["fan_bike"]}
  ],
  results: [
    {athlete_name: "Alex", station_number: 1, group_index: 0, rank: 1, finished_time_ms: 60000, distance_m: 800, calories: 5},
    {athlete_name: "Sam", station_number: 2, group_index: 0, rank: 2, finished_time_ms: null, distance_m: 400, calories: 3},
    {athlete_name: "Jamie", station_number: 3, group_index: 1, rank: 1, finished_time_ms: null, distance_m: 100, calories: 30}
  ]
}"""


def test_mixed_latest_race_emits_one_slide_per_group():
    slides = _build_slides("[]", _MIXED_LATEST_RACE_JS)
    assert len(slides) == 2


def test_mixed_slide_title_is_the_group_heading_text():
    slides = _build_slides("[]", _MIXED_LATEST_RACE_JS)
    assert "Treadmill" in slides[0]
    assert "Distance" in slides[0]
    assert "800 m" in slides[0]
    assert "Fan Bike" in slides[1]
    assert "Calories" in slides[1]
    assert "30 kcal" in slides[1]


def test_mixed_slide_rows_are_scoped_to_their_own_group():
    slides = _build_slides("[]", _MIXED_LATEST_RACE_JS)
    assert "Alex" in slides[0] and "Sam" in slides[0]
    assert "Jamie" not in slides[0]
    assert "Jamie" in slides[1]
    assert "Alex" not in slides[1] and "Sam" not in slides[1]


def test_mixed_slide_scores_each_group_by_its_own_race_type():
    # Group 0 (distance): Alex finished -> a finish-time score, not DNF.
    # Group 1 (calories): Jamie never finished a target-based race -> DNF
    # plus raw calories progress, not a bogus finish time.
    slides = _build_slides("[]", _MIXED_LATEST_RACE_JS)
    assert "60.0s" in slides[0]
    assert "DNF" in slides[1]
    assert "30 kcal" in slides[1]


def test_non_mixed_latest_race_slide_is_unchanged():
    latest_race = (
        "{race_type: 'distance', results: ["
        "{athlete_name: 'Alex', station_number: 1,"
        " finished_time_ms: 29600, distance_m: 300, calories: 50}"
        "]}"
    )
    slides = _build_slides("[]", latest_race)
    assert len(slides) == 1
    assert "29.6s" in slides[0]
    assert "Distance" in slides[0]


# -- showPodiumOverlay: skipped for a mixed race ----------------------------


def _run_show_podium_overlay(config_js: str, leaderboard_nodes_js: str) -> dict:
    source = _read_index()
    fns = "\n".join(
        _strip_js_comments(_extract_function(source, name))
        for name in (
            "getMedalMeta",
            "formatResultScore",
            "formatTeamScore",
            "buildPodiumOverlayCard",
            "hidePodiumOverlay",
            "showPodiumOverlay",
            "metricNumber",
        )
    )
    script = (
        _t_stub()
        + "const escapeHtml = (v) => String(v == null ? '' : v);\n"
        + f"let currentConfig = {config_js};\n"
        + f"let leaderboardNodes = {leaderboard_nodes_js};\n"
        + "let teamLeaderboardRows = [];\n"
        + "let podiumOverlayTimer = null;\n"
        + "let overlayCreated = false;\n"
        + "const overlay = { classList: { add() {}, remove() {} }, addEventListener() {} };\n"
        + "const document = {\n"
        + "  getElementById(id) { return id === 'podium-overlay' && overlayCreated ? overlay : null; },\n"
        + "  createElement() { overlayCreated = true; return overlay; },\n"
        + "  body: { appendChild() {} },\n"
        + "};\n"
        + "function requestAnimationFrame() {}\n"
        + "const window = { setTimeout: () => 1, clearTimeout: () => {} };\n"
        + fns
        + "\n"
        + "showPodiumOverlay();\n"
        + "console.log(JSON.stringify({ overlayCreated }));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def test_show_podium_overlay_is_a_no_op_for_a_mixed_race():
    result = _run_show_podium_overlay(
        '{race_type: "mixed", groups: []}',
        '[{node_id: "n1", athlete_name: "Alex", finished_time_ms: 5000}]',
    )
    assert result["overlayCreated"] is False


def test_show_podium_overlay_still_creates_overlay_for_a_non_mixed_race():
    result = _run_show_podium_overlay(
        '{race_type: "distance"}',
        '[{node_id: "n1", athlete_name: "Alex", finished_time_ms: 5000}]',
    )
    assert result["overlayCreated"] is True
