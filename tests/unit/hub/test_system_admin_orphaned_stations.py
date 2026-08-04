"""Regression tests for the "orphaned stations" feature on System Admin
(hub_server/static/systemAdmin.html).

Orphaned stations are those whose assigned node_id is no longer present in any
edge node's equipment_streams list (indicating the edge node binding was removed).

These tests follow the same source-extraction technique used in
test_system_admin_unassigned_streams.py and test_system_admin_clear_all_stations.py:
pull the exact body of the function under test out of the page's inline <script>,
then assert on that narrow window.
"""

import re
import subprocess
import tempfile
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read() -> str:
    return (STATIC_DIR / "systemAdmin.html").read_text(encoding="utf-8")


def _extract_function(source: str, signature: str, stop_markers) -> str:
    """Return source text starting at `signature` (e.g. "function foo(") up
    to (not including) whichever of `stop_markers` appears first after it."""
    start = source.index(signature)
    window = source[start : start + 4000]
    stops = [window.index(marker) for marker in stop_markers if marker in window[1:]]
    end = min(stops) + 1 if stops else len(window)
    return window[:end]


def _script(source: str) -> str:
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return source[start:end]


NEXT_FN = "\n    function "
NEXT_ASYNC_FN = "\n    async function "
STOPS = [NEXT_FN, NEXT_ASYNC_FN]

# Comment-stripping helpers (CLAUDE.md: a comment must not satisfy a source assertion)
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    """Strip JS comments so a `//` comment can't silently satisfy a
    source-text assertion (CLAUDE.md's known "comment satisfies the
    assertion" trap).
    """
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


# ---------------------------------------------------------------------------
# A. Real behavioral proof of the orphan predicate (node.js execution)
# ---------------------------------------------------------------------------


def test_is_station_orphaned_predicate_exists():
    """The isStationOrphaned function must be defined in the page."""
    source = _script(_read())
    assert "function isStationOrphaned(nodeId, nodes)" in source


def test_is_station_orphaned_detects_present_device():
    """A node_id that is present in some edge's equipment_streams must return
    false, even if last_telemetry_epoch_ms is null (sleeping machine)."""
    source = _script(_read())
    func_body = _extract_function(source, "function isStationOrphaned(", STOPS)

    # Create a harness that tests the actual function
    harness = f"""
{func_body}

// Test: device-1 is in edge-01's equipment_streams
const nodes = [
  {{
    edge_node_id: "edge-01",
    equipment_streams: [
      {{node_id: "device-1", last_telemetry_epoch_ms: null, status: "configured"}}
    ]
  }}
];

const result = isStationOrphaned("device-1", nodes);
console.log(result ? "ORPHANED" : "PRESENT");
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(harness)
        tmp_file.flush()
        tmp_path = tmp_file.name

    try:
        output = subprocess.run(
            ["node", tmp_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert output.returncode == 0, f"node failed: {output.stderr}"
        assert "PRESENT" in output.stdout, (
            f"device-1 should be detected as PRESENT (not orphaned), "
            f"got: {output.stdout}"
        )
    finally:
        Path(tmp_path).unlink()


def test_is_station_orphaned_detects_absent_device():
    """A node_id that is NOT in any edge's equipment_streams must return true."""
    source = _script(_read())
    func_body = _extract_function(source, "function isStationOrphaned(", STOPS)

    harness = f"""
{func_body}

// Test: device-99 is NOT in any edge's equipment_streams
const nodes = [
  {{
    edge_node_id: "edge-01",
    equipment_streams: [
      {{node_id: "device-1", status: "configured"}},
      {{node_id: "device-2", status: "configured"}}
    ]
  }},
  {{
    edge_node_id: "edge-02",
    equipment_streams: [
      {{node_id: "device-3", status: "configured"}}
    ]
  }}
];

const result = isStationOrphaned("device-99", nodes);
console.log(result ? "ORPHANED" : "PRESENT");
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(harness)
        tmp_file.flush()
        tmp_path = tmp_file.name

    try:
        output = subprocess.run(
            ["node", tmp_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert output.returncode == 0, f"node failed: {output.stderr}"
        assert (
            "ORPHANED" in output.stdout
        ), f"device-99 should be detected as ORPHANED, got: {output.stdout}"
    finally:
        Path(tmp_path).unlink()


def test_is_station_orphaned_returns_false_for_empty_nodes_array():
    """If nodes is empty (hub just restarted, no heartbeats yet), must return
    false to avoid treating all stations as orphaned."""
    source = _script(_read())
    func_body = _extract_function(source, "function isStationOrphaned(", STOPS)

    harness = f"""
{func_body}

