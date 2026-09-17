"""Game Admin gains a "Clear Results" action (hub_server/static/
gameAdmin.html) next to "Download Results (CSV)": a danger-styled button
that opens a real two-step in-page confirmation dialog (never
window.confirm/alert/prompt) before POSTing to /api/results/clear.

This executes the REAL, unmodified functions pulled out of gameAdmin.html's
inline <script> via brace-depth matching (the same extraction technique as
test_game_admin_roster_panel.py / test_game_admin_results_export.py) under
`node` with minimal DOM/fetch stubs -- never a source-text grep -- so
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
function makeClassList(el) {
  return {
    add(c) { el._classes.add(c); },
    remove(c) { el._classes.delete(c); },
    toggle(c, force) {
      if (force === undefined) {
        if (el._classes.has(c)) { el._classes.delete(c); return false; }
        el._classes.add(c);
        return true;
      }
      if (force) el._classes.add(c); else el._classes.delete(c);
      return force;
    },
    contains(c) { return el._classes.has(c); },
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
      _classes: new Set(),
      focusCallCount: 0,
      focus() { element.focusCallCount += 1; document.activeElement = element; },
    };
    element.classList = makeClassList(element);
    elements[id] = element;
  }
  return elements[id];
}
function $(id) { return el(id); }
let currentLocale = "zh-TW";
function t(key, params = {}) {
  const paramText = Object.keys(params).length ? ` ${JSON.stringify(params)}` : "";
  return `${key}${paramText}`;
}
function setMessage(id, text, kind) {
  const node = el(id);
  node.textContent = text || "";
  node.className = `status-text ${kind || ""}`;
}
function adminHeaders(extra = {}) { return { ...extra, "X-FitRace-Admin-Token": "secret" }; }
const state = { clearResultsCount: 0 };

// Minimal `document` stand-in: tracks the currently focused element (set by
// el().focus() above) and lets a real keydown listener be registered/removed
// on it, so a test can simulate "Escape reaches document" (how a real
// browser bubbles a keydown from whatever is actually focused) instead of
// calling a handler function directly against the modal element -- which
// would pass even if the real code never wired the listener anywhere the
// event could reach.
const _documentListeners = { keydown: [] };
const document = {
  activeElement: null,
  addEventListener(type, handler) {
    const list = _documentListeners[type] || (_documentListeners[type] = []);
    if (!list.includes(handler)) list.push(handler);
  },
  removeEventListener(type, handler) {
    const list = _documentListeners[type];
    if (list) _documentListeners[type] = list.filter((h) => h !== handler);
  },
  listenerCount(type) { return (_documentListeners[type] || []).length; },
  dispatchKeydown(event) {
    (_documentListeners.keydown || []).slice().forEach((handler) => handler(event));
  },
};
"""


def _harness(body: str) -> str:
    source = _strip_js_comments(_read())
    fetch_json_fn = _extract_function(source, "fetchJson")
    open_fn = _extract_function(source, "openClearResultsModal")
    close_fn = _extract_function(source, "closeClearResultsModal")
    step2_fn = _extract_function(source, "goToClearResultsStep2")
    confirm_fn = _extract_function(source, "confirmClearResults")
    backdrop_fn = _extract_function(source, "handleClearResultsBackdropClick")
    keydown_fn = _extract_function(source, "handleClearResultsKeydown")
    assert "results/clear" in confirm_fn  # sanity: real source, not a stub
    assert "results/races" in open_fn  # sanity: real source, not a stub
    return f"""
{_DOM_STUB}

{fetch_json_fn}
{open_fn}
{close_fn}
{step2_fn}
{confirm_fn}
{backdrop_fn}
{keydown_fn}

{body}
"""


def _races_fetch_script(count):
    return f"""
global.fetch = async (url) => {{
  calls.push({{ url, method: "GET" }});
  return {{
    ok: true,
    status: 200,
    statusText: "OK",
    text: async () => JSON.stringify({{ races: {json.dumps([{} for _ in range(count)])} }}),
  }};
}};
"""


