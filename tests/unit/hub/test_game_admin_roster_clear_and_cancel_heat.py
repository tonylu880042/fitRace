"""Game Admin gains two new roster actions (hub_server/static/
gameAdmin.html), fixing a real venue incident where an operator loaded a
heat, the race config changed/reset under it, and there was no way back:

- "Clear Roster" (danger, next to "Clear Results"): a two-step in-page
  confirmation dialog -- the same pattern as "Clear Results" -- before
  DELETE /api/roster.
- "Cancel Current Heat" (next to the "Current Heat" block, only shown/
  enabled when a heat is actually loaded): a single in-page confirmation
  naming who is in the heat before POST /api/roster/current-heat/cancel.

This executes the REAL, unmodified functions pulled out of gameAdmin.html's
inline <script> via brace-depth matching (the same extraction technique as
test_game_admin_clear_results.py / test_game_admin_roster_panel.py) under
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
function escapeHtml(value) { return String(value ?? ""); }
let currentLocale = "zh-TW";
function syncVisibility() {}
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


def _clear_roster_harness(body: str) -> str:
    source = _strip_js_comments(_read())
    total_fn = _extract_function(source, "clearRosterTotal")
    open_fn = _extract_function(source, "openClearRosterModal")
    close_fn = _extract_function(source, "closeClearRosterModal")
    step2_fn = _extract_function(source, "goToClearRosterStep2")
    confirm_fn = _extract_function(source, "confirmClearRoster")
    backdrop_fn = _extract_function(source, "handleClearRosterBackdropClick")
    keydown_fn = _extract_function(source, "handleClearRosterKeydown")
    assert "/api/roster" in confirm_fn  # sanity: real source, not a stub
    assert "DELETE" in confirm_fn
    return f"""
{_DOM_STUB}

let state;

{total_fn}
{open_fn}
{close_fn}
{step2_fn}
{confirm_fn}
{backdrop_fn}
{keydown_fn}

{body}
"""


def _cancel_heat_harness(body: str) -> str:
    source = _strip_js_comments(_read())
    names_fn = _extract_function(source, "currentHeatDisplayNames")
    open_fn = _extract_function(source, "openCancelHeatModal")
    close_fn = _extract_function(source, "closeCancelHeatModal")
    confirm_fn = _extract_function(source, "confirmCancelHeat")
    backdrop_fn = _extract_function(source, "handleCancelHeatBackdropClick")
    keydown_fn = _extract_function(source, "handleCancelHeatKeydown")
    assert "current-heat/cancel" in confirm_fn  # sanity: real source

    async_refresh = """
async function refreshOperationalState() {}
"""
    return f"""
{_DOM_STUB}

let state;
{async_refresh}

{names_fn}
{open_fn}
{close_fn}
{confirm_fn}
{backdrop_fn}
{keydown_fn}

{body}
"""


# ---------------------------------------------------------------------------
# renderRoster: show/hide + disable wiring for the two new buttons
# ---------------------------------------------------------------------------


def test_render_roster_hides_cancel_heat_button_when_nothing_loaded():
    source = _strip_js_comments(_read())
    render_roster = _extract_function(source, "renderRoster")
    division_label = _extract_function(source, "divisionLabel")
    render_heat_list = _extract_function(source, "renderRosterHeatList")
    render_entry_list = _extract_function(source, "renderRosterEntryList")
    assert "btn-cancel-current-heat" in render_roster  # sanity: real wiring

    harness = f"""
{_DOM_STUB}

{division_label}
{render_heat_list}
{render_entry_list}

let state;

{render_roster}

state = {{
  race: {{ state: "READY" }},
  roster: {{ entries: [], heat_size: 2, current_heat: [], next_heat: [], counts: {{ pending: 0, loaded: 0, done: 0, absent: 0 }} }},
}};
renderRoster();
const withNothingLoaded = {{ hidden: el("btn-cancel-current-heat").hidden }};

state.roster.current_heat = [{{ id: "a", name: "Alice", station_number: 1 }}];
state.roster.counts.loaded = 1;
renderRoster();
const withHeatLoaded = {{ hidden: el("btn-cancel-current-heat").hidden }};

