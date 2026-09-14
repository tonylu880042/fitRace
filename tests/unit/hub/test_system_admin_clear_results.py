"""System Admin gains a "Clear Race Results" maintenance panel
(hub_server/static/systemAdmin.html): a password-gated, confirm-guarded
button that POSTs to /api/results/clear.

This executes the REAL, unmodified `clearRaceResults` function pulled out
of systemAdmin.html's inline <script> via brace-depth matching (the same
extraction technique as test_game_admin_roster_panel.py) under `node` with
minimal DOM/state stubs -- never a source-text grep -- so deleting the real
wiring turns this red instead of being satisfied by a nearby comment.
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
    return (STATIC_DIR / "systemAdmin.html").read_text(encoding="utf-8")


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
    elements[id] = {
      id,
      disabled: false,
      textContent: "",
      innerHTML: "",
      value: "",
      className: "",
      classList: { toggle() {}, contains() { return false; } },
    };
  }
  return elements[id];
}
function $(id) { return el(id); }
function escapeHtml(value) { return String(value ?? ""); }
function t(key, params = {}) {
  const paramText = Object.keys(params).length ? ` ${JSON.stringify(params)}` : "";
  return `${key}${paramText}`;
}
function setMessage(id, text, kind = "") {
  const node = $(id);
  node.textContent = text || "";
  node.className = `status-text ${kind}`;
}
"""


def _harness(body: str) -> str:
    source = _strip_js_comments(_read())
    clear_fn = _extract_function(source, "clearRaceResults")
    assert "results/clear" in clear_fn  # sanity: real source, not a stub
    return f"""
{_DOM_STUB}

{clear_fn}

{body}
"""


def test_clear_results_posts_password_in_body_not_url():
    el_setup = """
global.window = { confirm: () => true };
elFor("clear-results-password").value = "hunter2 密碼";

let capturedUrl = null;
let capturedOptions = null;
global.fetch = async (url, options) => {
  capturedUrl = url;
  capturedOptions = options;
  return { ok: true, status: 200, json: async () => ({ cleared_count: 3, backup_path: "/data/race_results.jsonl.bak-20260914120000" }) };
};

(async () => {
  await clearRaceResults();
  console.log(JSON.stringify({
    url: capturedUrl,
    body: JSON.parse(capturedOptions.body),
    urlHasPassword: capturedUrl.includes("hunter2"),
  }));
})();
"""
    harness = _harness("function elFor(id) { return el(id); }\n" + el_setup)
    output = _run_node(harness)
    result = json.loads(output)
    assert result["url"] == "/api/results/clear"
    assert result["body"] == {"password": "hunter2 密碼"}
    assert result["urlHasPassword"] is False


def test_clear_results_field_cleared_after_success():
    harness = _harness("""
global.window = { confirm: () => true };
el("clear-results-password").value = "hunter2";
global.fetch = async () => ({ ok: true, status: 200, json: async () => ({ cleared_count: 1, backup_path: "/data/race_results.jsonl.bak-20260914120000" }) });

(async () => {
  await clearRaceResults();
  console.log(JSON.stringify({ fieldValue: el("clear-results-password").value }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["fieldValue"] == ""


def test_clear_results_field_cleared_after_failure():
    harness = _harness("""
global.window = { confirm: () => true };
el("clear-results-password").value = "wrong-one";
global.fetch = async () => ({ ok: false, status: 401, json: async () => ({ detail: "Invalid password" }) });

(async () => {
  await clearRaceResults();
  console.log(JSON.stringify({ fieldValue: el("clear-results-password").value }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["fieldValue"] == ""


def test_clear_results_declined_confirm_sends_nothing():
    harness = _harness("""
let confirmCalled = false;
global.window = { confirm: () => { confirmCalled = true; return false; } };
el("clear-results-password").value = "hunter2";
let fetchCalled = false;
global.fetch = async () => { fetchCalled = true; return { ok: true, status: 200, json: async () => ({}) }; };

(async () => {
  await clearRaceResults();
  console.log(JSON.stringify({
    confirmCalled,
    fetchCalled,
    fieldValue: el("clear-results-password").value,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["confirmCalled"] is True
    assert result["fetchCalled"] is False
    assert result["fieldValue"] == "hunter2"  # untouched -- request never sent


def test_clear_results_button_disabled_while_request_in_flight():
    harness = _harness("""
global.window = { confirm: () => true };
el("clear-results-password").value = "hunter2";
let disabledDuringFetch = null;
global.fetch = async () => {
  disabledDuringFetch = el("btn-clear-results").disabled;
  return { ok: true, status: 200, json: async () => ({ cleared_count: 0, backup_path: null }) };
};

(async () => {
  await clearRaceResults();
  console.log(JSON.stringify({
    disabledDuringFetch,
    disabledAfter: el("btn-clear-results").disabled,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["disabledDuringFetch"] is True
    assert result["disabledAfter"] is False


def test_clear_results_success_message_includes_count_and_backup_filename():
    harness = _harness("""
global.window = { confirm: () => true };
el("clear-results-password").value = "hunter2";
global.fetch = async () => ({ ok: true, status: 200, json: async () => ({ cleared_count: 5, backup_path: "/data/race_results.jsonl.bak-20260914120000" }) });

(async () => {
  await clearRaceResults();
  console.log(JSON.stringify({ message: el("clear-results-message").textContent }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert "message.clear_results_success" in result["message"]
    assert '"count":5' in result["message"]
    assert "race_results.jsonl.bak-20260914120000" in result["message"]


def test_clear_results_status_code_maps_to_message_key():
    cases = [
        (401, "message.clear_results_wrong_password"),
        (403, "message.clear_results_not_configured"),
        (409, "message.clear_results_race_running"),
    ]
    for status, expected_key in cases:
        harness = _harness(f"""
global.window = {{ confirm: () => true }};
el("clear-results-password").value = "hunter2";
global.fetch = async () => ({{ ok: false, status: {status}, json: async () => ({{ detail: "server said no" }}) }});

(async () => {{
  await clearRaceResults();
  console.log(JSON.stringify({{ message: el("clear-results-message").textContent }}));
}})();
""")
        output = _run_node(harness)
        result = json.loads(output)
        assert (
            expected_key in result["message"]
        ), f"status {status} expected key {expected_key}, got {result['message']}"
