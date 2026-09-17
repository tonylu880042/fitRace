"""Game Admin (hub_server/static/gameAdmin.html) gains two selects in the
"Live Presentation" block, next to the Start Sound select: one to show/hide
the dashboard's self sign-up QR code, one for the Game Admin control-page
QR code. Both POST to /api/dashboard/qr-visibility with admin headers and,
on failure, roll the select back to its previous value -- mirroring exactly
how setStartCountdownSound() already handles start-sound-enabled.

This executes the REAL, unmodified setSignupQrVisible()/setAdminQrVisible()
functions pulled out of gameAdmin.html's inline <script> via brace-depth
matching (the same technique as test_game_admin_clear_results.py) under
node with minimal DOM/fetch stubs -- never a source-text grep -- so
deleting the real wiring turns this red instead of being satisfied by a
nearby comment.
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


def _matching_paren_end(source: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(source):
        char = source[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching closing paren found")


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    async_start = source.rfind("async ", 0, start)
    if async_start != -1 and source[async_start:start] == "async ":
        start = async_start
    paren_open = source.index("(", start)
    paren_end = _matching_paren_end(source, paren_open)
    brace_open = source.index("{", paren_end)
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
function el(id) {
  if (!elements[id]) {
    elements[id] = { id, value: "true", textContent: "", className: "" };
  }
  return elements[id];
}
function $(id) { return el(id); }
function t(key) { return key; }
function setMessage(id, text, kind) {
  const node = el(id);
  node.textContent = text || "";
  node.className = `status-text ${kind || ""}`;
}
function adminHeaders(extra = {}) { return { ...extra, "X-FitRace-Admin-Token": "secret" }; }
"""


def _harness(body: str) -> str:
    source = _strip_js_comments(_read())
    fetch_json_fn = _extract_function(source, "fetchJson")
    signup_fn = _extract_function(source, "setSignupQrVisible")
    admin_fn = _extract_function(source, "setAdminQrVisible")
    assert "dashboard/qr-visibility" in signup_fn  # sanity: real source
    assert "dashboard/qr-visibility" in admin_fn
    assert "signup_qr_visible" in signup_fn
    assert "admin_qr_visible" in admin_fn
    return f"""
{_DOM_STUB}
let state = {{ race: {{ state: "IDLE", signup_qr_visible: true, admin_qr_visible: true }} }};

{fetch_json_fn}
{signup_fn}
{admin_fn}

{body}
"""


def _ok_fetch_script(response_body):
    return f"""
const calls = [];
function renderRace() {{}}
global.fetch = async (url, options) => {{
  calls.push({{ url, options }});
  return {{
    ok: true,
    status: 200,
    statusText: "OK",
    text: async () => JSON.stringify({json.dumps(response_body)}),
  }};
}};
"""


def _failing_fetch_script():
    return """
const calls = [];
function renderRace() {}
global.fetch = async (url, options) => {
  calls.push({ url, options });
  return {
    ok: false,
    status: 500,
    statusText: "Internal Server Error",
    text: async () => JSON.stringify({ detail: "boom" }),
  };
};
"""


def test_signup_qr_visible_posts_correct_body_with_admin_headers():
    harness = _harness(f"""
{_ok_fetch_script({"state": "IDLE", "signup_qr_visible": False, "admin_qr_visible": True})}
(async () => {{
  await setSignupQrVisible(false);
  console.log(JSON.stringify({{
    url: calls[0].url,
    method: calls[0].options.method,
    body: JSON.parse(calls[0].options.body),
    adminToken: calls[0].options.headers["X-FitRace-Admin-Token"],
    contentType: calls[0].options.headers["Content-Type"],
  }}));
}})();
""")
    result = json.loads(_run_node(harness))
    assert result["url"] == "/api/dashboard/qr-visibility"
    assert result["method"] == "POST"
    assert result["body"] == {"signup_qr_visible": False}
    assert result["adminToken"] == "secret"
    assert result["contentType"] == "application/json"


def test_admin_qr_visible_posts_correct_body_with_admin_headers():
    harness = _harness(f"""
{_ok_fetch_script({"state": "IDLE", "signup_qr_visible": True, "admin_qr_visible": False})}
(async () => {{
  await setAdminQrVisible(false);
  console.log(JSON.stringify({{
    url: calls[0].url,
    method: calls[0].options.method,
    body: JSON.parse(calls[0].options.body),
    adminToken: calls[0].options.headers["X-FitRace-Admin-Token"],
  }}));
}})();
""")
    result = json.loads(_run_node(harness))
    assert result["url"] == "/api/dashboard/qr-visibility"
    assert result["method"] == "POST"
    assert result["body"] == {"admin_qr_visible": False}
    assert result["adminToken"] == "secret"


def test_signup_qr_visible_updates_state_on_success():
    harness = _harness(f"""
{_ok_fetch_script({"state": "IDLE", "signup_qr_visible": False, "admin_qr_visible": True})}
(async () => {{
  await setSignupQrVisible(false);
  console.log(JSON.stringify({{ raceState: state.race }}));
}})();
""")
    result = json.loads(_run_node(harness))
    assert result["raceState"]["signup_qr_visible"] is False


def test_signup_qr_visible_rolls_back_select_on_failure():
    # A real <select onchange> has already flipped its own DOM value to the
    # new choice (browser behaviour) *before* the handler runs -- so the
    # select must start this test already at "false" (matching the visible
    # arg passed below). If the assertion below is satisfied merely because
    # the select was left at its starting value, the rollback code was never
    # actually exercised; starting from the *new* value is what makes a
    # missing `$("signup-qr-visible").value = previousValue` turn this red.
    harness = _harness(f"""
{_failing_fetch_script()}
el("signup-qr-visible").value = "false";
(async () => {{
  await setSignupQrVisible(false);
  console.log(JSON.stringify({{
    selectValue: el("signup-qr-visible").value,
    message: el("race-message").textContent,
    messageClass: el("race-message").className,
  }}));
}})();
""")
    result = json.loads(_run_node(harness))
    assert result["selectValue"] == "true"
    assert result["message"] == "boom"
    assert "error" in result["messageClass"]


def test_admin_qr_visible_rolls_back_select_on_failure():
    # Same DOM-already-changed setup as the signup test above -- the select
    # starts at the new ("false") value the browser would have already
    # applied, so the assertion can only pass if the handler explicitly
    # writes previousValue back.
    harness = _harness(f"""
{_failing_fetch_script()}
el("admin-qr-visible").value = "false";
(async () => {{
  await setAdminQrVisible(false);
  console.log(JSON.stringify({{
    selectValue: el("admin-qr-visible").value,
    message: el("race-message").textContent,
  }}));
}})();
""")
    result = json.loads(_run_node(harness))
    assert result["selectValue"] == "true"
    assert result["message"] == "boom"


def test_signup_qr_visible_rolls_back_to_false_when_that_was_current():
    # Previous state was False (not the True default used by the other
    # rollback test), and the select already shows the new choice ("true")
    # as a real onchange would leave it -- rollback must restore "false",
    # proving previousValue is read from state.race rather than hardcoded.
    harness = _harness(f"""
{_failing_fetch_script()}
state.race.signup_qr_visible = false;
el("signup-qr-visible").value = "true";
(async () => {{
  await setSignupQrVisible(true);
  console.log(JSON.stringify({{ selectValue: el("signup-qr-visible").value }}));
}})();
""")
    result = json.loads(_run_node(harness))
    assert result["selectValue"] == "false"
