"""Regression test for a select silently going stale after Game Admin is
reopened (or simply refreshed) while a dashboard QR is hidden.

renderRace() (hub_server/static/gameAdmin.html, ~line 2399) is the single
function that syncs every Game Admin control from the race state the page
already holds -- it runs after every fetch/refresh (refreshState(),
refreshAll(), and on success inside setStartCountdownSound() /
setSignupQrVisible() / setAdminQrVisible()). Two of its lines sync the
QR-visibility selects:

    $("signup-qr-visible").value = race.signup_qr_visible === false ? "false" : "true";
    $("admin-qr-visible").value = race.admin_qr_visible === false ? "false" : "true";

If either line were replaced by a hardcoded "true" -- a real, valid-JS
mutation, not a crash -- the operator's browser would show 顯示/Visible for
a QR the dashboard is actually hiding, right after reopening or refreshing
Game Admin, with no error at all. Every OTHER QR test in this suite (the
POST body, the admin-header check, the rollback-on-failure behaviour of
setSignupQrVisible()/setAdminQrVisible()) stays green under that mutation,
because none of them exercise renderRace() itself -- only the change
handlers that run when the OPERATOR flips the select, never the sync that
runs when the PAGE loads/refreshes with a state that already disagrees
with the select.

This executes the REAL, unmodified renderRace() pulled out of gameAdmin.
html's inline <script> via brace-depth matching (the same technique as
test_game_admin_clear_results.py / test_game_admin_qr_visibility.py) under
node. Its side-effecting callees (renderRaceActionButtons,
updateControlGuidance, renderReadinessPanel, syncSessionModeControl,
renderRoster, normalizeLeaderboardDisplayMode) are stubbed as no-ops --
this test cares only about the two select-sync lines it contains, not the
rest of the page's rendering -- never a source-text grep, so deleting or
hardcoding the real sync line turns this red instead of being satisfied by
a nearby comment.
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


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


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


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


_DOM_STUB = """
const elements = {};
function makeClassList(el) {
  return {
    add() {},
    remove() {},
    toggle() {},
    contains() { return false; },
  };
}
function el(id) {
  if (!elements[id]) {
    const element = {
      id,
      disabled: false,
      hidden: false,
      textContent: "",
      innerHTML: "",
      value: "",
      className: "",
      dataset: {},
    };
    element.classList = makeClassList(element);
    elements[id] = element;
  }
  return elements[id];
}
function $(id) { return el(id); }
function t(key) { return key; }

// renderRace() calls these unconditionally, but this test only cares about
// the two QR-select sync lines it contains -- stubbed as no-ops so the
// REAL renderRace() can run standalone without pulling in the rest of the
// page's rendering (readiness panel, roster tables, action buttons, ...).
function normalizeLeaderboardDisplayMode(mode) {
  return ["classic", "race_track", "team_battle", "sprint_board"].includes(mode) ? mode : "classic";
}
function renderRaceActionButtons() {}
function updateControlGuidance() {}
function renderReadinessPanel() {}
function syncSessionModeControl() {}
function renderRoster() {}
"""


def _extract_render_race() -> str:
    source = _strip_js_comments(_read())
    fn = _extract_function(source, "renderRace")
    # Sanity: real source, not a stub -- both QR-select sync lines present.
    assert 'signup-qr-visible").value' in fn
    assert 'admin-qr-visible").value' in fn
    assert "signup_qr_visible" in fn
    assert "admin_qr_visible" in fn
    return fn


def _sync(race_js: str) -> dict:
    fn = _extract_render_race()
    script = f"""
{_DOM_STUB}
let state = {{ race: {race_js} }};

{fn}

renderRace();
console.log(JSON.stringify({{
  signupSelect: el("signup-qr-visible").value,
  adminSelect: el("admin-qr-visible").value,
}}));
"""
    return json.loads(_run_node(script))


def test_signup_hidden_admin_visible_syncs_both_selects():
    result = _sync(
        '{ state: "IDLE", signup_qr_visible: false, admin_qr_visible: true }'
    )
    assert result["signupSelect"] == "false"
    assert result["adminSelect"] == "true"


def test_signup_visible_admin_hidden_syncs_both_selects():
    result = _sync(
        '{ state: "IDLE", signup_qr_visible: true, admin_qr_visible: false }'
    )
    assert result["signupSelect"] == "true"
    assert result["adminSelect"] == "false"


def test_both_hidden_syncs_both_selects_to_false():
    result = _sync(
        '{ state: "IDLE", signup_qr_visible: false, admin_qr_visible: false }'
    )
    assert result["signupSelect"] == "false"
    assert result["adminSelect"] == "false"


def test_missing_fields_default_both_selects_to_true():
    result = _sync('{ state: "IDLE" }')
    assert result["signupSelect"] == "true"
    assert result["adminSelect"] == "true"
