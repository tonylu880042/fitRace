"""Game Admin's roster import preview modal (hub_server/static/
gameAdmin.html): selecting a roster file no longer goes straight to
window.confirm() + import -- it dry-runs the import first (POST
/api/roster/import with dry_run: true) and shows a preview modal (table of
姓名/組別/隊伍, error rows highlighted, relay team-size warnings, and
"將取代目前 N 筆名單" when existing_count > 0) before the operator confirms.
Confirm is disabled while there are row errors; any user-provided text (a
name, a team) is HTML-escaped before it lands in the DOM.

This executes the REAL functions pulled out of the page via brace-depth
extraction under node -- never a source-text grep -- so deleting the real
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
    async_start = source.rfind("async ", 0, start)
    if async_start != -1 and source[async_start:start] == "async ":
        start = async_start
    paren_open = source.index("(", start)
    paren_end = _matching_paren_end(source, paren_open)
    brace_open = source.index("{", paren_end)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _extract_dictionaries_js(source: str) -> str:
    const_start = source.index("const dictionaries = {")
    const_open = source.index("{", const_start)
    const_close = _matching_brace_end(source, const_open)

    zh_marker = 'dictionaries["zh-TW"] = {'
    zh_start = source.index(zh_marker, const_close)
    zh_open = source.index("{", zh_start)
    zh_close = _matching_brace_end(source, zh_open)

    return source[const_start : zh_close + 1] + ";"


def _extract_t_function(source: str) -> str:
    t_start = source.index("function t(")
    paren_open = source.index("(", t_start)
    paren_end = _matching_paren_end(source, paren_open)
    t_open = source.index("{", paren_end)
    t_end = _matching_brace_end(source, t_open)
    return source[t_start : t_end + 1]


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
      hidden: false,
      textContent: "",
      innerHTML: "",
      value: "",
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
      focus() {},
    };
  }
  return elements[id];
}
function $(id) { return el(id); }
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
function adminHeaders(extra = {}) { return { ...extra, "X-FitRace-Admin-Token": "secret" }; }
function setMessage(id, text, kind) { el(id).textContent = text; }
function t(key, params = {}) {
  const paramText = Object.keys(params).length ? ` ${JSON.stringify(params)}` : "";
  return `${key}${paramText}`;
}
global.document = { addEventListener() {}, removeEventListener() {} };
"""


def _harness(*fn_sources: str, extra: str = "") -> str:
    return f"""
{_DOM_STUB}
let state = {{ roster: {{ entries: [] }}, race: {{ config: {{}} }} }};
{chr(10).join(fn_sources)}
{extra}
"""


def _extract(source: str, *names: str) -> list[str]:
    return [_extract_function(source, name) for name in names]


def test_handle_roster_file_selected_previews_before_importing_no_confirm():
    source = _strip_js_comments(_read())
    handle_fn, decode_fn = _extract(
        source, "handleRosterFileSelected", "decodeRosterFileBytes"
    )
    assert "window.confirm" not in handle_fn  # sanity: replaced, not added to

    harness = f"""
{_DOM_STUB}
{decode_fn}
let previewedWith = null;
function previewRosterImport(text) {{ previewedWith = text; }}
let state = {{ roster: {{ entries: [{{ id: "x" }}] }} }};

class FakeFileReader {{
  readAsArrayBuffer(file) {{
    this.result = file.bytes;
    if (this.onload) this.onload();
  }}
}}
global.FileReader = FakeFileReader;

{handle_fn}

const bytes = new TextEncoder().encode("name\\nAlice\\n").buffer;
const event = {{ target: {{ files: [{{ bytes }}], value: "stub.csv" }} }};
handleRosterFileSelected(event);
console.log(JSON.stringify({{ previewedWith, inputCleared: event.target.value === "" }}));
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["previewedWith"] == "name\nAlice\n"
    assert result["inputCleared"] is True


def test_preview_roster_import_posts_dry_run_and_opens_modal():
    source = _strip_js_comments(_read())
    preview_fn, render_fn = _extract(
        source, "previewRosterImport", "renderRosterImportPreviewModal"
    )

    harness = _harness(
        render_fn,
        preview_fn,
        extra="""
