"""Game Admin gains a "Roster & Heats" panel (hub_server/static/
gameAdmin.html): a CSV import handler, a one-button "Load next heat", and
per-entry Absent/Requeue actions.

This executes the REAL, unmodified functions pulled out of gameAdmin.html's
inline <script> via brace-depth matching (the same extraction technique as
test_signup_division.py / test_record_wall_division.py) under `node` with
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
    # If this is an "async function name(...)", include the "async" keyword
    # -- otherwise the extracted body's `await` calls are a syntax error in
    # isolation.
    async_start = source.rfind("async ", 0, start)
    if async_start != -1 and source[async_start:start] == "async ":
        start = async_start
    # Skip past the parameter list before looking for the body's opening
    # brace -- a default parameter value like `options = {}` has its own
    # "{" that would otherwise be mistaken for the function body.
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
"""


def test_load_next_heat_button_disabled_while_race_running():
    source = _strip_js_comments(_read())
    render_roster = _extract_function(source, "renderRoster")
    division_label = _extract_function(source, "divisionLabel")
    render_heat_list = _extract_function(source, "renderRosterHeatList")
    render_entry_list = _extract_function(source, "renderRosterEntryList")
    assert "btn-load-next-heat" in render_roster  # sanity: real source

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
const readyDisabled = el("btn-load-next-heat").disabled;

state.race.state = "RUNNING";
renderRoster();
const runningDisabled = el("btn-load-next-heat").disabled;

console.log(JSON.stringify({{ readyDisabled, runningDisabled }}));
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["readyDisabled"] is False
    assert result["runningDisabled"] is True


def test_render_roster_shows_current_heat_with_station_and_next_heat_without():
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
    heat_size: 1,
    current_heat: [{{ id: "a", name: "Alice", division: "men", team: "Red", station_number: 1 }}],
    next_heat: [{{ id: "b", name: "Bob", division: "women", team: null, station_number: null }}],
    counts: {{ pending: 1, loaded: 1, done: 0, absent: 0 }},
  }},
}};
renderRoster();

console.log(JSON.stringify({{
  current: el("roster-current-heat").innerHTML,
  next: el("roster-next-heat").innerHTML,
  counts: el("roster-counts").textContent,
}}));
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert "Alice" in result["current"]
    assert "1" in result["current"]  # station number shown for the current heat
    assert "Bob" in result["next"]
    assert "1" in result["counts"]  # pending count rendered


def test_import_roster_csv_posts_json_body_with_admin_headers():
    source = _strip_js_comments(_read())
    import_fn = _extract_function(source, "importRosterCsv")
    render_errors = _extract_function(source, "renderRosterImportErrors")
    assert "csv" in import_fn  # sanity: real source, not a stub

    harness = f"""
{_DOM_STUB}
function adminHeaders(extra = {{}}) {{ return {{ ...extra, "X-FitRace-Admin-Token": "secret" }}; }}
function renderRoster() {{}}
function setMessage(id, text, kind) {{ el(id).textContent = text; }}

{render_errors}

let capturedUrl = null;
let capturedOptions = null;
global.fetch = async (url, options) => {{
  capturedUrl = url;
  capturedOptions = options;
  return {{ ok: true, json: async () => ({{ entries: [] }}) }};
}};

{import_fn}

(async () => {{
  await importRosterCsv("name\\nAlice\\n");
  console.log(JSON.stringify({{
    url: capturedUrl,
    method: capturedOptions.method,
    body: JSON.parse(capturedOptions.body),
    headers: capturedOptions.headers,
  }}));
}})();
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["url"] == "/api/roster/import"
    assert result["method"] == "POST"
    assert result["body"] == {"csv": "name\nAlice\n"}
    assert result["headers"]["X-FitRace-Admin-Token"] == "secret"
    assert result["headers"]["Content-Type"] == "application/json"


def test_import_roster_csv_renders_row_errors_on_422():
    source = _strip_js_comments(_read())
    import_fn = _extract_function(source, "importRosterCsv")
    render_errors = _extract_function(source, "renderRosterImportErrors")

    harness = f"""
{_DOM_STUB}
function adminHeaders(extra = {{}}) {{ return {{ ...extra }}; }}
function renderRoster() {{}}
let lastMessage = null;
function setMessage(id, text, kind) {{ lastMessage = {{ id, text, kind }}; }}

{render_errors}

global.fetch = async (url, options) => {{
  return {{
    ok: false,
    statusText: "Unprocessable Entity",
    json: async () => ({{ detail: [{{ row: 2, message: "Invalid division: bogus" }}] }}),
  }};
}};

{import_fn}

(async () => {{
  await importRosterCsv("name,division\\nAlice,bogus\\n");
  console.log(JSON.stringify({{
    errorsHtml: el("roster-import-errors").innerHTML,
    messageKind: lastMessage.kind,
  }}));
}})();
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert "Invalid division: bogus" in result["errorsHtml"]
    assert result["messageKind"] == "error"


