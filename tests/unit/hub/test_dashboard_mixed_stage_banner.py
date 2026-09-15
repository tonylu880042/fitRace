"""A mixed race (config.race_type === "mixed") carries its targets inside
config.groups, not in the top-level target_value/duration_sec that every
other race_type uses. Two dashboard call sites built a single-race-wide
target string straight off currentConfig.race_type -- getRaceStageDetails
(the stage banner "sub" text, e.g. "Target 800m") and renderDashboardChrome
(the header config description / type indicator) -- and neither of their
branch ladders had a "mixed" case, so a mixed race fell through to the
duration-based branch and showed a bogus "0s" built from the unused
top-level duration_sec.

This pins that both call sites now show t("race_type.mixed") joined with
each group's own target label (buildMixedGroupHeading/
formatMixedGroupTargetLabel, see test_dashboard_mixed_leaderboard.py),
joined " / " across groups, e.g. "800 m / 500 m / 30 kcal". It also pins
that updateStopwatch was deliberately left unchanged: race_type "mixed"
never matches its "distance"/"calories" or "time"/"max_power"/"watts"
branches, so it already just keeps ticking elapsed wall-clock time instead
of ever computing (or freezing on) a bogus remaining-time countdown -- no
code change needed there, but a mutation to add a "mixed" branch that
freezes elapsed at 0 must be caught by a real executing test rather than
merely asserted in a comment.

Same brace-depth extraction / node-execution technique as
tests/unit/hub/test_dashboard_mixed_leaderboard.py. No apostrophes in this
file's comments for the same reason noted there.
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
    return "const t = (key) => `T[${key}]`;\n"


def _metric_number_stub() -> str:
    return (
        "function metricNumber(value, fallback) {\n"
        "  const fb = fallback === undefined ? 0 : fallback;\n"
        "  const n = Number(value);\n"
        "  return Number.isFinite(n) ? n : fb;\n"
        "}\n"
    )


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


_GROUPS_JS = """[
  {equipment_types: ["treadmill"], race_type: "distance", target_value: 800, duration_sec: 0},
  {equipment_types: ["rowing_machine"], race_type: "distance", target_value: 500, duration_sec: 0},
  {equipment_types: ["fan_bike"], race_type: "calories", target_value: 30, duration_sec: 0}
]"""


# -- getRaceStageDetails (stage banner target) ------------------------------


def _stage_target_helpers() -> str:
    source = _strip_js_comments(_read_index())
    return "\n".join(
        _strip_js_comments(_extract_function(source, name))
        for name in (
            "formatMixedGroupTargetLabel",
            "buildMixedRaceTargetSummary",
            "competitionStageLabel",
            "getRaceStageDetails",
        )
    )


def _run_get_race_stage_details(state_js: str, config_js: str) -> dict:
    source = _stage_target_helpers()
    script = (
        _t_stub()
        + _metric_number_stub()
        + "let currentSessionMode = 'race';\n"
        + f"let currentConfig = {config_js};\n"
        + f"let currentState = {state_js};\n"
        + "let raceStageOverride = null;\n"
        + "let stopwatchInterval = null;\n"
        + "const document = { getElementById: () => null };\n"
        + source
        + "\n"
        + f"console.log(JSON.stringify(getRaceStageDetails({state_js})));"
    )
    return json.loads(_run_node(script))


def test_running_stage_shows_group_targets_joined_by_slash():
    details = _run_get_race_stage_details(
        '"RUNNING"', f'{{race_type: "mixed", groups: {_GROUPS_JS}}}'
    )
    assert details["sub"] == "T[stage.running_sub_target]"


def test_ready_stage_target_is_joined_group_targets_not_bogus_zero_seconds():
    """Before the fix, a mixed race fell through getRaceStageDetails
    target ternary to the duration_sec branch -- top-level duration_sec is
    unused/0 for a mixed config, so it silently showed "0s". This proves
    the real per-group target string reaches the caller instead."""
    source = _stage_target_helpers()
    script = (
        _t_stub()
        + _metric_number_stub()
        + "let currentSessionMode = 'race';\n"
        + f'let currentConfig = {{race_type: "mixed", groups: {_GROUPS_JS}}};\n'
        + "let currentState = 'READY';\n"
        + "let raceStageOverride = null;\n"
        + "let stopwatchInterval = null;\n"
        + "const document = { getElementById: () => null };\n"
        + source
        + "\n"
        + "const details = getRaceStageDetails('READY');\n"
        + "console.log(JSON.stringify({ sub: details.sub }));\n"
    )
    result = json.loads(_run_node(script))
    assert result["sub"] == "T[stage.ready_sub_target]"


def test_build_mixed_race_target_summary_joins_every_group_with_slash():
    source = _stage_target_helpers()
    script = (
        _metric_number_stub()
        + source
        + "\n"
        + f"console.log(buildMixedRaceTargetSummary({{groups: {_GROUPS_JS}}}));"
    )
    assert _run_node(script) == "800 m / 500 m / 30 kcal"


# -- renderDashboardChrome (config description / type indicator) -----------


def _run_render_dashboard_chrome(config_js: str) -> dict:
    source = _read_index()
    fns = "\n".join(
        _strip_js_comments(_extract_function(source, name))
        for name in (
            "formatMixedGroupTargetLabel",
            "buildMixedRaceTargetSummary",
            "competitionStageLabel",
            "getRaceStageDetails",
            "getClassStageDetails",
            "readinessBlockingReasons",
            "readinessNoticeHtml",
            "renderRaceStageBanner",
            "metricNumber",
            "renderDashboardChrome",
        )
    )
    script = (
        _t_stub()
        + "const escapeHtml = (v) => String(v == null ? '' : v);\n"
        + "let currentSessionMode = 'race';\n"
        + f"let currentConfig = {config_js};\n"
        + "let currentState = 'READY';\n"
        + "let latestReadiness = null;\n"
        + "let raceStageOverride = null;\n"
        + "let stopwatchInterval = null;\n"
        + "const configDesc = {};\n"
        + "const typeIndicator = {};\n"
        + "const panelTitle = {};\n"
        + "const bannerEl = {};\n"
        + "const kickerEl = {};\n"
        + "const mainEl = {};\n"
        + "const subEl = {};\n"
        + "const timerEl = {};\n"
        + "const document = { getElementById: (id) => ({\n"
        + '  "current-config-desc": configDesc,\n'
        + '  "race-type-indicator": typeIndicator,\n'
        + '  "leaderboard-panel-title": panelTitle,\n'
        + '  "race-stage-banner": bannerEl,\n'
        + '  "race-stage-kicker": kickerEl,\n'
        + '  "race-stage-main": mainEl,\n'
        + '  "race-stage-sub": subEl,\n'
        + '  "race-stage-timer": timerEl,\n'
        + "}[id] || null) };\n"
        + fns
        + "\n"
        + "renderDashboardChrome();\n"
        + "console.log(JSON.stringify({ configDesc: configDesc.innerText, typeIndicator: typeIndicator.innerText }));"
    )
    return json.loads(_run_node(script))


def test_config_desc_shows_mixed_label_and_joined_group_targets():
    result = _run_render_dashboard_chrome(
        f'{{race_type: "mixed", groups: {_GROUPS_JS}}}'
    )
    assert (
        result["configDesc"]
        == "T[stage.individual] · T[race_type.mixed]: 800 m / 500 m / 30 kcal"
    )


def test_type_indicator_shows_mixed_label_and_joined_group_targets():
    result = _run_render_dashboard_chrome(
        f'{{race_type: "mixed", groups: {_GROUPS_JS}}}'
    )
    assert (
        result["typeIndicator"]
        == "T[stage.individual] · T[race_type.mixed] (800 m / 500 m / 30 kcal)"
    )


# -- updateStopwatch: deliberately unchanged for mixed ----------------------


def test_updatestopwatch_never_freezes_elapsed_for_a_mixed_race():
    """updateStopwatch's allFinished/elapsed-pinning branches only match
    race_type distance/calories/time/max_power/watts. A mixed race must
    keep ticking live elapsed time rather than being pinned to 0 by a
    stray duration_sec branch -- this executes the real function with a
    frozen Date.now() to prove elapsed keeps advancing."""
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "updateStopwatch"))
    format_fn = _strip_js_comments(_extract_function(source, "formatElapsedTime"))
    script = (
        _metric_number_stub() + "let currentState = 'RUNNING';\n"
        "let currentConfig = { race_type: 'mixed', groups: [] };\n"
        "let leaderboardNodes = [];\n"
        "let raceStartTime = 1000;\n"
        "let raceEndTime = null;\n"
        "let stopwatchInterval = 1;\n"
        "function clearInterval() {}\n"
        "const stopwatchLbl = {};\n"
        "function renderRaceStageBanner() {}\n"
        "const document = { getElementById: (id) => (id === 'stopwatch-lbl' ? stopwatchLbl : null) };\n"
        "Date.now = () => 6000;\n"
        + format_fn
        + "\n"
        + fn
        + "\n"
        + "updateStopwatch();\n"
        + "console.log(JSON.stringify({ text: stopwatchLbl.innerText, intervalStillSet: stopwatchInterval !== null }));"
    )
    result = json.loads(_run_node(script))
    assert result["intervalStillSet"] is True
    assert result["text"] == "00:05.0"