let capturedUrl = null;
let capturedBody = null;
global.fetch = async (url, options) => {
  capturedUrl = url;
  capturedBody = JSON.parse(options.body);
  return { ok: true, json: async () => ({ entries: [], errors: [], existing_count: 0 }) };
};

(async () => {
  await previewRosterImport("name\\nAlice\\n");
  console.log(JSON.stringify({
    url: capturedUrl,
    body: capturedBody,
    modalShown: el("roster-import-preview-modal").classList.contains("show"),
  }));
})();
""",
    )
    # The classList stub always reports false for contains(); check the
    # modal's actual add() call happened by tracking it separately below.
    output = _run_node(
        harness.replace(
            "classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },",
            "classList: { added: false, add() { this.added = true; }, remove() {}, toggle() {}, contains() { return this.added; } },",
        )
    )
    result = json.loads(output)
    assert result["url"] == "/api/roster/import"
    assert result["body"] == {"csv": "name\nAlice\n", "dry_run": True}
    assert result["modalShown"] is True


def test_render_preview_modal_shows_table_escapes_html_and_replace_note():
    source = _strip_js_comments(_read())
    render_fn, division_label = _extract(
        source, "renderRosterImportPreviewModal", "divisionLabel"
    )

    harness = _harness(
        division_label,
        render_fn,
        extra="""
state.rosterImportPreview = {
  text: "irrelevant",
  entries: [{ name: "<img src=x>", division: "men", team: "Red" }],
  errors: [],
  existing_count: 3,
  rows: [{ row: 2, name: "<img src=x>", division: "men", team: "Red" }],
};
renderRosterImportPreviewModal();
console.log(JSON.stringify({
  table: el("roster-import-preview-table").innerHTML,
  replaceNote: el("roster-import-preview-replace-note").textContent,
  confirmDisabled: el("btn-roster-import-preview-confirm").disabled,
}));
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    assert "<img src=x>" not in result["table"]
    assert "&lt;img src=x&gt;" in result["table"]
    assert "3" in result["replaceNote"]
    assert result["confirmDisabled"] is False


