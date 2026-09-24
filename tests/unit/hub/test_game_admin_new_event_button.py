"""Game Admin (hub_server/static/gameAdmin.html) gains a "Start New Event"
button in the "Live Presentation" block that POSTs to /api/race/new-event
with admin headers, after a window.confirm() the operator can decline, and
updates the visible "since HH:MM" label on success (see
RaceManager.start_new_event / RaceResultsQuery.get_standings).

This executes the REAL, unmodified startNewEvent() function pulled out of
gameAdmin.html's inline <script> via brace-depth matching (same technique
as test_game_admin_qr_visibility.py) under node with minimal DOM/fetch/
confirm stubs -- never a source-text grep -- so deleting the real wiring
turns this red instead of being satisfied by a nearby comment.
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
    elements[id] = { id, textContent: "", disabled: false };
  }
  return elements[id];
}
function $(id) { return el(id); }
function t(key, params) {
  let value = key;
  Object.entries(params || {}).forEach(([name, replacement]) => {
    value = `${value}:${name}=${replacement}`;
  });
  return value;
}
function setMessage(id, text, kind) {
  const node = el(id);
  node.textContent = text || "";
  node.kind = kind;
}
function adminHeaders(extra = {}) { return { ...extra, "X-FitRace-Admin-Token": "secret" }; }
"""


def _harness(confirm_returns, body: str) -> str:
    source = _strip_js_comments(_read())
    fetch_json_fn = _extract_function(source, "fetchJson")
    start_new_event_fn = _extract_function(source, "startNewEvent")
    assert "race/new-event" in start_new_event_fn  # sanity: real source
    assert "window.confirm" in start_new_event_fn
    confirm_js = "true" if confirm_returns else "false"
    return f"""
{_DOM_STUB}
global.window = {{ confirm: () => {confirm_js} }};
let state = {{ race: {{ state: "IDLE", event_start_epoch_ms: null }} }};

{fetch_json_fn}
{start_new_event_fn}

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


def _failing_fetch_script(detail="Race is running"):
    return f"""
const calls = [];
function renderRace() {{}}
global.fetch = async (url, options) => {{
  calls.push({{ url, options }});
  return {{
    ok: false,
    status: 409,
    statusText: "Conflict",
    text: async () => JSON.stringify({{ detail: {json.dumps(detail)} }}),
  }};
}};
"""


def test_start_new_event_posts_with_admin_headers_after_confirm():
    harness = _harness(
        True,
        f"""
{_ok_fetch_script({"event_start_epoch_ms": 123456789})}
(async () => {{
  await startNewEvent();
  console.log(JSON.stringify({{
    calledFetch: calls.length,
    url: calls[0].url,
    method: calls[0].options.method,
    adminToken: calls[0].options.headers["X-FitRace-Admin-Token"],
    eventStart: state.race.event_start_epoch_ms,
  }}));
}})();
""",
    )
    result = json.loads(_run_node(harness))
    assert result["calledFetch"] == 1
    assert result["url"] == "/api/race/new-event"
    assert result["method"] == "POST"
    assert result["adminToken"] == "secret"
    assert result["eventStart"] == 123456789


def test_start_new_event_does_nothing_if_operator_declines_confirm():
    harness = _harness(
        False,
        f"""
{_ok_fetch_script({"event_start_epoch_ms": 999})}
(async () => {{
  await startNewEvent();
  console.log(JSON.stringify({{ calledFetch: calls.length }}));
}})();
""",
    )
    result = json.loads(_run_node(harness))
    assert result["calledFetch"] == 0


def test_start_new_event_shows_error_message_on_409():
    harness = _harness(
        True,
        f"""
{_failing_fetch_script("Race is running")}
(async () => {{
  await startNewEvent();
  console.log(JSON.stringify({{
    messageText: elements["race-message"].textContent,
    messageKind: elements["race-message"].kind,
    eventStart: state.race.event_start_epoch_ms,
  }}));
}})();
""",
    )
    result = json.loads(_run_node(harness))
    assert "Race is running" in result["messageText"]
    assert result["messageKind"] == "error"
    assert result["eventStart"] is None
