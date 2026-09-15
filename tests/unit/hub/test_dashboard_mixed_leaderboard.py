"""A "mixed" race (config.race_type === "mixed") races several equipment
groups from one start, each group with its own race_type/target
(config.groups, see hub_server.domain.models.RaceGroup). Progress rows are
tagged with a group_index (or null for unmatched equipment / unbound
station placeholders). Ranking everyone together by one global race_type
(the pre-existing behaviour) is meaningless once "mixed" reaches any of the
~23 race_type branches in index.html -- this pins the leaderboard side of
the fix: one section per group, each internally ranked and scored by its
OWN group.race_type, rendered regardless of leaderboardDisplayMode.

Same brace-depth extraction technique as
tests/unit/hub/test_dashboard_leaderboard_card_cache.py -- the REAL,
unmodified functions are pulled out of index.html and run under node, never
matched by source-text grep, so a nearby comment mentioning the same words
can't satisfy these tests.

No apostrophes in this file's comments: the extraction helpers below track
quote characters without skipping comments, so one would corrupt extraction
for every function defined after it in the string.
"""

import json
import re
import subprocess
from pathlib import Path

from hub_server.infrastructure.locales import (
    SUPPORTED_LOCALES,
    load_locale,
)

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


_FN_NAMES = [
    "metricNumber",
    "sortLeaderboardNodes",
    "formatResultScore",
    "formatMixedGroupTargetLabel",
    "buildMixedGroupHeading",
    "buildMixedRaceTargetSummary",
    "buildMixedLeaderboardSections",
    "renderMixedLeaderboardSectionRows",
    "renderMixedLeaderboardSections",
    "resetLeaderboardCardCache",
    "isLeaderboardFinal",
    "renderRegistrationEmptyState",
    "renderLeaderboard",
]


def _extract_all_fns() -> str:
    source = _read_index()
    return "\n".join(
        _strip_js_comments(_extract_function(source, name)) for name in _FN_NAMES
    )


def _t_stub() -> str:
    return "const t = (key) => `T[${key}]`;\n"


def _stubs() -> str:
    return (
        "const escapeHtml = (value) => String(value == null ? '' : value);\n"
        "const nodeDisplayName = (node) => (node && (node.node_display_name || node.display_name || node.node_id)) || '--';\n"
        "function detectAthleteFinishes() {}\n"
        "function detectRelayHandoffs() {}\n"
        "function triggerFinishCelebration() {}\n"
        "let renderSprintBoardLeaderboardCalls = 0;\n"
        "function renderSprintBoardLeaderboard() { renderSprintBoardLeaderboardCalls += 1; }\n"
        "let renderRaceTrackLeaderboardCalls = 0;\n"
        "function renderRaceTrackLeaderboard() { renderRaceTrackLeaderboardCalls += 1; }\n"
        "let renderTeamBattleLeaderboardCalls = 0;\n"
        "function renderTeamBattleLeaderboard() { renderTeamBattleLeaderboardCalls += 1; }\n"
        "let renderTeamLeaderboardCalls = 0;\n"
        "function renderTeamLeaderboard() { renderTeamLeaderboardCalls += 1; }\n"
        "let leaderboardNodes = [];\n"
        "let teamLeaderboardRows = [];\n"
        "let leaderboardRankByNode = new Map();\n"
        "let leaderboardCardRefs = new Map();\n"
        "let leaderboardCardSignature = null;\n"
        "let currentSessionMode = 'race';\n"
        "let currentState = 'RUNNING';\n"
        "let currentConfig = null;\n"
        "let currentSignupQrUrl = '';\n"
    )


