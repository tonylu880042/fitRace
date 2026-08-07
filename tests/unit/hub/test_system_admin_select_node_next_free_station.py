"""Regression test for selectNode() in Station Assignment
(hub_server/static/systemAdmin.html).

Bug: clicking the unassigned-stream row's "Use in form above" (arrow) button
only set the stream dropdown:

    function selectNode(nodeId) {
      $("node-select").value = nodeId;
    }

The station-number input kept whatever value it had -- typically a station
number that is already assigned -- so the operator had to click "+"
repeatedly to reach a free slot.

Fix: selectNode() must also advance the station-number input to the
LOWEST currently-unassigned station number from 1 upward (filling gaps,
e.g. if 1, 2, 4 are assigned it picks 3, not 5), leaving the value
unchanged only if every station 1..99 is already assigned. Since it now
touches the station number, it must call updateSignupLink() too (like
stepStation() and selectStation() already do), or the displayed
signup URL keeps pointing at the previous station -- a wrong QR/link
handed to an athlete.

This module extracts the real nextFreeStationNumber()/selectNode() source
out of the page's inline <script> (comments stripped) and executes it
under node with a stubbed $()/updateSignupLink()/state, per the technique
established in tests/unit/hub/test_game_admin_race_action_buttons.py. A
test that only pattern-matches the source text cannot catch a wrong
"next free" calculation or a missing updateSignupLink() call -- it has to
actually run the extracted code and inspect the resulting DOM state.

The harness intentionally does NOT wrap execution in try/except: if a
stub is missing and the extracted code throws before reaching the
assignment, node exits non-zero and _run_node's assertion fails loudly,
instead of silently making every case look identical.
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


def _read() -> str:
    return (STATIC_DIR / "systemAdmin.html").read_text(encoding="utf-8")


def _stripped_script() -> str:
    source = _read()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _extract_next_free_and_select_node() -> str:
    """nextFreeStationNumber() and selectNode(), from a comment-stripped
    script, up to (not including) the next function (selectStation).
    Grabbing the real function bodies means a mutation of the free-slot
    search or a dropped updateSignupLink() call is exercised, not just
    pattern-matched."""
    script = _stripped_script()
    start = script.index("function nextFreeStationNumber")
    end = script.index("function selectStation", start)
    return script[start:end]


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


_DOM_HARNESS_PREFIX = """
function makeEl(initial) {
  // Real <input> elements always coerce assigned values to strings, the
  // way the browser DOM does -- mirror that here so a number written by
  // the extracted code reads back the same way it would on the real page.
  let raw = initial;
  return {
    get value() { return raw; },
    set value(v) { raw = String(v); },
  };
}
const mockElements = {
  "node-select": makeEl(""),
  "station-number": makeEl("50"),
};
function $(id) {
  if (!mockElements[id]) mockElements[id] = makeEl("");
  return mockElements[id];
}
let updateSignupLinkCalls = 0;
function updateSignupLink() { updateSignupLinkCalls += 1; }
const state = { stations: { stations: {} } };
"""


def _select_node(
    assigned_station_numbers, initial_station_value="50", node_id="edge-01:rower"
):
    body = _extract_next_free_and_select_node()
    stations_obj = {str(n): {"node_id": f"stale-{n}"} for n in assigned_station_numbers}
    harness = f"""
{_DOM_HARNESS_PREFIX}
{body}

mockElements["station-number"].value = "{initial_station_value}";
state.stations.stations = {json.dumps(stations_obj)};

selectNode({json.dumps(node_id)});

console.log(JSON.stringify({{
  station_value: mockElements["station-number"].value,
  node_value: mockElements["node-select"].value,
  update_signup_link_calls: updateSignupLinkCalls,
}}));
"""
    output = _run_node(harness)
    return json.loads(output.strip().splitlines()[-1])


def test_next_free_and_select_node_are_actually_defined():
    body = _extract_next_free_and_select_node()
    assert "function nextFreeStationNumber" in body
    assert "function selectNode(nodeId)" in body


# ---------------------------------------------------------------------------
# Coverage required by the spec, plus proof the harness really reaches the
# assignment: the four cases below must produce four DIFFERENT station
# values (1, 6, 3, unchanged-at-50), not the same fallback value.
# ---------------------------------------------------------------------------


def test_no_stations_assigned_picks_1():
    result = _select_node(assigned_station_numbers=[])
    assert result["station_value"] == "1"
    assert result["node_value"] == "edge-01:rower"
    assert result["update_signup_link_calls"] >= 1


def test_contiguous_1_to_5_assigned_picks_6():
    result = _select_node(assigned_station_numbers=[1, 2, 3, 4, 5])
    assert result["station_value"] == "6"
    assert result["update_signup_link_calls"] >= 1


def test_gap_at_3_picks_3_not_5():
    result = _select_node(assigned_station_numbers=[1, 2, 4])
    assert result["station_value"] == "3"
    assert result["update_signup_link_calls"] >= 1


def test_all_1_to_99_assigned_leaves_value_unchanged():
    result = _select_node(
        assigned_station_numbers=list(range(1, 100)), initial_station_value="42"
    )
    assert result["station_value"] == "42"
    assert result["update_signup_link_calls"] >= 1


def test_results_differ_across_inputs_proving_harness_reaches_assignment():
    """Guards against the documented trap: if a missing stub caused the
    extracted code to throw before reaching the assignment line and that
    were silently swallowed, every case below would collapse to the same
    (wrong) value. They must not."""
    values = {
        _select_node(assigned_station_numbers=[])["station_value"],
        _select_node(assigned_station_numbers=[1, 2, 3, 4, 5])["station_value"],
        _select_node(assigned_station_numbers=[1, 2, 4])["station_value"],
        _select_node(
            assigned_station_numbers=list(range(1, 100)), initial_station_value="42"
        )["station_value"],
    }
    assert values == {"1", "6", "3", "42"}


def test_selecting_node_still_sets_node_select():
    result = _select_node(assigned_station_numbers=[1, 2, 4], node_id="edge-02:bike")
    assert result["node_value"] == "edge-02:bike"
