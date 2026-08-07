"""Regression test for the Stop / Reset button disabled logic in
renderRace() (hub_server/static/gameAdmin.html, around lines 1662-1663):

    $("btn-stop").disabled  = raceState !== "RUNNING";
    $("btn-reset").disabled = raceState === "RUNNING";

These are two independent predicates that CLAUDE.md flags as exactly the
kind of pair that must stay exact complements: in every race state, exactly
ONE of Stop / Reset must be clickable. `btn-reset` triggers resetRace(),
which deletes the venue's current race configuration -- so if a copy-paste
slip ever made both predicates read the same way (e.g. both keyed off
`raceState !== "RUNNING"`), the failure mode is silent and severe: during a
RUNNING race BOTH buttons render disabled, with no way to stop a live race
from this panel at all.

Today the only assertions touching these two buttons check presence and DOM
order (tests/unit/hub/test_game_admin_action_bar.py,
test_game_admin_race_control_restructure.py), e.g.
`assert 'id="btn-stop"' in panel`. None of them execute the disabled logic,
so a predicate mutation would leave the whole suite green.

This module extracts the real renderRace() source out of the page's inline
<script> (comments stripped) and executes it under node with the DOM/helper
stubs it calls, per the technique already established in
tests/unit/hub/test_game_admin_race_action_buttons.py and
tests/unit/hub/test_game_admin_readiness_state_mapping.py. A test that only
pattern-matches the source text (e.g. `assert "raceState !== " in line`)
cannot catch a wrong boolean combination -- it has to actually run.

renderRace() also calls normalizeLeaderboardDisplayMode(),
renderRaceActionButtons(), updateControlGuidance() and
renderReadinessPanel() unconditionally, plus metricNumber() /
syncRaceFields() / syncCompetitionFields() when `race.config` is set and
the form isn't dirty. Stubbing every one of those as a no-op would work,
but the config-dependent calls are avoidable entirely: `state.race` below
carries no `config` key, so that branch's condition
(`config && !state.raceConfigDirty`) is false and those three helpers are
never reached -- only normalizeLeaderboardDisplayMode,
renderRaceActionButtons, updateControlGuidance and renderReadinessPanel
need stubs. Each stub is a real, callable no-op (not a name left undefined)
so renderRace() runs to completion and actually reaches the two lines under
test, rather than throwing before it gets there.
"""

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


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _stripped_script() -> str:
    source = _read()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _extract_render_race() -> str:
    """renderRace(), from a comment-stripped script, up to (not including)
    the next function (renderRaceActionButtons). Grabbing the real function
    body means a mutation of either disabled-assignment is exercised, not
    just pattern-matched."""
    script = _stripped_script()
    start = script.index("function renderRace()")
    end = script.index("function renderRaceActionButtons", start)
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


def test_render_race_is_actually_defined():
    body = _extract_render_race()
    assert "function renderRace()" in body


_DOM_HARNESS_PREFIX = """
function makeEl() {
  return {
    disabled: false,
    textContent: "",
    className: "",
    classList: { toggle: function () {} },
  };
}
const mockElements = {};
function $(id) {
  if (!mockElements[id]) mockElements[id] = makeEl();
  return mockElements[id];
}
function t(key) { return key; }
function normalizeLeaderboardDisplayMode(v) { return v; }
function renderRaceActionButtons() {}
function updateControlGuidance() {}
function renderReadinessPanel() {}
const state = {};
"""


def _render(race_state):
    body = _extract_render_race()
    harness = f"""
{_DOM_HARNESS_PREFIX}
{body}

state.race = {{ state: "{race_state}" }};
state.raceConfigDirty = false;
state.countdownActive = false;

renderRace();
console.log(JSON.stringify({{
  stop_disabled: mockElements["btn-stop"].disabled,
  reset_disabled: mockElements["btn-reset"].disabled,
}}));
"""
    output = _run_node(harness)
    import json

    return json.loads(output.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# Proof the harness genuinely reaches the two target lines: the raw results
# for each state, asserted individually so a future reader can see they are
# not all identical (which they would be if renderRace() had thrown before
# reaching lines 1662-1663 and a bug in the harness papered over it).
# ---------------------------------------------------------------------------


def test_idle_state_stop_disabled_reset_enabled():
    result = _render("IDLE")
    assert result == {"stop_disabled": True, "reset_disabled": False}


def test_ready_state_stop_disabled_reset_enabled():
    result = _render("READY")
    assert result == {"stop_disabled": True, "reset_disabled": False}


def test_running_state_stop_enabled_reset_disabled():
    result = _render("RUNNING")
    assert result == {"stop_disabled": False, "reset_disabled": True}


def test_stopped_state_stop_disabled_reset_enabled():
    result = _render("STOPPED")
    assert result == {"stop_disabled": True, "reset_disabled": False}


# ---------------------------------------------------------------------------
# The core behavioural contract, as ONE partition assertion per CLAUDE.md's
# "prefer one partition pass" guidance: for every race state, exactly one
# of Stop / Reset is enabled. `stop_disabled != reset_disabled` is true iff
# exactly one of the two is disabled (and therefore exactly one enabled) --
# it catches BOTH failure directions (both disabled during a live race;
# both enabled outside one) in a single check, so the two predicates cannot
# quietly drift into agreement with each other.
#
# Also pins the specific correct mapping (not just "some" partition): Stop
# is the enabled one if and only if the race is RUNNING.
# ---------------------------------------------------------------------------


def test_stop_and_reset_are_exact_complements_across_race_states():
    for race_state in ("IDLE", "READY", "RUNNING", "STOPPED"):
        result = _render(race_state)

        assert result["stop_disabled"] != result["reset_disabled"], (
            f"race_state={race_state!r}: stop_disabled={result['stop_disabled']!r} "
            f"and reset_disabled={result['reset_disabled']!r} must be exact "
            "complements -- exactly one of Stop/Reset must be clickable. "
            "Both disabled means a RUNNING race can't be stopped from this "
            "panel; both enabled means Reset (which deletes the venue's "
            "race configuration) is reachable mid-race."
        )

        stop_enabled = not result["stop_disabled"]
        expected_stop_enabled = race_state == "RUNNING"
        assert stop_enabled == expected_stop_enabled, (
            f"race_state={race_state!r}: Stop must be enabled if and only "
            f"if the race is RUNNING, got stop_enabled={stop_enabled!r}."
        )