def _fake_dom() -> str:
    return """
function makeContainer() {
  let html = "";
  const container = { querySelectorAll() { return []; }, querySelector() { return null; } };
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


def _run_node(script: str):
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# -- Pure helper tests -----------------------------------------------------


def _format_target_label(group_js: str) -> str:
    source = _strip_js_comments(_read_index())
    fn = _strip_js_comments(_extract_function(source, "formatMixedGroupTargetLabel"))
    metric_fn = _strip_js_comments(_extract_function(source, "metricNumber"))
    script = (
        metric_fn
        + "\n"
        + fn
        + "\n"
        + f"console.log(formatMixedGroupTargetLabel({group_js}));"
    )
    return _run_node(script)


def test_format_mixed_group_target_label_distance():
    assert _format_target_label('{race_type: "distance", target_value: 800}') == "800 m"


def test_format_mixed_group_target_label_calories():
    assert (
        _format_target_label('{race_type: "calories", target_value: 30}') == "30 kcal"
    )


def test_format_mixed_group_target_label_time():
    assert _format_target_label('{race_type: "time", duration_sec: 60}') == "60 s"


def _build_heading(group_js: str) -> str:
    source = _strip_js_comments(_read_index())
    metric_fn = _strip_js_comments(_extract_function(source, "metricNumber"))
    target_fn = _strip_js_comments(
        _extract_function(source, "formatMixedGroupTargetLabel")
    )
    heading_fn = _strip_js_comments(_extract_function(source, "buildMixedGroupHeading"))
    script = (
        _t_stub()
        + metric_fn
        + "\n"
        + target_fn
        + "\n"
        + heading_fn
        + "\n"
        + f"console.log(buildMixedGroupHeading({group_js}));"
    )
    return _run_node(script)


def test_build_mixed_group_heading_joins_equipment_race_type_and_target():
    heading = _build_heading(
        '{equipment_types: ["treadmill", "curved_treadmill"], race_type: "distance", target_value: 800}'
    )
    assert (
        heading
        == "T[equipment_type.treadmill]/T[equipment_type.curved_treadmill] · T[race_type.distance] · 800 m"
    )


# -- buildMixedLeaderboardSections -----------------------------------------


_GROUPS_JS = """[
  {equipment_types: ["treadmill"], race_type: "distance", target_value: 800, duration_sec: 0},
  {equipment_types: ["rowing_machine"], race_type: "calories", target_value: 30, duration_sec: 0}
]"""

_PROGRESS_JS = """{
  n1: {node_id: "n1", group_index: 0, progress_percent: 50, distance_m: 400, station_number: 1},
  n2: {node_id: "n2", group_index: 0, progress_percent: 80, distance_m: 640, station_number: 2},
  n3: {node_id: "n3", group_index: 1, progress_percent: 20, calories: 6, station_number: 3}
}"""


def _build_sections(progress_js: str, groups_js: str) -> list:
    source = _strip_js_comments(_read_index())
    fns = "\n".join(
        _strip_js_comments(_extract_function(source, name))
        for name in (
            "metricNumber",
            "sortLeaderboardNodes",
            "formatMixedGroupTargetLabel",
            "buildMixedGroupHeading",
            "buildMixedLeaderboardSections",
        )
    )
    script = (
        _t_stub()
        + fns
        + "\n"
        + f"console.log(JSON.stringify(buildMixedLeaderboardSections({progress_js}, {{groups: {groups_js}}})));"
    )
    return json.loads(_run_node(script))


def test_sections_split_rows_by_group_index_and_sort_within_own_group():
    sections = _build_sections(_PROGRESS_JS, _GROUPS_JS)
    assert len(sections) == 2
    assert [row["node_id"] for row in sections[0]["rows"]] == ["n2", "n1"]
    assert [row["node_id"] for row in sections[1]["rows"]] == ["n3"]


def test_second_group_sorts_by_its_own_race_type_not_the_first_groups():
    """Group 1 is calories-scored; ranking it by group 0's race_type
    (distance) instead of its own would fall back to comparing station
    numbers (both rows have zero distance_m/progress_percent), producing
    the OPPOSITE order from a correct calories-descending sort. Catches a
    mutation that hardcodes groups[0].race_type for every section."""
    progress = """{
      n5: {node_id: "n5", group_index: 1, calories: 50, station_number: 10},
      n6: {node_id: "n6", group_index: 1, calories: 10, station_number: 1}
    }"""
    sections = _build_sections(progress, _GROUPS_JS)
    assert [row["node_id"] for row in sections[1]["rows"]] == ["n5", "n6"]


def test_no_ungrouped_section_when_every_row_has_a_group():
    sections = _build_sections(_PROGRESS_JS, _GROUPS_JS)
    assert len(sections) == 2  # no third "ungrouped" section


def test_ungrouped_rows_form_a_trailing_section_when_present():
    progress = _PROGRESS_JS[:-1] + """,
      n4: {node_id: "n4", group_index: null, progress_percent: 10, station_number: 4}
    }"""
    sections = _build_sections(progress, _GROUPS_JS)
    assert len(sections) == 3
    assert sections[2]["groupIndex"] is None
    assert sections[2]["heading"] == "T[leaderboard.ungrouped]"
    assert [row["node_id"] for row in sections[2]["rows"]] == ["n4"]


# -- Per-group score formatting (renderMixedLeaderboardSections) -----------


def _render_sections_html(progress_js: str, groups_js: str) -> str:
    source = _strip_js_comments(_read_index())
    fns = "\n".join(
        _strip_js_comments(_extract_function(source, name))
        for name in (
            "metricNumber",
            "sortLeaderboardNodes",
            "formatResultScore",
            "formatMixedGroupTargetLabel",
            "buildMixedGroupHeading",
            "buildMixedLeaderboardSections",
            "renderMixedLeaderboardSectionRows",
            "renderMixedLeaderboardSections",
        )
    )
    script = (
        _t_stub()
        + "const escapeHtml = (value) => String(value == null ? '' : value);\n"
        + "const nodeDisplayName = (node) => (node && (node.node_display_name || node.display_name || node.node_id)) || '--';\n"
        + fns
        + "\n"
        + f"const sections = buildMixedLeaderboardSections({progress_js}, {{groups: {groups_js}}});\n"
        + "console.log(renderMixedLeaderboardSections(sections));"
    )
    return _run_node(script)


_TIME_AND_MAX_POWER_GROUPS_JS = """[
  {equipment_types: ["treadmill"], race_type: "time", target_value: 0, duration_sec: 60},
  {equipment_types: ["fan_bike"], race_type: "max_power", target_value: 0, duration_sec: 30}
]"""

_TIME_AND_MAX_POWER_PROGRESS_JS = """{
  n7: {node_id: "n7", group_index: 0, distance_m: 1234, station_number: 1},
  n8: {node_id: "n8", group_index: 1, max_power_watts: 250, station_number: 2}
}"""


def test_each_section_is_scored_by_its_own_group_race_type_not_a_shared_one():
    """A time group scores by distance_m ("1234m"), a max_power group by
    max_power_watts ("250W") -- neither ever shows a percent value. The
    leaderboard sort test cannot see a raceType mixup here since sorting a
    single-row group never reorders anything, and distance vs calories
    format identically in formatResultScore, so only a time or max_power
    row actually exposes the wrong raceType reaching
    renderMixedLeaderboardSectionRows."""
    html = _render_sections_html(
        _TIME_AND_MAX_POWER_PROGRESS_JS, _TIME_AND_MAX_POWER_GROUPS_JS
    )
    assert "1234m" in html
    assert "250W" in html
    assert "%" not in html


# -- renderLeaderboard wiring -----------------------------------------------


def _run_render_leaderboard(script_body: str) -> dict:
    script = (
        _t_stub() + _stubs() + _fake_dom() + _extract_all_fns() + "\n" + script_body
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def test_mixed_race_renders_sections_regardless_of_display_mode():
    result = _run_render_leaderboard(f"""
