"""Regression test for startRaceAction() in Game Admin
(hub_server/static/gameAdmin.html).

Bug: touching any race-config dropdown sets `state.raceConfigDirty = true`,
and `renderRaceActionButtons()` folded that flag straight into Start's
disabled expression:

    startBtn.disabled = raceState === "RUNNING" || state.raceConfigDirty
      || !readinessReady || busy;

So every race start was a three-beat "edit -> Save -> Start": Start stayed
hard-disabled until the operator pressed Save Race first, even though
`configureRace()` already validates and persists the config on its own.

Fix: drop `state.raceConfigDirty` from the disabled expression, and make
`startRaceAction()` call `configureRace()` itself first when the config is
dirty, aborting the start if that save fails (`configureRace()` returns
`false` on a validation failure such as `value <= 0`).

This module extracts the real startRaceAction()/renderRaceActionButtons()
source out of the page's inline <script> (comments stripped) and executes
each under node with stubbed collaborators, per the technique established
in tests/unit/hub/test_game_admin_race_action_buttons.py and
tests/unit/hub/test_system_admin_select_node_next_free_station.py. A test
that only pattern-matches the source text cannot catch a dropped
"abort on failed save" check or a wrong call order -- it has to actually
run the extracted code and inspect the resulting call log.
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


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _stripped_script() -> str:
    source = _read()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _extract_start_race_action() -> str:
    """startRaceAction(), from a comment-stripped script, up to (not
    including) the next function (startRace). Grabbing the real function
    body means a dropped abort-on-failed-save check or a reordered call is
    exercised, not just pattern-matched."""
    script = _stripped_script()
    start = script.index("async function startRaceAction")
    end = script.index("async function startRace()", start)
    return script[start:end]


def _extract_render_race_action_buttons() -> str:
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


def test_start_race_action_is_actually_defined():
    body = _extract_start_race_action()
    assert "async function startRaceAction()" in body


# ---------------------------------------------------------------------------
# startRaceAction(): drive the extracted function with stubbed
# configureRace/switchToRaceMode/startRace, recording call order in a
# shared log. switchToRaceMode is stubbed (defaulting to success, already in
# "race" mode) even though this commit's startRaceAction body does not call
# it yet, so this harness keeps working unchanged once a later commit wires
# that call in too.
# ---------------------------------------------------------------------------


def _run_start_race_action(dirty, configure_race_return=True, race_state="READY"):
    body = _extract_start_race_action()
    harness = f"""
const callLog = [];
let configureRaceReturn = {"true" if configure_race_return else "false"};
async function configureRace() {{ callLog.push("configureRace"); return configureRaceReturn; }}
async function switchToRaceMode() {{ callLog.push("switchToRaceMode"); return true; }}
async function startRace() {{ callLog.push("startRace"); }}
function renderRaceActionButtons() {{}}
const state = {{
  raceActionPending: null,
  countdownActive: false,
  raceConfigDirty: {"true" if dirty else "false"},
  race: {{ state: "{race_state}", session_mode: "race" }},
}};

{body}

(async () => {{
  await startRaceAction();
  console.log(JSON.stringify({{ callLog, pending: state.raceActionPending }}));
}})();
"""
    output = _run_node(harness)
    return json.loads(output.strip().splitlines()[-1])


def test_dirty_config_saves_before_starting():
    result = _run_start_race_action(dirty=True, configure_race_return=True)
    assert result["callLog"].count("configureRace") == 1
    assert "startRace" in result["callLog"]
    assert result["callLog"].index("configureRace") < result["callLog"].index(
        "startRace"
    )


def test_failed_save_aborts_the_start():
    result = _run_start_race_action(dirty=True, configure_race_return=False)
    assert "configureRace" in result["callLog"]
    assert "startRace" not in result["callLog"]
    # The pending flag must still be cleared in the `finally` block even on
    # an aborted start, or Start would stay stuck showing "processing".
    assert result["pending"] is None


def test_clean_config_starts_directly_without_saving():
    result = _run_start_race_action(dirty=False, configure_race_return=True)
    assert "configureRace" not in result["callLog"]
    assert result["callLog"].count("startRace") == 1


# ---------------------------------------------------------------------------
# renderRaceActionButtons(): a dirty-but-ready config must leave Start
# enabled now that the obligation to press Save first is gone.
# ---------------------------------------------------------------------------


def test_dirty_but_ready_leaves_start_enabled():
    body = _extract_render_race_action_buttons()
    harness = f"""
function makeEl() {{
  return {{
    disabled: false,
    textContent: "",
    className: "",
    classList: {{ toggle: function () {{}} }},
  }};
}}
const mockElements = {{}};
function $(id) {{
  if (!mockElements[id]) mockElements[id] = makeEl();
  return mockElements[id];
}}
function t(key) {{ return key; }}
const state = {{
  race: {{ state: "READY" }},
  readiness: {{ ready: true }},
  raceConfigDirty: true,
  countdownActive: false,
  raceActionPending: null,
}};

{body}

renderRaceActionButtons();
console.log(JSON.stringify({{ start_disabled: mockElements["btn-start-race"].disabled }}));
"""
    output = _run_node(harness)
    result = json.loads(output.strip().splitlines()[-1])
    assert result["start_disabled"] is False