console.log(JSON.stringify({{ withNothingLoaded, withHeatLoaded }}));
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["withNothingLoaded"]["hidden"] is True
    assert result["withHeatLoaded"]["hidden"] is False


def test_render_roster_disables_clear_roster_and_cancel_heat_while_running():
    source = _strip_js_comments(_read())
    render_roster = _extract_function(source, "renderRoster")
    division_label = _extract_function(source, "divisionLabel")
    render_heat_list = _extract_function(source, "renderRosterHeatList")
    render_entry_list = _extract_function(source, "renderRosterEntryList")

    harness = f"""
{_DOM_STUB}

{division_label}
{render_heat_list}
{render_entry_list}

let state;

{render_roster}

state = {{
  race: {{ state: "READY" }},
  roster: {{
    entries: [],
    heat_size: 2,
    current_heat: [{{ id: "a", name: "Alice", station_number: 1 }}],
    next_heat: [],
    counts: {{ pending: 0, loaded: 1, done: 0, absent: 0 }},
  }},
}};
renderRoster();
const ready = {{
  clearRosterDisabled: el("btn-clear-roster").disabled,
  cancelHeatDisabled: el("btn-cancel-current-heat").disabled,
  cancelHeatHidden: el("btn-cancel-current-heat").hidden,
}};

state.race.state = "RUNNING";
renderRoster();
const running = {{
  clearRosterDisabled: el("btn-clear-roster").disabled,
  cancelHeatDisabled: el("btn-cancel-current-heat").disabled,
}};

console.log(JSON.stringify({{ ready, running }}));
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["ready"]["clearRosterDisabled"] is False
    assert result["ready"]["cancelHeatDisabled"] is False
    assert result["ready"]["cancelHeatHidden"] is False
    assert result["running"]["clearRosterDisabled"] is True
    assert result["running"]["cancelHeatDisabled"] is True


# ---------------------------------------------------------------------------
# Clear Roster: two-step confirmation, nothing sent before step-2 confirm
# ---------------------------------------------------------------------------


def test_open_clear_roster_modal_shows_counts_and_hides_step2():
    harness = _clear_roster_harness("""
state = { roster: { counts: { pending: 3, loaded: 1, done: 2, absent: 1 } } };

openClearRosterModal();
console.log(JSON.stringify({
  step1Hidden: el("clear-roster-step-1").hidden,
  step2Hidden: el("clear-roster-step-2").hidden,
  step1Body: el("clear-roster-step1-body").textContent,
  nextHidden: el("btn-clear-roster-next").hidden,
  modalShown: el("clear-roster-modal").classList.contains("show"),
}));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["step1Hidden"] is False
    assert result["step2Hidden"] is True
    assert '"pending":3' in result["step1Body"]
    assert '"loaded":1' in result["step1Body"]
    assert '"done":2' in result["step1Body"]
    assert '"absent":1' in result["step1Body"]
    assert result["nextHidden"] is False
    assert result["modalShown"] is True


