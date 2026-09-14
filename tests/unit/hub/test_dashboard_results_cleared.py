"""Test dashboard WebSocket handler for results_cleared message type.

This executes the REAL, unmodified ws.onmessage handler pulled out of
index.html's inline <script> via brace-depth matching, under `node` with
minimal DOM/state stubs -- never a source-text grep -- so deleting the real
wiring turns this red instead of being satisfied by a nearby comment.
"""

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
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


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


def test_dashboard_calls_refresh_record_wall_on_results_cleared_message():
    """WebSocket handler calls refreshRecordWallData when results_cleared message arrives."""
    source = _strip_js_comments(_read())

    # Find the ws.onmessage assignment: "ws.onmessage = (event) => {"
    marker = "ws.onmessage = (event) => {"
    if marker not in source:
        raise AssertionError(
            f"Could not find ws.onmessage handler. Looked for: {marker}"
        )

    # Find start of assignment
    assign_start = source.index(marker)

    # Find the opening brace of the handler body
    brace_open = source.index("{", assign_start)

    # Find the closing brace using brace matching
    brace_end = _matching_brace_end(source, brace_open)

    # Extract the entire assignment from "ws.onmessage" to the closing brace
    handler_assignment = source[assign_start : brace_end + 1]

    # Verify the real handler contains our wiring
    assert "results_cleared" in handler_assignment
    assert "refreshRecordWallData" in handler_assignment
    assert "recordWallActive" in handler_assignment

    harness = f"""
{_DOM_STUB}

// Track whether refreshRecordWallData was called
let refreshRecordWallDataCallCount = 0;

function refreshRecordWallData() {{
  refreshRecordWallDataCallCount++;
}}

// State variables that the handler depends on
let recordWallActive = true;

// Real handler assignment extracted from index.html
const ws = {{}};
{handler_assignment}

// Test 1: recordWallActive = true, should call refreshRecordWallData
refreshRecordWallDataCallCount = 0;
const event1 = {{
  data: JSON.stringify({{type: "results_cleared"}})
}};
ws.onmessage(event1);
console.log(refreshRecordWallDataCallCount);

// Test 2: recordWallActive = false, should NOT call refreshRecordWallData
recordWallActive = false;
refreshRecordWallDataCallCount = 0;
const event2 = {{
  data: JSON.stringify({{type: "results_cleared"}})
}};
ws.onmessage(event2);
console.log(refreshRecordWallDataCallCount);
"""

    result = _run_node(harness)
    lines = result.split("\n")
    assert len(lines) >= 2, f"Expected 2 output lines, got: {result}"

    call_count_active = int(lines[0])
    call_count_inactive = int(lines[1])

    assert (
        call_count_active == 1
    ), f"With recordWallActive=true, refreshRecordWallData should be called once, got {call_count_active}"
    assert (
        call_count_inactive == 0
    ), f"With recordWallActive=false, refreshRecordWallData should not be called, got {call_count_inactive}"
