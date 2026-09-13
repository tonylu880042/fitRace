"""Regression test for startRaceAction() switching the projector into race
mode in Game Admin (hub_server/static/gameAdmin.html).

Before this change the operator had to separately press "Switch Projector
to Race Mode" before Start Race would produce a meaningful dashboard. Start
Race should do this itself.

Ordering is a correctness constraint, not a preference: CLAUDE.md states
the hub runs one session mode at a time and switching modes is blocked
while a session is RUNNING. So inside startRaceAction() the sequence must
be strictly: save-if-dirty -> switch to race mode (only if not already in
race mode) -> start. Switching after the race has started would be
rejected by the backend.

`switchToRaceMode()` is changed to return `true` on success / `false` in
its `catch` block (mirroring `configureRace()`'s existing contract), and
`startRaceAction()` calls it -- skipping the call entirely when
`state.race.session_mode` already reads "race" -- aborting the start if it
returns false.

Same execute-under-node technique as
test_game_admin_start_autosaves_dirty_config.py: extract the real function
bodies (comments stripped) and run them with stubbed collaborators,
recording call order in a shared log, so a wrong order or a dropped abort
check is exercised rather than pattern-matched.
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
    script = _stripped_script()
    start = script.index("async function startRaceAction")
    end = script.index("async function startRace()", start)
    return script[start:end]


def _extract_switch_to_race_mode() -> str:
    """switchToRaceMode(), from a comment-stripped script, up to (not
    including) the next function (reloadDashboard). Grabbing the real
    function body means a dropped `return true`/`return false` is
    exercised, not just pattern-matched."""
    script = _stripped_script()
    start = script.index("async function switchToRaceMode")
    end = script.index("async function reloadDashboard", start)
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


def test_switch_to_race_mode_is_actually_defined():
    body = _extract_switch_to_race_mode()
    assert "async function switchToRaceMode()" in body


# ---------------------------------------------------------------------------
# switchToRaceMode(): must resolve true on success, false on failure, while
# leaving its existing setMessage behaviour untouched.
# ---------------------------------------------------------------------------


def _run_switch_to_race_mode(fetch_should_throw):
    body = _extract_switch_to_race_mode()
    harness = f"""
const messages = [];
function setMessage(id, text, kind) {{ messages.push({{ id, text, kind }}); }}
function adminHeaders(h) {{ return h || {{}}; }}
function t(key) {{ return key; }}
function renderRace() {{}}
const state = {{ race: null }};
async function fetchJson(url, opts) {{
  if ({"true" if fetch_should_throw else "false"}) throw new Error("boom");
  return {{ state: "READY", session_mode: "race" }};
}}

{body}

(async () => {{
  const result = await switchToRaceMode();
  console.log(JSON.stringify({{ result, messages }}));
}})();
"""
    output = _run_node(harness)
    return json.loads(output.strip().splitlines()[-1])


def test_switch_to_race_mode_returns_true_on_success():
    result = _run_switch_to_race_mode(fetch_should_throw=False)
    assert result["result"] is True
    assert any(m["kind"] == "ok" for m in result["messages"])


def test_switch_to_race_mode_returns_false_on_failure():
    result = _run_switch_to_race_mode(fetch_should_throw=True)
    assert result["result"] is False
    assert any(m["kind"] == "error" for m in result["messages"])


# ---------------------------------------------------------------------------
# startRaceAction(): configureRace -> switchToRaceMode -> startRace, in that
# exact order; a failed switch aborts before startRace; already being in
# race mode skips the switch call entirely.
# ---------------------------------------------------------------------------


def _run_start_race_action(
    dirty,
    session_mode,
    configure_race_return=True,
    switch_to_race_mode_return=True,
):
    body = _extract_start_race_action()
    harness = f"""
const callLog = [];
async function configureRace() {{ callLog.push("configureRace"); return {"true" if configure_race_return else "false"}; }}
async function switchToRaceMode() {{ callLog.push("switchToRaceMode"); return {"true" if switch_to_race_mode_return else "false"}; }}
async function startRace() {{ callLog.push("startRace"); }}
function renderRaceActionButtons() {{}}
const state = {{
  raceActionPending: null,
  countdownActive: false,
  raceConfigDirty: {"true" if dirty else "false"},
  race: {{ state: "READY", session_mode: {json.dumps(session_mode)} }},
}};

{body}

(async () => {{
  await startRaceAction();
  console.log(JSON.stringify({{ callLog }}));
}})();
"""
    output = _run_node(harness)
    return json.loads(output.strip().splitlines()[-1])


def test_order_is_configure_then_switch_then_start():
    result = _run_start_race_action(dirty=True, session_mode="class")
    assert result["callLog"] == ["configureRace", "switchToRaceMode", "startRace"]


def test_failed_switch_aborts_before_start():
    result = _run_start_race_action(
        dirty=False, session_mode="class", switch_to_race_mode_return=False
    )
    assert "switchToRaceMode" in result["callLog"]
    assert "startRace" not in result["callLog"]


def test_already_in_race_mode_skips_the_switch_call():
    result = _run_start_race_action(dirty=False, session_mode="race")
    assert "switchToRaceMode" not in result["callLog"]
    assert result["callLog"] == ["startRace"]