currentConfig = {{ race_type: "mixed", groups: {_GROUPS_JS} }};
leaderboardDisplayMode = "sprint_board";
renderLeaderboard({_PROGRESS_JS});
console.log(JSON.stringify({{
  html: leaderboardContainer.innerHTML,
  sprintCalls: renderSprintBoardLeaderboardCalls,
  raceTrackCalls: renderRaceTrackLeaderboardCalls,
}}));
""")
    assert result["sprintCalls"] == 0
    assert result["raceTrackCalls"] == 0
    assert "T[equipment_type.treadmill]" in result["html"]
    assert "T[equipment_type.rowing_machine]" in result["html"]


def test_mixed_race_empty_progress_shows_waiting_state():
    result = _run_render_leaderboard(f"""
currentConfig = {{ race_type: "mixed", groups: {_GROUPS_JS} }};
leaderboardDisplayMode = "classic";
renderLeaderboard({{}});
console.log(JSON.stringify({{ html: leaderboardContainer.innerHTML }}));
""")
    assert "registration-empty" in result["html"]


# -- Locale parity -----------------------------------------------------


def test_leaderboard_ungrouped_key_exists_in_every_locale():
    for locale in SUPPORTED_LOCALES:
        messages = load_locale(locale)["messages"]
        assert "leaderboard.ungrouped" in messages, f"missing in {locale}"


def test_leaderboard_ungrouped_zh_tw_is_genuinely_chinese():
    messages = load_locale("zh-TW")["messages"]
    value = messages["leaderboard.ungrouped"]
    assert re.search(r"[一-鿿]", value), value