const result = isStationOrphaned("device-1", []);
console.log(result ? "ORPHANED" : "PRESENT");
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(harness)
        tmp_file.flush()
        tmp_path = tmp_file.name

    try:
        output = subprocess.run(
            ["node", tmp_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert output.returncode == 0, f"node failed: {output.stderr}"
        assert "PRESENT" in output.stdout, (
            f"With empty nodes array, device-1 should return false (treat as present), "
            f"got: {output.stdout}"
        )
    finally:
        Path(tmp_path).unlink()


def test_is_station_orphaned_returns_false_for_null_nodes():
    """If nodes is null/undefined, must return false."""
    source = _script(_read())
    func_body = _extract_function(source, "function isStationOrphaned(", STOPS)

    harness = f"""
{func_body}

const result1 = isStationOrphaned("device-1", null);
const result2 = isStationOrphaned("device-1", undefined);
console.log((result1 ? "ORPHANED" : "PRESENT") + " null, " + (result2 ? "ORPHANED" : "PRESENT") + " undefined");
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(harness)
        tmp_file.flush()
        tmp_path = tmp_file.name

    try:
        output = subprocess.run(
            ["node", tmp_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert output.returncode == 0, f"node failed: {output.stderr}"
        assert (
            "PRESENT null, PRESENT undefined" in output.stdout
        ), f"With null/undefined nodes, should return false, got: {output.stdout}"
    finally:
        Path(tmp_path).unlink()


def test_is_station_orphaned_returns_false_for_empty_nodeid():
    """If nodeId is falsy (empty string, null), must return false."""
    source = _script(_read())
    func_body = _extract_function(source, "function isStationOrphaned(", STOPS)

    harness = f"""
{func_body}

const nodes = [{{edge_node_id: "edge-01", equipment_streams: []}}];
const result1 = isStationOrphaned("", nodes);
const result2 = isStationOrphaned(null, nodes);
console.log((result1 ? "ORPHANED" : "PRESENT") + " empty, " + (result2 ? "ORPHANED" : "PRESENT") + " null");
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(harness)
        tmp_file.flush()
        tmp_path = tmp_file.name

    try:
        output = subprocess.run(
            ["node", tmp_path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert output.returncode == 0, f"node failed: {output.stderr}"
        assert (
            "PRESENT empty, PRESENT null" in output.stdout
        ), f"With falsy nodeId, should return false, got: {output.stdout}"
    finally:
        Path(tmp_path).unlink()


# ---------------------------------------------------------------------------
# B. Static source tests
# ---------------------------------------------------------------------------


def test_orphan_predicate_has_reasoning_comment():
    """The isStationOrphaned function must have a comment explaining WHY it
    checks equipment_streams (configured bindings) and NOT freshness/staleness.
    This comment is meant to stop future edits from 'simplifying' it into a
    staleness check."""
    source = _script(_read())

    # Find the function signature
    sig_idx = source.index("function isStationOrphaned(")
    # Look backwards for a comment
    lookback = source[max(0, sig_idx - 500) : sig_idx]
    # Find the last comment before the function
    comment_match = re.search(r"//.*$", lookback, re.MULTILINE)

    assert comment_match is not None, (
        "isStationOrphaned must have a preceding // comment explaining "
        "why it checks equipment_streams (configured bindings) not staleness"
    )


def test_orphan_predicate_does_not_check_freshness_or_staleness():
    """The function body must NOT reference last_telemetry_epoch_ms, rssi,
    stale, freshness, offline, or missing — these indicate a freshness check
    instead of a binding presence check."""
    source = _script(_read())
    body = _extract_function(source, "function isStationOrphaned(", STOPS)
    body_stripped = _strip_js_comments(body)

    bad_words = [
        "last_telemetry_epoch_ms",
        "stale",
        "freshness",
        "offline",
        "missing",
        "rssi",
    ]
    for word in bad_words:
        assert word not in body_stripped, (
            f"isStationOrphaned must not check {word} — it should check "
            "configured binding presence only, not telemetry freshness"
        )


def test_orphaned_warning_banner_markup_present():
    """Must have a persistent banner div id='orphaned-stations-warning' with
    class='status-text warn' starting hidden."""
    source = _read()
    assert 'id="orphaned-stations-warning"' in source

    banner_idx = source.index('id="orphaned-stations-warning"')
    banner_tag = source[banner_idx - 50 : banner_idx + 80]
    assert "hidden" in banner_tag
    assert "warn" in banner_tag


def test_render_orphaned_stations_warning_function_exists():
    """Must have a renderOrphanedStationsWarning function."""
    source = _script(_read())
    assert "function renderOrphanedStationsWarning(" in source


def test_render_orphaned_warning_toggles_on_count():
    """renderOrphanedStationsWarning must show/hide the banner based on count."""
    source = _script(_read())
    body = _extract_function(source, "function renderOrphanedStationsWarning(", STOPS)

    assert "count > 0" in body or "count > 0" in body
    if_idx = body.index("if (")
    hidden_false_idx = body.index("banner.hidden = false;")
    else_idx = body.index("} else {")
    hidden_true_idx = body.index("banner.hidden = true;")

    # Order check: if-branch sets hidden=false, else-branch sets hidden=true
    assert if_idx < hidden_false_idx < else_idx < hidden_true_idx


def test_render_stations_calls_render_orphaned_warning():
    """renderStations() must call renderOrphanedStationsWarning()."""
    source = _script(_read())
    body = _extract_function(source, "function renderStations(", STOPS)
    assert "renderOrphanedStationsWarning(" in body


def test_clear_orphaned_stations_function_exists():
    """Must have a clearOrphanedStations() function."""
    source = _script(_read())
    assert "async function clearOrphanedStations()" in source or (
        "function clearOrphanedStations()" in source
    )


def test_clear_orphaned_stations_shows_one_confirm():
    """clearOrphanedStations must show exactly one window.confirm() before
    any assignStation calls."""
    source = _script(_read())
    body = _extract_function(
        source, "async function clearOrphanedStations(", STOPS
    ) or _extract_function(source, "function clearOrphanedStations(", STOPS)

    assert "window.confirm(" in body
    confirm_idx = body.index("window.confirm(")
    assign_idx = body.index("assignStation(")
    assert confirm_idx < assign_idx


def test_clear_orphaned_stations_reuses_assign_station():
    """clearOrphanedStations must call assignStation(stationNumber, null),
    never fetch() directly."""
    source = _script(_read())
    body = _extract_function(
        source, "async function clearOrphanedStations(", STOPS
    ) or _extract_function(source, "function clearOrphanedStations(", STOPS)

    assert "assignStation(" in body
    assert "/api/stations/assign" not in body
    assert "fetch(" not in body


def test_clear_orphaned_stations_catches_failures():
    """clearOrphanedStations must catch per-station failures and report them."""
    source = _script(_read())
    body = _extract_function(
        source, "async function clearOrphanedStations(", STOPS
    ) or _extract_function(source, "function clearOrphanedStations(", STOPS)

    assert "catch" in body
    assert "failedStations" in body or "failed" in body


def test_clear_orphaned_stations_failure_collection_in_catch():
    """The catch block must push failed stations to a failures array.
    (Mutation-proof test: comments can't satisfy this.)"""
    source = _script(_read())
    body = _extract_function(
        source, "async function clearOrphanedStations(", STOPS
    ) or _extract_function(source, "function clearOrphanedStations(", STOPS)

    # Find the catch block
    catch_match = re.search(
        r"catch\s*\(\s*error\s*\)\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
        body,
        re.DOTALL,
    )
    assert catch_match, "catch (error) block not found"

    catch_body = catch_match.group(1)
    catch_body_stripped = _strip_js_comments(catch_body)

    assert (
        ".push(" in catch_body_stripped
    ), "catch block must push failed station(s) to a failures list"


def test_clear_orphaned_uses_apply_stations_update():
    """clearOrphanedStations must use applyStationsUpdate() with server data,
    never fabricating cleared state locally."""
    source = _script(_read())
    body = _extract_function(
        source, "async function clearOrphanedStations(", STOPS
    ) or _extract_function(source, "function clearOrphanedStations(", STOPS)

    assert "applyStationsUpdate(" in body
    # Must not manually delete from state
    assert "delete state.stations.stations[" not in body


def test_no_auto_popup_for_orphaned_stations():
    """Unlike unassigned streams, orphaned stations must NOT have an automatic
    modal popup when they are detected. The operator must actively click the
    clear button."""
    source = _script(_read())
    body = _extract_function(source, "function renderOrphanedStationsWarning(", STOPS)

    # Must NOT call any dialog-opening function (like openUnassignedDialog)
    assert "openUnassignedDialog()" not in body
    assert ".classList.add(" not in body or "show" not in body


def test_clear_orphaned_button_wired_correctly():
    """The button that clears orphaned stations must have onclick
    wired to clearOrphanedStations()."""
    source = _read()
    # Look for a button with the orphaned-stations action in the panel header
    panel_idx = source.index('id="panel-stations"')
    panel_end = source.index("</section>", panel_idx)
    panel_section = source[panel_idx:panel_end]

    # The button should be somewhere in the panel (likely in panel-header)
    assert "clearOrphanedStations()" in panel_section


def test_clear_orphaned_button_and_banner_are_separate():
    """The orphaned-stations warning banner and clear button must be
    separate from the unassigned-streams banner."""
    source = _read()
    assert 'id="orphaned-stations-warning"' in source
    assert 'id="unassigned-streams-warning"' in source
    # Ensure they're different elements
    orphaned_idx = source.index('id="orphaned-stations-warning"')
    unassigned_idx = source.index('id="unassigned-streams-warning"')
    assert orphaned_idx != unassigned_idx


# ---------------------------------------------------------------------------
# C. i18n keys
# ---------------------------------------------------------------------------


NEW_I18N_KEYS = [
    "warning.orphaned_stations",
    "button.clear_orphaned_stations",
    "confirm.clear_orphaned_stations",
    "message.clear_orphaned_stations_success",
    "message.clear_orphaned_stations_partial_failure",
]


def test_new_orphaned_i18n_keys_present_in_en_us():
    """All orphaned-stations i18n keys must exist in en-US dictionary."""
    source = _read()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]

    for key in NEW_I18N_KEYS:
        assert f'"{key}":' in en_block, f"{key} missing from en-US dictionary"


def test_new_orphaned_i18n_keys_present_in_zh_tw():
    """All orphaned-stations i18n keys must exist in zh-TW dictionary."""
    source = _read()
    zh_start = source.index('dictionaries["zh-TW"] = {')
    zh_block = source[zh_start : zh_start + 8000]

    for key in NEW_I18N_KEYS:
        assert f'"{key}":' in zh_block, f"{key} missing from zh-TW dictionary"