def test_handle_roster_file_selected_confirms_before_replacing_existing_roster():
    source = _strip_js_comments(_read())
    handle_fn = _extract_function(source, "handleRosterFileSelected")
    assert "confirm" in handle_fn  # sanity: real source, not a stub

    harness = f"""
{_DOM_STUB}
function t(key) {{ return key; }}

class FakeFileReader {{
  readAsText(file) {{
    this.result = file.text;
    if (this.onload) this.onload();
  }}
}}
global.FileReader = FakeFileReader;

let confirmCalled = false;
let confirmReturns = true;
global.window = {{ confirm: () => {{ confirmCalled = true; return confirmReturns; }} }};

let importedWith = null;
function importRosterCsv(text) {{ importedWith = text; }}

let state = {{ roster: {{ entries: [{{ id: "x" }}] }} }};

{handle_fn}

const event = {{ target: {{ files: [{{ text: "name\\nAlice\\n" }}], value: "stub.csv" }} }};
handleRosterFileSelected(event);

console.log(JSON.stringify({{
  confirmCalled,
  importedWith,
  inputCleared: event.target.value === "",
}}));
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["confirmCalled"] is True
    assert result["importedWith"] == "name\nAlice\n"
    assert result["inputCleared"] is True


def test_load_next_heat_retries_with_force_only_after_confirm_returns_true():
    source = _strip_js_comments(_read())
    load_next_heat_fn = _extract_function(source, "loadNextHeat")
    assert "current_heat_not_raced" in load_next_heat_fn  # sanity: real source

    harness = f"""
{_DOM_STUB}
function adminHeaders(extra = {{}}) {{ return {{ ...extra }}; }}
function renderRoster() {{}}
async function refreshOperationalState() {{}}
function setMessage(id, text, kind) {{ el(id).textContent = text; }}

let state = {{ race: {{ state: "READY" }} }};

let confirmCalled = false;
let confirmArg = null;
global.window = {{
  confirm: (message) => {{ confirmCalled = true; confirmArg = message; return true; }},
}};

let callCount = 0;
const capturedBodies = [];
global.fetch = async (url, options) => {{
  callCount += 1;
  capturedBodies.push(JSON.parse(options.body));
  if (callCount === 1) {{
    return {{
      ok: false,
      status: 409,
      json: async () => ({{
        detail: {{
          reason: "current_heat_not_raced",
          message: "current heat has not raced",
          current_heat: [{{ name: "Alice" }}, {{ name: "Bob" }}],
        }},
      }}),
    }};
  }}
  return {{ ok: true, status: 200, json: async () => ({{ entries: [] }}) }};
}};

{load_next_heat_fn}

(async () => {{
  await loadNextHeat();
  console.log(JSON.stringify({{ confirmCalled, confirmArg, callCount, capturedBodies }}));
}})();
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["confirmCalled"] is True
    assert "Alice, Bob" in result["confirmArg"]
    assert result["callCount"] == 2
    assert result["capturedBodies"] == [{"force": False}, {"force": True}]


def test_load_next_heat_does_not_retry_when_confirm_declined():
    source = _strip_js_comments(_read())
    load_next_heat_fn = _extract_function(source, "loadNextHeat")

    harness = f"""
{_DOM_STUB}
function adminHeaders(extra = {{}}) {{ return {{ ...extra }}; }}
function renderRoster() {{}}
async function refreshOperationalState() {{}}
function setMessage(id, text, kind) {{ el(id).textContent = text; }}

let state = {{ race: {{ state: "READY" }} }};

let confirmCalled = false;
global.window = {{ confirm: () => {{ confirmCalled = true; return false; }} }};

let callCount = 0;
global.fetch = async (url, options) => {{
  callCount += 1;
  return {{
    ok: false,
    status: 409,
    json: async () => ({{
      detail: {{
        reason: "current_heat_not_raced",
        message: "current heat has not raced",
        current_heat: [{{ name: "Alice" }}],
      }},
    }}),
  }};
}};

{load_next_heat_fn}

(async () => {{
  await loadNextHeat();
  console.log(JSON.stringify({{ confirmCalled, callCount }}));
}})();
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["confirmCalled"] is True
    assert result["callCount"] == 1


def test_handle_roster_file_selected_skips_import_when_confirm_declined():
    source = _strip_js_comments(_read())
    handle_fn = _extract_function(source, "handleRosterFileSelected")

    harness = f"""
{_DOM_STUB}
function t(key) {{ return key; }}

class FakeFileReader {{
  readAsText(file) {{
    this.result = file.text;
    if (this.onload) this.onload();
  }}
}}
global.FileReader = FakeFileReader;

global.window = {{ confirm: () => false }};

let importCalled = false;
function importRosterCsv(text) {{ importCalled = true; }}

let state = {{ roster: {{ entries: [{{ id: "x" }}] }} }};

{handle_fn}

const event = {{ target: {{ files: [{{ text: "name\\nAlice\\n" }}], value: "stub.csv" }} }};
handleRosterFileSelected(event);

console.log(JSON.stringify({{ importCalled }}));
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["importCalled"] is False
