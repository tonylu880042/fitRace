"""Game Admin (hub_server/static/gameAdmin.html) gains a select next to
signup-qr-visible/admin-qr-visible: "Show Live Data While Idle"
(idle-live-telemetry-visible), POSTing to
/api/dashboard/idle-telemetry-visibility with admin headers and rolling
back to its previous value on failure -- mirroring exactly how
setSignupQrVisible()/setAdminQrVisible() already work (see
test_game_admin_qr_visibility.py, same extraction technique).

This executes the REAL, unmodified setIdleLiveTelemetryVisible() pulled out
of gameAdmin.html's inline <script> via brace-depth matching under node
with minimal DOM/fetch stubs -- never a source-text grep -- so deleting the
real wiring turns this red instead of being satisfied by a nearby comment.
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
    fn = _extract_function(source, "setIdleLiveTelemetryVisible")
    assert "dashboard/idle-telemetry-visibility" in fn  # sanity: real source
    assert "visible" in fn
    return f"""
{_DOM_STUB}
let state = {{ race: {{ state: "IDLE", idle_live_telemetry_visible: true }} }};

{fetch_json_fn}
{fn}

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


def test_idle_live_telemetry_visible_posts_correct_body_with_admin_headers():
    harness = _harness(f"""
{_ok_fetch_script({"state": "IDLE", "idle_live_telemetry_visible": False})}
(async () => {{
  await setIdleLiveTelemetryVisible(false);
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
    assert result["url"] == "/api/dashboard/idle-telemetry-visibility"
    assert result["method"] == "POST"
    assert result["body"] == {"visible": False}
    assert result["adminToken"] == "secret"
    assert result["contentType"] == "application/json"


def test_idle_live_telemetry_visible_updates_state_on_success():
    harness = _harness(f"""
{_ok_fetch_script({"state": "IDLE", "idle_live_telemetry_visible": False})}
(async () => {{
  await setIdleLiveTelemetryVisible(false);
  console.log(JSON.stringify({{ raceState: state.race }}));
}})();
""")
    result = json.loads(_run_node(harness))
    assert result["raceState"]["idle_live_telemetry_visible"] is False


def test_idle_live_telemetry_visible_rolls_back_select_on_failure():
    # A real <select onchange> has already flipped its own DOM value to the
    # new choice before the handler runs -- starting from that value is what
    # makes a missing rollback assignment turn this red.
    harness = _harness(f"""
{_failing_fetch_script()}
el("idle-live-telemetry-visible").value = "false";
(async () => {{
  await setIdleLiveTelemetryVisible(false);
  console.log(JSON.stringify({{
    selectValue: el("idle-live-telemetry-visible").value,
    message: el("race-message").textContent,
    messageClass: el("race-message").className,
  }}));
}})();
""")
    result = json.loads(_run_node(harness))
    assert result["selectValue"] == "true"
    assert result["message"] == "boom"
    assert "error" in result["messageClass"]


def test_idle_live_telemetry_visible_rolls_back_to_false_when_that_was_current():
    harness = _harness(f"""
{_failing_fetch_script()}
state.race.idle_live_telemetry_visible = false;
el("idle-live-telemetry-visible").value = "true";
(async () => {{
  await setIdleLiveTelemetryVisible(true);
  console.log(JSON.stringify({{ selectValue: el("idle-live-telemetry-visible").value }}));
}})();
""")
    result = json.loads(_run_node(harness))
    assert result["selectValue"] == "false"
