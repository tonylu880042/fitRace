"""Post-race "next step" hint in Game Admin (hub_server/static/
gameAdmin.html): once a heat's results are saved (race state STOPPED),
`#post-race-hint` (directly above the roster action bar) tells the operator
what to do next -- load the next heat, or that the roster is fully raced, or
just that results were saved when there is no roster at all. Any other race
state clears the hint.

This executes the REAL `renderRoster()` (which already reads both
`state.race.state` and `state.roster`) pulled out of the page via brace-depth
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
function t(key, params = {}) {
  const table = {
    "text.postrace_hint_next_heat": "Heat results saved. Next: press Next Heat Up.",
    "text.postrace_hint_roster_done": "Heat results saved. Everyone on the roster has raced.",
    "text.postrace_hint_saved": "Heat results saved.",
  };
  return table[key] || key;
}
"""


def _render(source: str, state_js: str) -> dict:
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
  hidden: el("post-race-hint").hidden,
  text: el("post-race-hint").textContent,
  hasCalloutClass: el("post-race-hint").classList.contains("postrace-callout"),
}}));
"""
    output = _run_node(harness)
    return json.loads(output)


def _entry(status, name="A"):
    return {
        "id": name,
        "name": name,
        "division": None,
        "team": None,
        "status": status,
        "station_number": None,
    }


def test_postrace_hint_shown_when_stopped_with_pending_entries():
    source = _strip_js_comments(_read())
    result = _render(
        source,
        """{
      race: { state: "STOPPED" },
      roster: {
        entries: [{ id: "a", name: "A", status: "pending" }],
        heat_size: 1, current_heat: [], next_heat: [],
        counts: { pending: 1, loaded: 0, done: 0, absent: 0 },
      },
    }""",
    )
    assert result["hidden"] is False
    assert "Next Heat Up" in result["text"]
    assert result["hasCalloutClass"] is True


def test_postrace_hint_shown_when_stopped_with_roster_fully_raced():
    source = _strip_js_comments(_read())
    result = _render(
        source,
        """{
      race: { state: "STOPPED" },
      roster: {
        entries: [{ id: "a", name: "A", status: "done" }],
        heat_size: 1, current_heat: [], next_heat: [],
        counts: { pending: 0, loaded: 0, done: 1, absent: 0 },
      },
    }""",
    )
    assert result["hidden"] is False
    assert "Everyone on the roster has raced" in result["text"]
    assert result["hasCalloutClass"] is True


def test_postrace_hint_shown_when_stopped_with_no_roster():
    source = _strip_js_comments(_read())
    result = _render(
        source,
        """{
      race: { state: "STOPPED" },
      roster: {
        entries: [],
        heat_size: 1, current_heat: [], next_heat: [],
        counts: { pending: 0, loaded: 0, done: 0, absent: 0 },
      },
    }""",
    )
    assert result["hidden"] is False
    assert result["text"] == "Heat results saved."
    assert result["hasCalloutClass"] is True


def test_postrace_hint_hidden_when_not_stopped():
    source = _strip_js_comments(_read())
    result = _render(
        source,
        """{
      race: { state: "READY" },
      roster: {
        entries: [{ id: "a", name: "A", status: "pending" }],
        heat_size: 1, current_heat: [], next_heat: [],
        counts: { pending: 1, loaded: 0, done: 0, absent: 0 },
      },
    }""",
    )
    assert result["hidden"] is True
    assert result["text"] == ""
    assert result["hasCalloutClass"] is False
