"""The dashboard (hub_server/static/index.html, display-only per
CLAUDE.md) shows a relay team's current leg/runner as a secondary line on
each of the three individual-row leaderboard modes (classic, race_track,
sprint_board), and flashes a transient handoff banner when a team's
relay_leg advances during a RUNNING race. The page's WebSocket handler
ignores typed "race_event" messages, so this is driven off the plain
per-athlete progress rows renderLeaderboard already receives on every
telemetry tick -- not race_event.

Per CLAUDE.md's testability guidance (see test_dashboard_class_next_
segment_announcement.py), this splits into:
  1. relayLegLine(node) -- a pure helper with no DOM -- executed directly
     under node.
  2. computeRelayHandoffCues(progressData, previousLegByNode, raceState)
     -- the pure show/no-show decision -- executed directly under node,
     covering: RUNNING with an advancing leg (cue fires), RUNNING with an
     unchanged leg (no cue), a brand new node_id never seen before (no
     cue, since there's no "previous" to compare against), and NOT
     RUNNING (never fires, but still records the leg so a resumed race
     can't replay a stale handoff).
  3. Source-text assertions (stripped of comments, so a comment can't
     satisfy them) that each of the three row renderers calls
     relayLegLine, and that renderLeaderboard calls detectRelayHandoffs.
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
        '    "dashboard.relay_leg": "Leg {leg}/{legs}",\n'
        '    "dashboard.relay_handoff": "Handoff! Next up: {runner}",\n'
        '    "fallback.team": "Team",\n'
        '    "stations.athlete": "Athlete"\n'
        "  };\n"
        "  let value = messages[key] || key;\n"
        "  Object.entries(params || {}).forEach(([name, replacement]) => {\n"
        "    value = value.replaceAll(`{${name}}`, String(replacement));\n"
        "  });\n"
        "  return value;\n"
        "};\n"
    )


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# -- relayLegLine: pure, no DOM ------------------------------------------


def _run_relay_leg_line(node_js: str) -> str:
    source = _strip_js_comments(_read_index())
    fn = _extract_function(source, "relayLegLine")
    script = (
        _t_stub() + fn + "\n" + f"console.log(JSON.stringify(relayLegLine({node_js})));"
    )
    import json

    return json.loads(_run_node(script))


def test_relay_leg_line_shows_leg_and_runner():
    out = _run_relay_leg_line(
        '{relay_legs: 4, relay_leg: 2, relay_current_runner: "Bob"}'
    )
    assert out == "Leg 2/4 · Bob"


def test_relay_leg_line_empty_for_non_relay_node():
    assert _run_relay_leg_line("{relay_legs: null}") == ""
    assert _run_relay_leg_line("{}") == ""


def test_relay_leg_line_without_runner_name():
    out = _run_relay_leg_line(
        "{relay_legs: 2, relay_leg: 1, relay_current_runner: null}"
    )
    assert out == "Leg 1/2"


# -- computeRelayHandoffCues: pure, no DOM --------------------------------


def _run_handoff_cues(progress_js: str, previous_js: str, race_state: str) -> list:
    source = _strip_js_comments(_read_index())
    fn = _extract_function(source, "computeRelayHandoffCues")
    script = (
        fn
        + "\n"
        + f"const previousLegByNode = new Map(Object.entries({previous_js}));\n"
        + f'const cues = computeRelayHandoffCues({progress_js}, previousLegByNode, "{race_state}");\n'
        + "console.log(JSON.stringify({ cues, seen: Object.fromEntries(previousLegByNode) }));"
    )
    import json

    return json.loads(_run_node(script))


def test_handoff_cue_fires_when_leg_advances_during_running_race():
    result = _run_handoff_cues(
        '{"n1": {relay_legs: 4, relay_leg: 2, athlete_name: "Volt", relay_current_runner: "Bob"}}',
        '{"n1": 1}',
        "RUNNING",
    )
    assert result["cues"] == [{"team": "Volt", "runner": "Bob"}]
    assert result["seen"] == {"n1": 2}


def test_no_cue_when_leg_is_unchanged():
    result = _run_handoff_cues(
        '{"n1": {relay_legs: 4, relay_leg: 2, athlete_name: "Volt", relay_current_runner: "Bob"}}',
        '{"n1": 2}',
        "RUNNING",
    )
    assert result["cues"] == []


def test_no_cue_for_a_node_never_seen_before():
    # No prior leg recorded for this node_id -- nothing to compare against,
    # so the very first tick of a race must never fire a spurious cue.
    result = _run_handoff_cues(
        '{"n1": {relay_legs: 4, relay_leg: 1, athlete_name: "Volt", relay_current_runner: "Alice"}}',
        "{}",
        "RUNNING",
    )
    assert result["cues"] == []
    assert result["seen"] == {"n1": 1}


def test_no_cue_on_first_render_after_reload_mid_race_but_fires_on_next_advance():
    # A dashboard reload (Game Admin's remote reload, or a plain projector
    # refresh) wipes previousRelayLegByNode, but the race itself is
    # unaffected -- the very first render afterwards can land with a team
    # already several legs in (relay_leg: 2, not 1). That must NOT be
    # mistaken for a fresh handoff (there is nothing to compare against
    # yet), but the NEXT genuine advance after this point must still cue
    # exactly once.
    first = _run_handoff_cues(
        '{"n1": {relay_legs: 4, relay_leg: 2, athlete_name: "Volt", relay_current_runner: "Bob"}}',
        "{}",
        "RUNNING",
    )
    assert first["cues"] == []
    assert first["seen"] == {"n1": 2}

    second = _run_handoff_cues(
        '{"n1": {relay_legs: 4, relay_leg: 3, athlete_name: "Volt", relay_current_runner: "Cara"}}',
        '{"n1": 2}',
        "RUNNING",
    )
    assert second["cues"] == [{"team": "Volt", "runner": "Cara"}]
    assert second["seen"] == {"n1": 3}


def test_no_cue_outside_running_but_leg_is_still_recorded():
    result = _run_handoff_cues(
        '{"n1": {relay_legs: 4, relay_leg: 3, athlete_name: "Volt", relay_current_runner: "Cara"}}',
        '{"n1": 1}',
        "STOPPED",
    )
    assert result["cues"] == []
    assert result["seen"] == {"n1": 3}


def test_non_relay_rows_are_ignored():
    result = _run_handoff_cues(
        '{"n1": {athlete_name: "Runner A"}}',
        "{}",
        "RUNNING",
    )
    assert result["cues"] == []
    assert result["seen"] == {}


# -- Wiring: each renderer calls relayLegLine; renderLeaderboard calls the
#    handoff detector. Source-text on comment-stripped source, mirroring
#    the accepted pattern in test_dashboard_station_machine_name.py for
#    DOM-bound render functions that can't run under plain node.
# -------------------------------------------------------------------------


def test_classic_leaderboard_calls_relay_leg_line():
    source = _strip_js_comments(_read_index())
    fn = _extract_function(source, "renderLeaderboard")
    assert "relayLegLine(node)" in fn


def test_race_track_leaderboard_calls_relay_leg_line():
    source = _strip_js_comments(_read_index())
    fn = _extract_function(source, "renderRaceTrackLeaderboard")
    assert "relayLegLine(node)" in fn


def test_sprint_board_leaderboard_calls_relay_leg_line():
    source = _strip_js_comments(_read_index())
    fn = _extract_function(source, "renderSprintBoardLeaderboard")
    assert "relayLegLine(row.raw)" in fn


def test_render_leaderboard_calls_relay_handoff_detector():
    source = _strip_js_comments(_read_index())
    fn = _extract_function(source, "renderLeaderboard")
    assert "detectRelayHandoffs(progressData)" in fn


def test_detect_relay_handoffs_shows_banner_for_each_cue():
    source = _strip_js_comments(_read_index())
    fn = _extract_function(source, "detectRelayHandoffs")
    assert "computeRelayHandoffCues(" in fn
    assert "showRelayHandoffBanner(" in fn