def test_open_modal_shows_step1_with_race_count_and_hides_step2():
    harness = _harness(f"""
const calls = [];
{_races_fetch_script(3)}

(async () => {{
  await openClearResultsModal();
  console.log(JSON.stringify({{
    step1Hidden: el("clear-results-step-1").hidden,
    step2Hidden: el("clear-results-step-2").hidden,
    step1Body: el("clear-results-step1-body").textContent,
    nextHidden: el("btn-clear-results-next").hidden,
    modalShown: el("clear-results-modal").classList.contains("show"),
  }}));
}})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["step1Hidden"] is False
    assert result["step2Hidden"] is True
    assert '"count":3' in result["step1Body"]
    assert result["nextHidden"] is False
    assert result["modalShown"] is True


def test_zero_races_hides_next_button_and_shows_nothing_to_clear():
    harness = _harness(f"""
const calls = [];
{_races_fetch_script(0)}

(async () => {{
  await openClearResultsModal();
  console.log(JSON.stringify({{
    nextHidden: el("btn-clear-results-next").hidden,
    step1Body: el("clear-results-step1-body").textContent,
  }}));
}})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["nextHidden"] is True
    assert "clear_results.step1_body_empty" in result["step1Body"]


def test_next_advances_to_step2_and_focuses_cancel_not_danger_button():
    harness = _harness(f"""
const calls = [];
{_races_fetch_script(5)}

(async () => {{
  await openClearResultsModal();
  goToClearResultsStep2();
  console.log(JSON.stringify({{
    step1Hidden: el("clear-results-step-1").hidden,
    step2Hidden: el("clear-results-step-2").hidden,
    step2Body: el("clear-results-step2-body").textContent,
    cancelFocusCount: el("btn-clear-results-cancel-2").focusCallCount,
    confirmFocusCount: el("btn-clear-results-confirm").focusCallCount,
    resultsClearCalls: calls.filter((c) => c.url === "/api/results/clear").length,
  }}));
}})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["step1Hidden"] is True
    assert result["step2Hidden"] is False
    assert '"count":5' in result["step2Body"]
    assert result["cancelFocusCount"] == 1
    assert result["confirmFocusCount"] == 0
    assert result["resultsClearCalls"] == 0


def test_cancel_at_step1_sends_nothing():
    harness = _harness(f"""
const calls = [];
{_races_fetch_script(2)}

(async () => {{
  await openClearResultsModal();
  closeClearResultsModal();
  console.log(JSON.stringify({{
    modalShown: el("clear-results-modal").classList.contains("show"),
    resultsClearCalls: calls.filter((c) => c.url === "/api/results/clear").length,
  }}));
}})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["modalShown"] is False
    assert result["resultsClearCalls"] == 0


def test_cancel_at_step2_sends_nothing():
    harness = _harness(f"""
const calls = [];
{_races_fetch_script(2)}

(async () => {{
  await openClearResultsModal();
  goToClearResultsStep2();
  closeClearResultsModal();
  console.log(JSON.stringify({{
    modalShown: el("clear-results-modal").classList.contains("show"),
    resultsClearCalls: calls.filter((c) => c.url === "/api/results/clear").length,
  }}));
}})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["modalShown"] is False
    assert result["resultsClearCalls"] == 0


def test_backdrop_click_closes_the_modal():
    harness = _harness("""
el("clear-results-modal").classList.add("show");
el("clear-results-step-2").hidden = false;
el("clear-results-step-1").hidden = true;

handleClearResultsBackdropClick({ target: { id: "clear-results-modal" } });
const afterBackdrop = {
  modalShown: el("clear-results-modal").classList.contains("show"),
};

el("clear-results-modal").classList.add("show");
handleClearResultsBackdropClick({ target: { id: "some-inner-element" } });
const afterMissClick = {
  modalShown: el("clear-results-modal").classList.contains("show"),
};

console.log(JSON.stringify({ afterBackdrop, afterMissClick }));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["afterBackdrop"]["modalShown"] is False
    assert result["afterMissClick"]["modalShown"] is True


def test_open_modal_moves_focus_to_step1_cancel_not_next():
    # Before the dialog opens, focus is wherever a real click left it: on
    # the trigger button out in the roster panel, NOT inside the modal.
    harness = _harness(f"""
const calls = [];
{_races_fetch_script(3)}
el("btn-clear-results").focus();

(async () => {{
  await openClearResultsModal();
  console.log(JSON.stringify({{
    activeElementId: document.activeElement ? document.activeElement.id : null,
  }}));
}})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["activeElementId"] == "btn-clear-results-cancel-1"


def test_escape_via_document_closes_modal_at_step1_even_though_trigger_had_focus():
    # Regression test: a real browser dispatches Escape to whatever is
    # focused, then it bubbles to document -- it is never delivered
    # straight to the modal element. Simulate that faithfully: focus starts
    # on the outer trigger button (as it would after a real click), and the
    # test only ever calls document.dispatchKeydown(), never
    # handleClearResultsKeydown() directly against the modal.
    harness = _harness(f"""
const calls = [];
{_races_fetch_script(3)}
el("btn-clear-results").focus();

(async () => {{
  await openClearResultsModal();
  const shownBefore = el("clear-results-modal").classList.contains("show");
  document.dispatchKeydown({{ key: "Escape" }});
  console.log(JSON.stringify({{
    shownBefore,
    shownAfter: el("clear-results-modal").classList.contains("show"),
    listenerCountAfter: document.listenerCount("keydown"),
  }}));
}})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["shownBefore"] is True
    assert result["shownAfter"] is False
    assert result["listenerCountAfter"] == 0


def test_escape_via_document_closes_modal_at_step2():
    harness = _harness(f"""
const calls = [];
{_races_fetch_script(3)}
el("btn-clear-results").focus();

(async () => {{
  await openClearResultsModal();
  goToClearResultsStep2();
  const shownBefore = el("clear-results-modal").classList.contains("show");
  document.dispatchKeydown({{ key: "Escape" }});
  console.log(JSON.stringify({{
    shownBefore,
    shownAfter: el("clear-results-modal").classList.contains("show"),
  }}));
}})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["shownBefore"] is True
    assert result["shownAfter"] is False


def test_confirm_sends_post_with_admin_headers_and_empty_password():
    harness = _harness("""
const calls = [];
global.fetch = async (url, options) => {
  calls.push({ url, options });
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify({ cleared_count: 4, backup_path: "/data/race_results.jsonl.bak-20260917120000" }),
  };
};

(async () => {
  await confirmClearResults();
  console.log(JSON.stringify({
    calls: calls.map((c) => ({
      url: c.url,
      method: c.options.method,
      headers: c.options.headers,
      body: JSON.parse(c.options.body),
    })),
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert len(result["calls"]) == 1
    call = result["calls"][0]
    assert call["url"] == "/api/results/clear"
    assert call["method"] == "POST"
    assert call["headers"]["X-FitRace-Admin-Token"] == "secret"
    assert call["body"] == {"password": ""}


def test_confirm_success_closes_modal_and_shows_count_and_backup_name():
    harness = _harness("""
global.fetch = async () => ({
  ok: true,
  status: 200,
  text: async () => JSON.stringify({ cleared_count: 4, backup_path: "/data/race_results.jsonl.bak-20260917120000" }),
});

(async () => {
  el("clear-results-modal").classList.add("show");
  await confirmClearResults();
  console.log(JSON.stringify({
    modalShown: el("clear-results-modal").classList.contains("show"),
    message: el("results-export-message").textContent,
    kind: el("results-export-message").className,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["modalShown"] is False
    assert '"count":4' in result["message"]
    assert "race_results.jsonl.bak-20260917120000" in result["message"]
    assert "ok" in result["kind"]


def test_confirm_running_race_shows_409_message_and_keeps_modal_open():
    harness = _harness("""
global.fetch = async () => ({
  ok: false,
  status: 409,
  text: async () => JSON.stringify({ detail: "Race is running" }),
});

(async () => {
  el("clear-results-modal").classList.add("show");
  await confirmClearResults();
  console.log(JSON.stringify({
    modalShown: el("clear-results-modal").classList.contains("show"),
    message: el("clear-results-modal-message").textContent,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["modalShown"] is True
    assert "message.clear_results_race_running" in result["message"]


def test_confirm_unauthorized_shows_401_message_telling_operator_to_unlock():
    harness = _harness("""
global.fetch = async () => ({
  ok: false,
  status: 401,
  text: async () => JSON.stringify({ detail: "Invalid password" }),
});

(async () => {
  el("clear-results-modal").classList.add("show");
  await confirmClearResults();
  console.log(JSON.stringify({
    modalShown: el("clear-results-modal").classList.contains("show"),
    message: el("clear-results-modal-message").textContent,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["modalShown"] is True
    assert "message.clear_results_unauthorized" in result["message"]


def test_confirm_double_click_sends_only_one_post():
    harness = _harness("""
const calls = [];
let resolveFetch;
global.fetch = async (url) => {
  calls.push(url);
  return new Promise((resolve) => {
    resolveFetch = () => resolve({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ cleared_count: 1, backup_path: null }),
    });
  });
};

(async () => {
  const first = confirmClearResults();
  const second = confirmClearResults();
  resolveFetch();
  await Promise.all([first, second]);
  console.log(JSON.stringify({ callCount: calls.length }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["callCount"] == 1


def test_confirm_button_disabled_while_in_flight_and_reenabled_after():
    harness = _harness("""
let disabledDuringFetch = null;
global.fetch = async () => {
  disabledDuringFetch = el("btn-clear-results-confirm").disabled;
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify({ cleared_count: 1, backup_path: null }),
  };
};

(async () => {
  await confirmClearResults();
  console.log(JSON.stringify({
    disabledDuringFetch,
    disabledAfter: el("btn-clear-results-confirm").disabled,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["disabledDuringFetch"] is True
    assert result["disabledAfter"] is False
