"""A raced current heat still looked "current" in Game Admin (hub_server/
static/gameAdmin.html): once any loaded entry has started=true (the heat
has actually raced -- see RosterManager.mark_current_heat_started()), the
backend already rejects Cancel Current Heat with "current heat already
raced" (RosterManager.cancel_current_heat), so showing the button there
only produces an error. renderRoster() must (a) retitle
#current-heat-title to "Just Raced" instead of "Current Heat", and (b) hide
#btn-cancel-current-heat -- for a STILL-UNRACED loaded heat, both stay as
today. This is display-only; roster.py's semantics are untouched.

This executes the REAL renderRoster() pulled out of the page via
brace-depth extraction under node -- never a source-text grep -- so
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
    const classes = new Set();
    elements[id] = {
      id,
      disabled: false,
      hidden: false,
      textContent: "",
      innerHTML: "",
      value: "",
      classList: {
        add(name) { classes.add(name); },
        remove(name) { classes.delete(name); },
        toggle(name, force) {
          const shouldHave = force === undefined ? !classes.has(name) : Boolean(force);
          if (shouldHave) classes.add(name); else classes.delete(name);
        },
        contains(name) { return classes.has(name); },
      },
    };
  }
  return elements[id];
}
function $(id) { return el(id); }
function escapeHtml(value) { return String(value ?? ""); }
function syncVisibility() {}
function t(key, params = {}) {
  const table = {
    "panel.current_heat": "Current Heat",
    "panel.current_heat_raced": "Just Raced",
  };
  const base = table[key] || key;
  const paramText = Object.keys(params).length ? ` ${JSON.stringify(params)}` : "";
  return `${base}${paramText}`;
}
"""


def _render(state_js: str) -> dict:
    source = _strip_js_comments(_read())
    render_roster = _extract_function(source, "renderRoster")
    division_label = _extract_function(source, "divisionLabel")
    render_heat_list = _extract_function(source, "renderRosterHeatList")
    render_team_list = _extract_function(source, "renderRosterTeamHeatList")
    render_entry_list = _extract_function(source, "renderRosterEntryList")

    harness = f"""
{_DOM_STUB}

{division_label}
{render_heat_list}
{render_team_list}
{render_entry_list}

let state = {state_js};

{render_roster}

renderRoster();
console.log(JSON.stringify({{
  title: el("current-heat-title").textContent,
  cancelHidden: el("btn-cancel-current-heat").hidden,
}}));
"""
    output = _run_node(harness)
    return json.loads(output)


def _entry(status, name="A", started=False):
    return {
        "id": name,
        "name": name,
        "division": None,
        "team": None,
        "status": status,
        "station_number": 1 if status == "loaded" else None,
        "started": started,
    }


def test_raced_heat_shows_just_raced_title_and_hides_cancel_button():
    result = _render("""{
      race: { state: "STOPPED" },
      roster: {
        entries: [%s],
        heat_size: 1, current_heat: [], next_heat: [],
        counts: { pending: 0, loaded: 1, done: 0, absent: 0 },
      },
    }""" % json.dumps(_entry("loaded", started=True)))
    assert result["title"] == "Just Raced"
    assert result["cancelHidden"] is True


def test_unraced_loaded_heat_keeps_current_heat_title_and_shows_cancel_button():
    result = _render("""{
      race: { state: "READY" },
      roster: {
        entries: [%s],
        heat_size: 1, current_heat: [], next_heat: [],
        counts: { pending: 0, loaded: 1, done: 0, absent: 0 },
      },
    }""" % json.dumps(_entry("loaded", started=False)))
    assert result["title"] == "Current Heat"
    assert result["cancelHidden"] is False


def test_no_current_heat_keeps_current_heat_title_and_hides_cancel_button():
    result = _render("""{
      race: { state: "IDLE" },
      roster: {
        entries: [],
        heat_size: 1, current_heat: [], next_heat: [],
        counts: { pending: 0, loaded: 0, done: 0, absent: 0 },
      },
    }""")
    assert result["title"] == "Current Heat"
    assert result["cancelHidden"] is True
