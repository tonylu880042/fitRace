"""Game Admin gains a "Download Results (CSV)" action (hub_server/static/
gameAdmin.html) that fetches GET /api/results/export.csv with the page's
admin header and saves the response as a file.

This executes the REAL, unmodified downloadResultsCsv() pulled out of
gameAdmin.html's inline <script> via brace-depth matching (the same
extraction technique as test_game_admin_roster_panel.py /
test_signup_division.py) under `node` with minimal fetch/DOM stubs -- never
a source-text grep -- so deleting the real wiring turns this red instead of
being satisfied by a nearby comment.
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


_STUB_PREAMBLE = """
let currentLocale = "zh-TW";
function adminHeaders(extra = {}) { return { ...extra, "X-FitRace-Admin-Token": "secret" }; }
function t(key, params = {}) {
  const paramText = Object.keys(params).length ? ` ${JSON.stringify(params)}` : "";
  return `${key}${paramText}`;
}
let lastMessage = null;
function setMessage(id, text, kind) { lastMessage = { id, text, kind }; }
let openLoginCalled = false;
function openLogin() { openLoginCalled = true; }

const createObjectURLCalls = [];
const revokeObjectURLCalls = [];
const URL = {
  createObjectURL(blob) { createObjectURLCalls.push(blob); return "blob:mock-url"; },
  revokeObjectURL(url) { revokeObjectURLCalls.push(url); },
};
const appendedElements = [];
const clickedAnchors = [];
const document = {
  createElement(tag) {
    const element = {
      tag,
      href: null,
      download: null,
      click() { clickedAnchors.push(element); },
      remove() {},
    };
    return element;
  },
  body: { appendChild(element) { appendedElements.push(element); } },
};
"""


def test_download_results_csv_sends_admin_header_and_clicks_named_anchor():
    source = _strip_js_comments(_read())
    download_fn = _extract_function(source, "downloadResultsCsv")
    parse_filename_fn = _extract_function(source, "parseAttachmentFilename")
    assert "export.csv" in download_fn  # sanity: real source, not a stub
    assert "adminHeaders" in download_fn

    harness = f"""
{_STUB_PREAMBLE}

let capturedUrl = null;
let capturedOptions = null;
global.fetch = async (url, options) => {{
  capturedUrl = url;
  capturedOptions = options;
  return {{
    ok: true,
    status: 200,
    statusText: "OK",
    headers: {{ get: (name) => name === "Content-Disposition" ? 'attachment; filename="fitrace-results-20240101-1200.csv"' : null }},
    blob: async () => ({{ __isBlob: true }}),
  }};
}};

{parse_filename_fn}
{download_fn}

(async () => {{
  await downloadResultsCsv();
  console.log(JSON.stringify({{
    url: capturedUrl,
    headers: capturedOptions.headers,
    anchorCount: clickedAnchors.length,
    anchorDownload: clickedAnchors[0] ? clickedAnchors[0].download : null,
    anchorHref: clickedAnchors[0] ? clickedAnchors[0].href : null,
    appendedCount: appendedElements.length,
    objectUrlCreated: createObjectURLCalls.length,
    objectUrlRevoked: revokeObjectURLCalls.length,
    openLoginCalled,
    lastMessage,
  }}));
}})();
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["url"].startswith("/api/results/export.csv")
    assert result["headers"]["X-FitRace-Admin-Token"] == "secret"
    assert result["anchorCount"] == 1
    assert result["anchorDownload"] == "fitrace-results-20240101-1200.csv"
    assert result["anchorHref"] == "blob:mock-url"
    assert result["appendedCount"] == 1
    assert result["objectUrlCreated"] == 1
    assert result["objectUrlRevoked"] == 1
    assert result["openLoginCalled"] is False
    assert result["lastMessage"]["kind"] == "ok"


def test_download_results_csv_declines_gracefully_on_401():
    source = _strip_js_comments(_read())
    download_fn = _extract_function(source, "downloadResultsCsv")
    parse_filename_fn = _extract_function(source, "parseAttachmentFilename")

    harness = f"""
{_STUB_PREAMBLE}

global.fetch = async () => ({{
  ok: false,
  status: 401,
  statusText: "Unauthorized",
  headers: {{ get: () => null }},
  json: async () => ({{ detail: "Admin token required" }}),
}});

{parse_filename_fn}
{download_fn}

(async () => {{
  await downloadResultsCsv();
  console.log(JSON.stringify({{
    anchorCount: clickedAnchors.length,
    objectUrlCreated: createObjectURLCalls.length,
    openLoginCalled,
    lastMessage,
  }}));
}})();
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["anchorCount"] == 0
    assert result["objectUrlCreated"] == 0
    assert result["openLoginCalled"] is True
    assert result["lastMessage"]["kind"] == "error"
