"""Regression test for `selectStation(stationNumber, nodeId)` in
hub_server/static/systemAdmin.html.

Bug (verified live on the venue hub): the pencil (Edit) button on an
assigned-station row loads that row's station number/node into the
assign-form fields above the table, but gives zero visible feedback. If
the clicked row's values already match the form's current contents,
clicking produces no visible change at all, and an operator reasonably
concludes the button is broken.

Fix: selectStation() must additionally focus + select the station-number
input (so the operator's eye is drawn there even when the value didn't
change) and post a confirmation message via the existing setMessage()
helper, using the new i18n key "message.station_loaded".

This module extracts the real selectStation() function body out of
systemAdmin.html's inline <script> (brace-depth matched, comments
stripped -- the same technique as
tests/unit/hub/test_system_admin_select_node_next_free_station.py and
tests/unit/hub/test_dashboard_station_machine_name.py) and executes it
under `node` with stubs for `$`, `setMessage`, `updateSignupLink`, and `t`.
It asserts on what the extracted code actually DID (recorded focus/select
calls, the exact setMessage args, whether updateSignupLink ran, and the
node-select value) -- not on page source text, per the CLAUDE.md warning
that a source-text/comment match can stay green even after the real
behaviour is deleted.
"""

import json
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


def _read_system_admin() -> str:
    return (STATIC_DIR / "systemAdmin.html").read_text(encoding="utf-8")


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


def _extract_select_station() -> str:
    source = _read_system_admin()
    return _strip_js_comments(_extract_function(source, "selectStation"))


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


_HARNESS_PREFIX = """
function makeEl(initial) {
  let raw = initial;
  const calls = { focus: 0, select: 0 };
  return {
    get value() { return raw; },
    set value(v) { raw = String(v); },
    focus() { calls.focus += 1; },
    select() { calls.select += 1; },
    _calls: calls,
  };
}
const mockElements = {
  "node-select": makeEl("sentinel-node"),
  "station-number": makeEl("50"),
};
function $(id) {
  if (!mockElements[id]) mockElements[id] = makeEl("");
  return mockElements[id];
}
let updateSignupLinkCalls = 0;
function updateSignupLink() { updateSignupLinkCalls += 1; }
const setMessageCalls = [];
function setMessage(id, message, level) {
  setMessageCalls.push({ id, message, level });
}
function t(key, params) {
  const table = { "message.station_loaded": "Station {stationNumber} loaded" };
  let value = table[key] || key;
  Object.entries(params || {}).forEach(([name, replacement]) => {
    value = value.replaceAll(`{${name}}`, replacement);
  });
  return value;
}
"""


def _select_station(station_number, node_id):
    body = _extract_select_station()
    harness = f"""
{_HARNESS_PREFIX}
{body}

selectStation({json.dumps(station_number)}, {json.dumps(node_id)});

console.log(JSON.stringify({{
  station_focus_calls: mockElements["station-number"]._calls.focus,
  station_select_calls: mockElements["station-number"]._calls.select,
  node_value: mockElements["node-select"].value,
  station_value: mockElements["station-number"].value,
  update_signup_link_calls: updateSignupLinkCalls,
  set_message_calls: setMessageCalls,
}}));
"""
    output = _run_node(harness)
    return json.loads(output.strip().splitlines()[-1])


def test_select_station_is_actually_defined():
    body = _extract_select_station()
    assert "function selectStation(stationNumber, nodeId)" in body


def test_select_station_focuses_and_selects_station_input_and_shows_message():
    result = _select_station(5, "node-abc")
    assert result["station_focus_calls"] >= 1
    assert result["station_select_calls"] >= 1
    assert result["node_value"] == "node-abc"
    assert result["update_signup_link_calls"] >= 1

    assert len(result["set_message_calls"]) == 1
    call = result["set_message_calls"][0]
    assert call["id"] == "station-message"
    assert "5" in call["message"]
    assert call["level"] == "ok"


def test_select_station_with_falsy_node_id_leaves_dropdown_untouched():
    result = _select_station(7, "")
    assert result["node_value"] == "sentinel-node"
    # The rest of the feedback behaviour must still fire even when nodeId
    # is falsy -- only the dropdown assignment is guarded.
    assert result["station_focus_calls"] >= 1
    assert result["station_select_calls"] >= 1
    assert result["update_signup_link_calls"] >= 1
    assert len(result["set_message_calls"]) == 1
    assert "7" in result["set_message_calls"][0]["message"]
