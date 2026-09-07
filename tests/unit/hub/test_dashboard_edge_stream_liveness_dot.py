"""Regression test for `renderEdgeNodes()` in hub_server/static/index.html.

Bug (verified live on the venue hub): the edge node's own firmware hardcodes
`stream.status` to the literal string "configured" forever (see
edge_node/main.py:395 -- not touched by this change), so a stream pill on
the dashboard shows "configured" whether the machine is actively streaming
telemetry or has been powered off for an hour. The truthful liveness signal
is `stream.last_telemetry_epoch_ms`, which `renderEdges()` in
hub_server/static/systemAdmin.html already uses for its own "connected /
idle / no data" badge (a 15000ms freshness window).

Fix: each `.stream-pill` must render a small status dot -- green when
`last_telemetry_epoch_ms` is truthy and less than 15000ms old, grey
otherwise (including null/undefined/0, i.e. "never sent data" must never
show green) -- and the pill's status text must become
`t("connection.online")` / `t("connection.offline")` instead of echoing
the raw (always-"configured") `stream.status` field.

This module extracts the real `renderEdgeNodes` function body out of
index.html's inline <script> (comments stripped, brace-depth matched -- the
same technique as `_matching_brace_end` in
tests/unit/hub/test_dashboard_station_machine_name.py) and executes it
under `node` with stubs for `t`, `escapeHtml`, `metricNumber`,
`formatRelativeAge` and `document.getElementById`. It asserts on the actual
rendered HTML string, not on page source text, per the CLAUDE.md warning
that a source-text/comment match can stay green even after the real
behaviour is deleted.
"""

import re
import subprocess
import tempfile
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _matching_brace_end(source: str, open_idx: int) -> int:
    """Return the index of the "}" that matches the "{" at open_idx,
    tracking string literals so braces inside quoted values don't throw off
    the depth count. Mirrors tests/unit/hub/test_dashboard_station_machine_name.py."""
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


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _extract_render_edge_nodes() -> str:
    source = _read_index()
    return _strip_js_comments(_extract_function(source, "renderEdgeNodes"))


_HARNESS_PREFIX = """
let edgeNodes;
const captured = {};
function makeEl() {
  return {
    _innerHTML: "",
    _innerText: "",
    get innerHTML() { return this._innerHTML; },
    set innerHTML(v) { this._innerHTML = v; },
    get innerText() { return this._innerText; },
    set innerText(v) { this._innerText = v; },
  };
}
const elements = {
  "edge-node-list": makeEl(),
  "edge-node-summary": makeEl(),
  "edge-node-caption": makeEl(),
};
const document = {
  getElementById(id) { return elements[id] || null; },
};
function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
function metricNumber(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}
function formatRelativeAge() { return "just now"; }
function t(key, params) {
  const table = {
    "connection.online": "STUB_ONLINE",
    "connection.offline": "STUB_OFFLINE",
  };
  if (Object.prototype.hasOwnProperty.call(table, key)) return table[key];
  return key + (params ? ":" + JSON.stringify(params) : "");
}
"""


def _run_node(js_source: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(js_source)
        tmp_file.flush()
        tmp_path = tmp_file.name
    try:
        result = subprocess.run(
            ["node", tmp_path], capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0, f"node failed: {result.stderr}"
        return result.stdout
    finally:
        Path(tmp_path).unlink()


def _render_edge_node_list_html() -> str:
    fn_body = _extract_render_edge_nodes()
    harness = f"""
{_HARNESS_PREFIX}
{fn_body}

const nodes = [
  {{
    edge_node_id: "edge-01",
    display_name: "Edge 01",
    status: "online",
    ip: "10.0.0.5",
    last_seen_epoch_ms: Date.now(),
    equipment_streams: [
      {{
        display_name: "Fresh Rower",
        status: "configured",
        antenna_channel: "uart-1",
        last_telemetry_epoch_ms: Date.now() - 1000,
      }},
      {{
        display_name: "Stale Bike",
        status: "configured",
        antenna_channel: "uart-2",
        last_telemetry_epoch_ms: Date.now() - 60000,
      }},
      {{
        display_name: "Never Sent",
        status: "configured",
        last_telemetry_epoch_ms: null,
      }},
    ],
  }},
];

renderEdgeNodes(nodes);
console.log(elements["edge-node-list"].innerHTML);
"""
    return _run_node(harness)


def _pill_block(html: str, needle: str) -> str:
    """Return the single <span class="stream-pill" ...>...</span> block
    whose content contains `needle`, so assertions target the right pill
    instead of the whole rendered card."""
    for match in re.finditer(
        r'<span class="stream-pill"[^>]*>.*?</span>\s*</span>', html, re.DOTALL
    ):
        if needle in match.group(0):
            return match.group(0)
    raise AssertionError(f"no stream-pill block containing {needle!r} found in: {html}")


def test_render_edge_nodes_is_actually_defined():
    body = _extract_render_edge_nodes()
    assert "function renderEdgeNodes(nodes)" in body


def test_fresh_stream_renders_online_dot_and_text_not_configured():
    html = _render_edge_node_list_html()
    pill = _pill_block(html, "Fresh Rower")
    assert "online" in pill
    assert "STUB_ONLINE" in pill
    assert "STUB_OFFLINE" not in pill


def test_stale_stream_renders_offline_dot_and_text():
    html = _render_edge_node_list_html()
    pill = _pill_block(html, "Stale Bike")
    assert "STUB_OFFLINE" in pill
    assert "STUB_ONLINE" not in pill
    # The offline dot must not carry the "online" modifier class.
    dot_match = re.search(r'<span class="node-status-dot[^"]*"', pill)
    assert dot_match, f"no status dot found in pill: {pill}"
    assert "online" not in dot_match.group(0)


def test_never_sent_data_stream_renders_offline_dot_and_text():
    html = _render_edge_node_list_html()
    pill = _pill_block(html, "Never Sent")
    assert "STUB_OFFLINE" in pill
    assert "STUB_ONLINE" not in pill
    dot_match = re.search(r'<span class="node-status-dot[^"]*"', pill)
    assert dot_match, f"no status dot found in pill: {pill}"
    assert "online" not in dot_match.group(0)


def test_raw_configured_status_never_echoed_into_output():
    """Proves the raw stream.status value (which the edge node hardcodes to
    "configured" forever) is no longer echoed into the pill text -- every
    stub stream here sets status: "configured", so this string must not
    survive into the rendered output at all."""
    html = _render_edge_node_list_html()
    assert "configured" not in html


def test_antenna_channel_suffix_still_shown_for_streams_that_have_one():
    html = _render_edge_node_list_html()
    fresh_pill = _pill_block(html, "Fresh Rower")
    assert "uart-1" in fresh_pill
    stale_pill = _pill_block(html, "Stale Bike")
    assert "uart-2" in stale_pill
