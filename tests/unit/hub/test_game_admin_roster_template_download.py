"""Game Admin's "Download Roster Template" button (hub_server/static/
gameAdmin.html): downloadRosterTemplate() must pick mode/legs from the
CURRENT race config (relay when competition_mode == "relay", using its
relay_legs) and hit GET /api/roster/template.csv with admin headers and the
current locale mapped to the "zh-TW"/"en" the backend understands.

This executes the REAL downloadRosterTemplate() pulled out of the page via
brace-depth extraction under node -- never a source-text grep -- so deleting
the real wiring turns this red instead of being satisfied by a nearby
comment.
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
    elements[id] = { id, textContent: "", innerHTML: "" };
  }
  return elements[id];
}
function $(id) { return el(id); }
function adminHeaders(extra = {}) { return { ...extra, "X-FitRace-Admin-Token": "secret" }; }
function setMessage(id, text, kind) { el(id).textContent = text; }
function t(key) { return key; }
class FakeBlob {}
global.Blob = FakeBlob;
global.URL = { createObjectURL: () => "blob:stub", revokeObjectURL: () => {} };
const anchors = [];
global.document = {
  createElement: () => {
    const a = { click() {}, remove() {} };
    anchors.push(a);
    return a;
  },
  body: { appendChild() {} },
};
"""


def _run(state_js: str, current_locale: str) -> dict:
    source = _strip_js_comments(_read())
    download_fn = _extract_function(source, "downloadRosterTemplate")
    parse_filename_fn = _extract_function(source, "parseAttachmentFilename")
    assert "template.csv" in download_fn  # sanity: real source

    harness = f"""
{_DOM_STUB}
{parse_filename_fn}
let currentLocale = "{current_locale}";
let state = {state_js};

let capturedUrl = null;
global.fetch = async (url, options) => {{
  capturedUrl = url;
  return {{
    ok: true,
    headers: {{ get: () => 'attachment; filename="roster-template.csv"' }},
    blob: async () => new Blob(),
  }};
}};

{download_fn}

(async () => {{
  await downloadRosterTemplate();
  console.log(JSON.stringify({{ url: capturedUrl }}));
}})();
"""
    output = _run_node(harness)
    return json.loads(output)


def test_download_template_uses_relay_mode_and_legs_from_config():
    result = _run(
        """{ race: { config: { competition_mode: "relay", relay_legs: 3 } } }""",
        "zh-TW",
    )
    assert "mode=relay" in result["url"]
    assert "legs=3" in result["url"]
    assert "lang=zh-TW" in result["url"]


def test_download_template_uses_individual_mode_when_not_relay():
    result = _run(
        """{ race: { config: { competition_mode: "individual" } } }""",
        "en-US",
    )
    assert "mode=individual" in result["url"]
    assert "lang=en" in result["url"]
