"""Regression test for renderRaceActionButtons() in Game Admin
(hub_server/static/gameAdmin.html).

Bug: after "Reset Race" (or on first page load, before any field has been
touched), BOTH the Save and Start buttons render disabled with no
discoverable way out:

    saveBtn.disabled  = !state.raceConfigDirty || raceState === "RUNNING" || busy;
    startBtn.disabled = raceState === "RUNNING" || state.raceConfigDirty || !readinessReady || busy;

`resetRace()` clears server-side config (readiness.ready becomes false,
with the check message "Save race settings before starting.") but does NOT
set `state.raceConfigDirty = true`. So with raceState IDLE, ready false,
dirty false: saveBtn.disabled is true (not dirty) AND startBtn.disabled is
true (not ready) -- the readiness panel tells the operator to save, but
Save is greyed out. Dead end.

This module extracts the real renderRaceActionButtons() source out of the
page's inline <script> (comments stripped) and executes it under node with
a stubbed $()/t()/state, per the technique already established in
tests/unit/hub/test_game_admin_readiness_state_mapping.py. A test that only
pattern-matches the source text (e.g. `assert "!state.raceConfigDirty" in
line`) cannot catch a wrong boolean combination -- it has to actually run.
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


def _extract_render_race_action_buttons() -> str:
    """renderRaceActionButtons(), from a comment-stripped script, up to (not
    including) the next function (readinessStatusClass). Grabbing the real
    function body means a mutation of either boolean expression is
    exercised, not just pattern-matched."""
    script = _stripped_script()
    start = script.index("function renderRaceActionButtons")
    end = script.index("function readinessStatusClass", start)
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


def test_render_race_action_buttons_is_actually_defined():
    body = _extract_render_race_action_buttons()
    assert "function renderRaceActionButtons()" in body


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
const state = {};
"""


def _render(race_state, ready, dirty, countdown_active=False, action_pending=None):
    body = _extract_render_race_action_buttons()
    pending = "null" if action_pending is None else f'"{action_pending}"'
    harness = f"""
{_DOM_HARNESS_PREFIX}
{body}

state.race = {{ state: "{race_state}" }};
state.readiness = {{ ready: {"true" if ready else "false"} }};
state.raceConfigDirty = {"true" if dirty else "false"};
state.countdownActive = {"true" if countdown_active else "false"};
state.raceActionPending = {pending};

renderRaceActionButtons();
console.log(JSON.stringify({{
  save_disabled: mockElements["btn-save-race"].disabled,
  start_disabled: mockElements["btn-start-race"].disabled,
}}));
"""
    output = _run_node(harness)
    import json

    return json.loads(output.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# 1. Just after Reset / first load: the bug. Must FAIL before the fix.
# ---------------------------------------------------------------------------


def test_after_reset_or_first_load_save_is_enabled_not_start():
    result = _render(race_state="IDLE", ready=False, dirty=False)
    assert result["save_disabled"] is False, (
        "Save must be clickable right after Reset Race / on first load, "
        "otherwise the operator has no way to configure and start a race."
    )
    assert result["start_disabled"] is True


# ---------------------------------------------------------------------------
# 2. Configured and ready, nothing edited: Start is the live action.
# ---------------------------------------------------------------------------


def test_configured_and_ready_start_is_enabled_not_save():
    result = _render(race_state="READY", ready=True, dirty=False)
    assert result["save_disabled"] is True
    assert result["start_disabled"] is False


# ---------------------------------------------------------------------------
# 3. Configured, ready, but edited since: must save before starting again.
# ---------------------------------------------------------------------------


def test_ready_but_dirty_save_is_enabled_start_is_blocked():
    result = _render(race_state="READY", ready=True, dirty=True)
    assert result["save_disabled"] is False, "Unsaved edits must be savable."
    assert result["start_disabled"] is True, "Must not start with unsaved changes."


# ---------------------------------------------------------------------------
# 4. Race running: no button should let the operator touch config or start.
# ---------------------------------------------------------------------------


def test_running_race_both_buttons_disabled():
    result = _render(race_state="RUNNING", ready=True, dirty=False)
    assert result["save_disabled"] is True
    assert result["start_disabled"] is True


def test_running_race_both_buttons_disabled_even_if_dirty():
    result = _render(race_state="RUNNING", ready=False, dirty=True)
    assert result["save_disabled"] is True
    assert result["start_disabled"] is True


# ---------------------------------------------------------------------------
# 5. Busy / countdown active: both buttons must be disabled while an action
#    is genuinely in flight.
# ---------------------------------------------------------------------------


def test_countdown_active_both_buttons_disabled():
    result = _render(race_state="READY", ready=True, dirty=False, countdown_active=True)
    assert result["save_disabled"] is True
    assert result["start_disabled"] is True


def test_action_pending_both_buttons_disabled():
    result = _render(race_state="IDLE", ready=False, dirty=False, action_pending="save")
    assert result["save_disabled"] is True
    assert result["start_disabled"] is True


# ---------------------------------------------------------------------------
# 6. The actual user-facing contract: whenever the race is neither RUNNING
#    nor busy AND no config has been validated yet (IDLE / STOPPED --
#    exactly the states Reset Race and first load land in), Save must
#    always be reachable regardless of dirty/ready, so the two buttons can
#    never both be disabled. This is the precise axis the reported bug
#    lives on and is guaranteed unconditionally by `needsSave = dirty ||
#    raceState !== "READY"`.
#
# NOTE: READY + readiness.ready == false + dirty == false (both disabled)
# is deliberately excluded here. It is a real, common state (race_manager
# .configure() moves state to READY unconditionally on save, before
# stations/athletes are set up) but it predates this bug: the superseded
# single-button design (commit 3a2ec45) computed
# `button.disabled = ... || (!shouldSave && !readinessReady)`, which is
# also true there. It isn't the contradiction this fix targets either --
# in that state the readiness panel points at Station Assignment /
# registration, not at Save, so there is no disabled button being pointed
# to. Fixing it would require changing startBtn's gating, which the spec
# explicitly says to leave alone.
# ---------------------------------------------------------------------------


def test_never_both_disabled_when_config_not_yet_validated():
    combos = [
        (race_state, ready, dirty)
        for race_state in ("IDLE", "STOPPED")
        for ready in (True, False)
        for dirty in (True, False)
    ]
    for race_state, ready, dirty in combos:
        result = _render(race_state=race_state, ready=ready, dirty=dirty)
        assert not (result["save_disabled"] and result["start_disabled"]), (
            f"Both buttons disabled with race_state={race_state!r}, "
            f"ready={ready!r}, dirty={dirty!r} -- operator has no way "
            "forward."
        )
        assert result["save_disabled"] is False, (
            f"Save must always be reachable in race_state={race_state!r} "
            "(config not yet validated as READY), regardless of dirty/"
            "ready."
        )