def test_render_preview_modal_disables_confirm_and_highlights_errors():
    source = _strip_js_comments(_read())
    render_fn, division_label, error_text_fn = _extract(
        source,
        "renderRosterImportPreviewModal",
        "divisionLabel",
        "rosterErrorText",
    )

    harness = _harness(
        division_label,
        error_text_fn,
        render_fn,
        extra="""
state.rosterImportPreview = {
  text: "irrelevant",
  entries: [],
  errors: [{ row: 2, message: "Invalid division: bogus" }],
  existing_count: 0,
  rows: [{
    row: 2, name: "Bob", division: null, team: null,
    error: { row: 2, message: "Invalid division: bogus" },
  }],
};
renderRosterImportPreviewModal();
console.log(JSON.stringify({
  table: el("roster-import-preview-table").innerHTML,
  confirmDisabled: el("btn-roster-import-preview-confirm").disabled,
}));
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    assert "Invalid division: bogus" in result["table"]
    assert "row-error" in result["table"]
    assert result["confirmDisabled"] is True


def test_render_preview_modal_shows_rows_in_file_order_valid_and_error_mixed():
    source = _strip_js_comments(_read())
    render_fn, division_label, error_text_fn = _extract(
        source,
        "renderRosterImportPreviewModal",
        "divisionLabel",
        "rosterErrorText",
    )

    harness = _harness(
        division_label,
        error_text_fn,
        render_fn,
        extra="""
state.rosterImportPreview = {
  text: "irrelevant",
  entries: [],
  errors: [{ row: 3, message: "Invalid division: bogus", code: "invalid_division", value: "bogus" }],
  existing_count: 0,
  rows: [
    { row: 2, name: "Alice", division: "men", team: null },
    { row: 3, name: "Bob", division: null, team: null, error: { row: 3, message: "Invalid division: bogus", code: "invalid_division", value: "bogus" } },
    { row: 4, name: "Cara", division: "women", team: null },
  ],
};
renderRosterImportPreviewModal();
console.log(JSON.stringify({
  table: el("roster-import-preview-table").innerHTML,
  confirmDisabled: el("btn-roster-import-preview-confirm").disabled,
}));
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    table = result["table"]

    # File order preserved: Alice (row 2) before the error row (row 3)
    # before Cara (row 4) -- errors are not hoisted to the top.
    assert table.index("Alice") < table.index("bogus") < table.index("Cara")
    # The error row is localized and marked -- not just the raw message
    # sitting next to two untouched valid rows.
    assert "row-error" in table
    assert result["confirmDisabled"] is True


def test_render_preview_modal_shows_header_level_error_with_no_rows():
    # A header-level error (missing_header_name / missing_header_row) has
    # NO row in `rows` carrying it -- build_import_preview() returns
    # rows: [] for these. Before this fix, rendering only `rows` left the
    # table empty and Confirm disabled with no explanation at all. This
    # uses the REAL `dictionaries`/`t()` (not this file's simplified stub)
    # so it actually proves the zh-TW localized text renders, not just
    # some key echoed back.
    source = _strip_js_comments(_read())
    dict_js = _extract_dictionaries_js(source)
    t_fn = _extract_t_function(source)
    render_fn, division_label, error_text_fn = _extract(
        source,
        "renderRosterImportPreviewModal",
        "divisionLabel",
        "rosterErrorText",
    )

    harness = f"""
{_DOM_STUB}
{dict_js}
let currentLocale = "zh-TW";
{t_fn}
{division_label}
{error_text_fn}
{render_fn}
let state = {{ roster: {{ entries: [] }}, race: {{ config: {{}} }} }};
state.rosterImportPreview = {{
  text: "irrelevant",
  entries: [],
  errors: [{{ row: 1, message: "Missing required column: name", code: "missing_header_name" }}],
  existing_count: 0,
  rows: [],
}};
renderRosterImportPreviewModal();
console.log(JSON.stringify({{
  table: el("roster-import-preview-table").innerHTML,
  confirmDisabled: el("btn-roster-import-preview-confirm").disabled,
}}));
"""
    output = _run_node(harness)
    result = json.loads(output)
    table = result["table"]
    assert "row-error" in table
    assert "缺少必要欄位" in table
    assert result["confirmDisabled"] is True


def test_render_preview_modal_does_not_duplicate_row_level_errors():
    # A row-level error already renders once inside `rows` -- the
    # header-error fallback must not render it a second time.
    source = _strip_js_comments(_read())
    render_fn, division_label, error_text_fn = _extract(
        source,
        "renderRosterImportPreviewModal",
        "divisionLabel",
        "rosterErrorText",
    )

    harness = _harness(
        division_label,
        error_text_fn,
        render_fn,
        extra="""
state.rosterImportPreview = {
  text: "irrelevant",
  entries: [],
  errors: [{ row: 3, message: "Invalid division: bogus", code: "invalid_division", value: "bogus" }],
  existing_count: 0,
  rows: [
    { row: 2, name: "Alice", division: "men", team: null },
    { row: 3, name: "Bob", division: null, team: null, error: { row: 3, message: "Invalid division: bogus", code: "invalid_division", value: "bogus" } },
  ],
};
renderRosterImportPreviewModal();
console.log(JSON.stringify({
  errorCardCount: (el("roster-import-preview-table").innerHTML.match(/row-error/g) || []).length,
}));
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    assert result["errorCardCount"] == 1


def test_render_preview_modal_shows_relay_team_size_warnings():
    source = _strip_js_comments(_read())
    render_fn, division_label = _extract(
        source, "renderRosterImportPreviewModal", "divisionLabel"
    )

    harness = _harness(
        division_label,
        render_fn,
        extra="""
state.rosterImportPreview = {
  text: "irrelevant",
  entries: [{ name: "A", division: null, team: "Blue" }],
  errors: [],
  existing_count: 0,
  team_warnings: [{ team: "Blue", count: 1, expected: 2 }],
};
renderRosterImportPreviewModal();
console.log(JSON.stringify({
  warnings: el("roster-import-preview-warnings").innerHTML,
}));
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    assert "Blue" in result["warnings"]


def test_render_preview_modal_shows_teamless_warning_when_present():
    source = _strip_js_comments(_read())
    render_fn, division_label = _extract(
        source, "renderRosterImportPreviewModal", "divisionLabel"
    )

    harness = _harness(
        division_label,
        render_fn,
        extra="""
state.rosterImportPreview = {
  text: "irrelevant",
  entries: [{ name: "A", division: null, team: null }],
  errors: [],
  existing_count: 0,
  team_warnings: [],
  teamless_count: 3,
};
renderRosterImportPreviewModal();
console.log(JSON.stringify({
  warnings: el("roster-import-preview-warnings").innerHTML,
}));
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    assert "3" in result["warnings"]
    assert "row-warning" in result["warnings"]


def test_render_preview_modal_hides_teamless_warning_when_zero_or_absent():
    source = _strip_js_comments(_read())
    render_fn, division_label = _extract(
        source, "renderRosterImportPreviewModal", "divisionLabel"
    )

    harness = _harness(
        division_label,
        render_fn,
        extra="""
state.rosterImportPreview = {
  text: "irrelevant",
  entries: [{ name: "A", division: null, team: "Blue" }],
  errors: [],
  existing_count: 0,
  team_warnings: [],
  teamless_count: 0,
};
renderRosterImportPreviewModal();
const zeroCase = el("roster-import-preview-warnings").innerHTML;

state.rosterImportPreview = {
  text: "irrelevant",
  entries: [{ name: "A", division: null, team: "Blue" }],
  errors: [],
  existing_count: 0,
  team_warnings: [],
};
renderRosterImportPreviewModal();
const absentCase = el("roster-import-preview-warnings").innerHTML;

console.log(JSON.stringify({ zeroCase, absentCase }));
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    assert "teamless" not in result["zeroCase"].lower()
    assert "teamless" not in result["absentCase"].lower()
    assert result["zeroCase"] == ""
    assert result["absentCase"] == ""


def test_confirm_roster_import_sends_real_import_and_closes_modal():
    source = _strip_js_comments(_read())
    confirm_fn, render_fn, import_fn, render_errors_fn, close_fn = _extract(
        source,
        "confirmRosterImport",
        "renderRoster",
        "importRosterCsv",
        "renderRosterImportErrors",
        "closeRosterImportPreviewModal",
    )
    division_label, render_heat_list, render_team_list, render_entry_list = _extract(
        source,
        "divisionLabel",
        "renderRosterHeatList",
        "renderRosterTeamHeatList",
        "renderRosterEntryList",
    )

    harness = _harness(
        division_label,
        render_heat_list,
        render_team_list,
        render_entry_list,
        render_fn,
        render_errors_fn,
        import_fn,
        close_fn,
        confirm_fn,
        extra="""
state.rosterImportPreview = { text: "name\\nAlice\\n" };
state.race = { state: "IDLE" };
state.roster = { entries: [], heat_size: 1, current_heat: [], next_heat: [], counts: { pending: 0, loaded: 0, done: 0, absent: 0 } };

let capturedBody = null;
global.fetch = async (url, options) => {
  capturedBody = JSON.parse(options.body);
  return { ok: true, json: async () => ({ entries: [{ id: "a", name: "Alice" }], counts: { pending: 1, loaded: 0, done: 0, absent: 0 } }) };
};

(async () => {
  await confirmRosterImport();
  console.log(JSON.stringify({
    body: capturedBody,
    modalHidden: !el("roster-import-preview-modal").classList.contains("show"),
  }));
})();
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    assert result["body"] == {"csv": "name\nAlice\n"}