def test_open_clear_roster_modal_empty_roster_hides_next_button():
    harness = _clear_roster_harness("""
state = { roster: { counts: { pending: 0, loaded: 0, done: 0, absent: 0 } } };

openClearRosterModal();
console.log(JSON.stringify({
  nextHidden: el("btn-clear-roster-next").hidden,
  step1Body: el("clear-roster-step1-body").textContent,
}));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["nextHidden"] is True
    assert "clear_roster.step1_body_empty" in result["step1Body"]


def test_clear_roster_next_advances_to_step2_focuses_cancel_sends_nothing():
    harness = _clear_roster_harness("""
const calls = [];
global.fetch = async (url, options) => {
  calls.push({ url, method: (options && options.method) || "GET" });
  return { ok: true, status: 200, text: async () => JSON.stringify({ entries: [] }) };
};
state = { roster: { counts: { pending: 3, loaded: 0, done: 0, absent: 0 } } };

openClearRosterModal();
goToClearRosterStep2();
console.log(JSON.stringify({
  step1Hidden: el("clear-roster-step-1").hidden,
  step2Hidden: el("clear-roster-step-2").hidden,
  step2Body: el("clear-roster-step2-body").textContent,
  cancelFocusCount: el("btn-clear-roster-cancel-2").focusCallCount,
  confirmFocusCount: el("btn-clear-roster-confirm").focusCallCount,
  deleteCalls: calls.filter((c) => c.method === "DELETE").length,
}));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["step1Hidden"] is True
    assert result["step2Hidden"] is False
    assert '"total":3' in result["step2Body"]
    assert result["cancelFocusCount"] == 1
    assert result["confirmFocusCount"] == 0
    assert result["deleteCalls"] == 0


def test_clear_roster_cancel_at_step1_and_step2_send_nothing():
    harness = _clear_roster_harness("""
const calls = [];
global.fetch = async (url, options) => {
  calls.push({ url, method: (options && options.method) || "GET" });
  return { ok: true, status: 200, text: async () => JSON.stringify({ entries: [] }) };
};
state = { roster: { counts: { pending: 2, loaded: 0, done: 0, absent: 0 } } };

openClearRosterModal();
closeClearRosterModal();
const afterStep1Cancel = {
  modalShown: el("clear-roster-modal").classList.contains("show"),
  deleteCalls: calls.filter((c) => c.method === "DELETE").length,
};

openClearRosterModal();
goToClearRosterStep2();
closeClearRosterModal();
const afterStep2Cancel = {
  modalShown: el("clear-roster-modal").classList.contains("show"),
  deleteCalls: calls.filter((c) => c.method === "DELETE").length,
};

console.log(JSON.stringify({ afterStep1Cancel, afterStep2Cancel }));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["afterStep1Cancel"]["modalShown"] is False
    assert result["afterStep1Cancel"]["deleteCalls"] == 0
    assert result["afterStep2Cancel"]["modalShown"] is False
    assert result["afterStep2Cancel"]["deleteCalls"] == 0


def test_clear_roster_escape_via_document_closes_modal_at_either_step():
    harness = _clear_roster_harness("""
state = { roster: { counts: { pending: 2, loaded: 0, done: 0, absent: 0 } } };
el("btn-clear-roster").focus();

openClearRosterModal();
const shownAtStep1 = el("clear-roster-modal").classList.contains("show");
document.dispatchKeydown({ key: "Escape" });
const afterStep1Escape = {
  shown: el("clear-roster-modal").classList.contains("show"),
  listenerCount: document.listenerCount("keydown"),
};

openClearRosterModal();
goToClearRosterStep2();
document.dispatchKeydown({ key: "Escape" });
const afterStep2Escape = { shown: el("clear-roster-modal").classList.contains("show") };

console.log(JSON.stringify({ shownAtStep1, afterStep1Escape, afterStep2Escape }));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["shownAtStep1"] is True
    assert result["afterStep1Escape"]["shown"] is False
    assert result["afterStep1Escape"]["listenerCount"] == 0
    assert result["afterStep2Escape"]["shown"] is False


def test_clear_roster_backdrop_click_closes_modal():
    harness = _clear_roster_harness("""
el("clear-roster-modal").classList.add("show");
handleClearRosterBackdropClick({ target: { id: "clear-roster-modal" } });
const afterBackdrop = { shown: el("clear-roster-modal").classList.contains("show") };

el("clear-roster-modal").classList.add("show");
handleClearRosterBackdropClick({ target: { id: "some-inner-element" } });
const afterMissClick = { shown: el("clear-roster-modal").classList.contains("show") };

console.log(JSON.stringify({ afterBackdrop, afterMissClick }));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["afterBackdrop"]["shown"] is False
    assert result["afterMissClick"]["shown"] is True


def test_confirm_clear_roster_sends_delete_with_admin_headers():
    harness = _clear_roster_harness("""
const calls = [];
global.fetch = async (url, options) => {
  calls.push({ url, options });
  return { ok: true, status: 200, text: async () => JSON.stringify({ entries: [], counts: { pending: 0, loaded: 0, done: 0, absent: 0 } }) };
};
async function refreshOperationalState() {}

(async () => {
  await confirmClearRoster();
  console.log(JSON.stringify({
    calls: calls.map((c) => ({ url: c.url, method: c.options.method, headers: c.options.headers })),
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert len(result["calls"]) == 1
    call = result["calls"][0]
    assert call["url"] == "/api/roster"
    assert call["method"] == "DELETE"
    assert call["headers"]["X-FitRace-Admin-Token"] == "secret"


def test_confirm_clear_roster_success_closes_modal_and_refreshes():
    harness = _clear_roster_harness("""
state = {};
global.fetch = async () => ({
  ok: true,
  status: 200,
  text: async () => JSON.stringify({ entries: [], counts: { pending: 0, loaded: 0, done: 0, absent: 0 } }),
});
let refreshed = false;
async function refreshOperationalState() { refreshed = true; }
function renderRoster() {}

(async () => {
  el("clear-roster-modal").classList.add("show");
  await confirmClearRoster();
  console.log(JSON.stringify({
    modalShown: el("clear-roster-modal").classList.contains("show"),
    refreshed,
    message: el("roster-heat-message").textContent,
    kind: el("roster-heat-message").className,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["modalShown"] is False
    assert result["refreshed"] is True
    assert "message.roster_cleared" in result["message"]
    assert "ok" in result["kind"]


def test_confirm_clear_roster_running_race_shows_409_message_and_keeps_modal_open():
    harness = _clear_roster_harness("""
global.fetch = async () => ({
  ok: false,
  status: 409,
  text: async () => JSON.stringify({ detail: "Race is running" }),
});

(async () => {
  el("clear-roster-modal").classList.add("show");
  await confirmClearRoster();
  console.log(JSON.stringify({
    modalShown: el("clear-roster-modal").classList.contains("show"),
    message: el("clear-roster-modal-message").textContent,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["modalShown"] is True
    assert "message.clear_roster_race_running" in result["message"]


def test_confirm_clear_roster_double_click_sends_only_one_request():
    harness = _clear_roster_harness("""
state = {};
const calls = [];
let resolveFetch;
global.fetch = async (url) => {
  calls.push(url);
  return new Promise((resolve) => {
    resolveFetch = () => resolve({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ entries: [], counts: { pending: 0, loaded: 0, done: 0, absent: 0 } }),
    });
  });
};
async function refreshOperationalState() {}
function renderRoster() {}

(async () => {
  const first = confirmClearRoster();
  const second = confirmClearRoster();
  resolveFetch();
  await Promise.all([first, second]);
  console.log(JSON.stringify({ callCount: calls.length }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["callCount"] == 1


def test_confirm_clear_roster_button_disabled_while_in_flight():
    harness = _clear_roster_harness("""
state = {};
let disabledDuringFetch = null;
global.fetch = async () => {
  disabledDuringFetch = el("btn-clear-roster-confirm").disabled;
  return { ok: true, status: 200, text: async () => JSON.stringify({ entries: [], counts: { pending: 0, loaded: 0, done: 0, absent: 0 } }) };
};
async function refreshOperationalState() {}
function renderRoster() {}

(async () => {
  await confirmClearRoster();
  console.log(JSON.stringify({
    disabledDuringFetch,
    disabledAfter: el("btn-clear-roster-confirm").disabled,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["disabledDuringFetch"] is True
    assert result["disabledAfter"] is False


# ---------------------------------------------------------------------------
# Cancel Current Heat: single-step confirmation
# ---------------------------------------------------------------------------


def test_open_cancel_heat_modal_names_the_current_heat_and_focuses_cancel():
    harness = _cancel_heat_harness("""
state = {
  roster: {
    mode: "individual",
    current_heat: [{ name: "Alice" }, { name: "Bob" }],
  },
};
el("btn-cancel-current-heat").hidden = false;
el("btn-cancel-current-heat").disabled = false;

openCancelHeatModal();
console.log(JSON.stringify({
  modalShown: el("cancel-heat-modal").classList.contains("show"),
  body: el("cancel-heat-body").textContent,
  cancelFocusCount: el("btn-cancel-heat-cancel").focusCallCount,
  confirmFocusCount: el("btn-cancel-heat-confirm").focusCallCount,
}));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["modalShown"] is True
    assert "Alice, Bob" in result["body"]
    assert result["cancelFocusCount"] == 1
    assert result["confirmFocusCount"] == 0


def test_open_cancel_heat_modal_relay_mode_names_the_teams():
    harness = _cancel_heat_harness("""
state = {
  roster: {
    mode: "relay",
    current_heat_teams: [{ team: "Volt" }, { team: "Surge" }],
  },
};
el("btn-cancel-current-heat").hidden = false;
el("btn-cancel-current-heat").disabled = false;

openCancelHeatModal();
console.log(JSON.stringify({ body: el("cancel-heat-body").textContent }));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert "Volt, Surge" in result["body"]


def test_open_cancel_heat_modal_is_a_no_op_when_button_hidden_or_disabled():
    harness = _cancel_heat_harness("""
state = { roster: { mode: "individual", current_heat: [] } };
el("btn-cancel-current-heat").hidden = true;
el("btn-cancel-current-heat").disabled = false;

openCancelHeatModal();
const whenHidden = { modalShown: el("cancel-heat-modal").classList.contains("show") };

el("btn-cancel-current-heat").hidden = false;
el("btn-cancel-current-heat").disabled = true;
openCancelHeatModal();
const whenDisabled = { modalShown: el("cancel-heat-modal").classList.contains("show") };

console.log(JSON.stringify({ whenHidden, whenDisabled }));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["whenHidden"]["modalShown"] is False
    assert result["whenDisabled"]["modalShown"] is False


def test_cancel_heat_cancel_button_sends_nothing():
    harness = _cancel_heat_harness("""
const calls = [];
global.fetch = async (url) => { calls.push(url); return { ok: true, status: 200, text: async () => "{}" }; };
state = { roster: { mode: "individual", current_heat: [{ name: "Alice" }] } };
el("btn-cancel-current-heat").hidden = false;
el("btn-cancel-current-heat").disabled = false;

openCancelHeatModal();
closeCancelHeatModal();
console.log(JSON.stringify({
  modalShown: el("cancel-heat-modal").classList.contains("show"),
  postCalls: calls.length,
}));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["modalShown"] is False
    assert result["postCalls"] == 0


def test_cancel_heat_escape_via_document_closes_without_posting():
    harness = _cancel_heat_harness("""
const calls = [];
global.fetch = async (url) => { calls.push(url); return { ok: true, status: 200, text: async () => "{}" }; };
state = { roster: { mode: "individual", current_heat: [{ name: "Alice" }] } };
el("btn-cancel-current-heat").hidden = false;
el("btn-cancel-current-heat").disabled = false;
el("btn-cancel-current-heat").focus();

openCancelHeatModal();
const shownBefore = el("cancel-heat-modal").classList.contains("show");
document.dispatchKeydown({ key: "Escape" });
console.log(JSON.stringify({
  shownBefore,
  shownAfter: el("cancel-heat-modal").classList.contains("show"),
  listenerCountAfter: document.listenerCount("keydown"),
  postCalls: calls.length,
}));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["shownBefore"] is True
    assert result["shownAfter"] is False
    assert result["listenerCountAfter"] == 0
    assert result["postCalls"] == 0


def test_cancel_heat_backdrop_click_closes_modal():
    harness = _cancel_heat_harness("""
el("cancel-heat-modal").classList.add("show");
handleCancelHeatBackdropClick({ target: { id: "cancel-heat-modal" } });
const afterBackdrop = { shown: el("cancel-heat-modal").classList.contains("show") };

el("cancel-heat-modal").classList.add("show");
handleCancelHeatBackdropClick({ target: { id: "some-inner-element" } });
const afterMissClick = { shown: el("cancel-heat-modal").classList.contains("show") };

console.log(JSON.stringify({ afterBackdrop, afterMissClick }));
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["afterBackdrop"]["shown"] is False
    assert result["afterMissClick"]["shown"] is True


def test_confirm_cancel_heat_posts_and_closes_modal_on_success():
    harness = _cancel_heat_harness("""
const calls = [];
global.fetch = async (url, options) => {
  calls.push({ url, options });
  return { ok: true, status: 200, text: async () => JSON.stringify({ entries: [] }) };
};
let refreshed = false;
async function refreshOperationalState() { refreshed = true; }
function renderRoster() {}
state = { roster: { mode: "individual", current_heat: [{ name: "Alice" }] } };

(async () => {
  await confirmCancelHeat();
  console.log(JSON.stringify({
    calls: calls.map((c) => ({ url: c.url, method: c.options.method, headers: c.options.headers })),
    modalShown: el("cancel-heat-modal").classList.contains("show"),
    refreshed,
    message: el("roster-heat-message").textContent,
    kind: el("roster-heat-message").className,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert len(result["calls"]) == 1
    call = result["calls"][0]
    assert call["url"] == "/api/roster/current-heat/cancel"
    assert call["method"] == "POST"
    assert call["headers"]["X-FitRace-Admin-Token"] == "secret"
    assert result["modalShown"] is False
    assert result["refreshed"] is True
    assert "message.heat_cancelled" in result["message"]
    assert "ok" in result["kind"]


def test_confirm_cancel_heat_double_click_sends_only_one_post():
    harness = _cancel_heat_harness("""
const calls = [];
let resolveFetch;
global.fetch = async (url) => {
  calls.push(url);
  return new Promise((resolve) => {
    resolveFetch = () => resolve({ ok: true, status: 200, text: async () => JSON.stringify({ entries: [] }) });
  });
};
async function refreshOperationalState() {}
function renderRoster() {}
state = { roster: { mode: "individual", current_heat: [] } };

(async () => {
  const first = confirmCancelHeat();
  const second = confirmCancelHeat();
  resolveFetch();
  await Promise.all([first, second]);
  console.log(JSON.stringify({ callCount: calls.length }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["callCount"] == 1


def test_confirm_cancel_heat_button_disabled_while_in_flight():
    harness = _cancel_heat_harness("""
let disabledDuringFetch = null;
global.fetch = async () => {
  disabledDuringFetch = el("btn-cancel-heat-confirm").disabled;
  return { ok: true, status: 200, text: async () => JSON.stringify({ entries: [] }) };
};
async function refreshOperationalState() {}
function renderRoster() {}
state = { roster: { mode: "individual", current_heat: [] } };

(async () => {
  await confirmCancelHeat();
  console.log(JSON.stringify({
    disabledDuringFetch,
    disabledAfter: el("btn-cancel-heat-confirm").disabled,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["disabledDuringFetch"] is True
    assert result["disabledAfter"] is False


def test_confirm_cancel_heat_maps_409_details_to_friendly_messages():
    for detail, expected_key in [
        ("current heat already raced", "message.cancel_heat_already_raced"),
        ("no current heat", "message.cancel_heat_no_current_heat"),
        ("Race is running", "message.cancel_heat_race_running"),
    ]:
        harness = _cancel_heat_harness(f"""
global.fetch = async () => ({{
  ok: false,
  status: 409,
  text: async () => JSON.stringify({{ detail: {json.dumps(detail)} }}),
}});
state = {{ roster: {{ mode: "individual", current_heat: [] }} }};

(async () => {{
  el("cancel-heat-modal").classList.add("show");
  await confirmCancelHeat();
  console.log(JSON.stringify({{
    modalShown: el("cancel-heat-modal").classList.contains("show"),
    message: el("cancel-heat-modal-message").textContent,
  }}));
}})();
""")
        output = _run_node(harness)
        result = json.loads(output)
        assert result["modalShown"] is True
        assert expected_key in result["message"], (detail, result["message"])


def test_confirm_cancel_heat_unknown_error_falls_back_to_generic_message():
    harness = _cancel_heat_harness("""
global.fetch = async () => ({
  ok: false,
  status: 500,
  text: async () => JSON.stringify({ detail: "boom" }),
});
state = { roster: { mode: "individual", current_heat: [] } };

(async () => {
  el("cancel-heat-modal").classList.add("show");
  await confirmCancelHeat();
  console.log(JSON.stringify({
    modalShown: el("cancel-heat-modal").classList.contains("show"),
    message: el("cancel-heat-modal-message").textContent,
  }));
})();
""")
    output = _run_node(harness)
    result = json.loads(output)
    assert result["modalShown"] is True
    assert "message.cancel_heat_failed" in result["message"]
    assert "boom" in result["message"]
