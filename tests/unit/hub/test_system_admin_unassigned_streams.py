"""Regression tests for the "unassigned telemetry streams" prompt on
System Admin (hub_server/static/systemAdmin.html).

These tests follow the same source-extraction technique used elsewhere in
tests/unit/hub/test_static_page_i18n.py: pull the exact body of the function
under test out of the page's inline <script>, then assert on that narrow
window. That keeps a passing test from being satisfied by an unrelated
comment or a same-named string sitting somewhere else on the page.
"""

import re
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


# ---------------------------------------------------------------------------
# 1. Persistent warning banner
# ---------------------------------------------------------------------------


def test_unassigned_warning_banner_markup_present_and_hidden_by_default():
    source = _read()
    assert 'id="unassigned-streams-warning"' in source
    # Must start hidden -- it only appears once renderUnassignedWarning()
    # decides there is something to warn about.
    banner_tag = source[
        source.index('id="unassigned-streams-warning"')
        - 40 : source.index('id="unassigned-streams-warning"')
        + 60
    ]
    assert "hidden" in banner_tag
    assert "warn" in banner_tag


def test_render_unassigned_warning_toggles_on_count():
    source = _script(_read())
    body = _extract_function(source, "function renderUnassignedWarning(", STOPS)

    # The banner must be driven by the count of currently-unassigned
    # streams, go through t() (i18n), and flip `hidden` both ways -- not
    # just be shown once and never hidden again.
    assert "count > 0" in body
    if_idx = body.index("if (count > 0)")
    hidden_false_idx = body.index("banner.hidden = false;")
    else_idx = body.index("} else {")
    hidden_true_idx = body.index("banner.hidden = true;")
    # The false-branch must set hidden=false and the else-branch hidden=true,
    # in that order -- not the same assignment repeated or swapped.
    assert if_idx < hidden_false_idx < else_idx < hidden_true_idx
    assert 't("warning.unassigned_streams"' in body


def test_render_stations_feeds_available_node_ids_into_warning():
    source = _script(_read())
    body = _extract_function(source, "function renderStations(", STOPS)
    assert "renderUnassignedWarning(availableNodeIds)" in body


# ---------------------------------------------------------------------------
# 2. Dialog fired only when the unassigned set grows
# ---------------------------------------------------------------------------


def test_apply_stations_update_only_checks_growth_when_asked():
    source = _script(_read())
    body = _extract_function(source, "function applyStationsUpdate(", STOPS)

    assert "checkGrowth" in body
    # Growth = some currently-unassigned node id that wasn't in the
    # previously-known set.
    assert (
        "availableNodeIds.some((nodeId) => !state.knownUnassignedNodeIds.has(nodeId))"
        in body
    )
    assert "openUnassignedDialog()" in body
    assert "state.knownUnassignedNodeIds = new Set(availableNodeIds);" in body

    # Order matters: the comparison against the previously-known set must
    # happen BEFORE that set is overwritten with the current one, or growth
    # could never be detected (comparing the set against itself).
    growth_check_idx = body.index("state.knownUnassignedNodeIds.has(nodeId)")
    snapshot_idx = body.index(
        "state.knownUnassignedNodeIds = new Set(availableNodeIds);"
    )
    assert growth_check_idx < snapshot_idx


def test_refresh_stations_polls_with_growth_checking_enabled():
    source = _script(_read())
    body = _extract_function(source, "async function refreshStations(", STOPS)
    assert "checkGrowth: true" in body


def test_manual_assign_and_unassign_do_not_request_growth_check():
    """A deliberate assign/unassign by the operator should not itself pop
    the "new unassigned streams" dialog -- only the poll/websocket refresh
    path (refreshStations) should be able to trigger it."""
    source = _script(_read())
    assign_body = _extract_function(
        source, "async function assignSelectedStation(", STOPS
    )
    unassign_body = _extract_function(source, "async function unassignStation(", STOPS)
    assert "checkGrowth: true" not in assign_body
    assert "checkGrowth: true" not in unassign_body
    assert "applyStationsUpdate(" in assign_body
    assert "applyStationsUpdate(" in unassign_body


# ---------------------------------------------------------------------------
# 3. One-click "assign all" inside the dialog
# ---------------------------------------------------------------------------


def test_assign_all_reuses_existing_assign_station_helper():
    source = _script(_read())
    body = _extract_function(
        source, "async function assignAllUnassignedStreams(", STOPS
    )
    # Must reuse the same path as the per-row "->" action / the Assign
    # button (assignStation()), not talk to the API directly a second way.
    assert "assignStation(candidate, nodeId)" in body
    assert "/api/stations/assign" not in body
    assert "fetch(" not in body


def test_assign_all_reports_failures_per_stream_and_keeps_them_unassigned():
    source = _script(_read())
    body = _extract_function(
        source, "async function assignAllUnassignedStreams(", STOPS
    )
    assert "catch (error)" in body
    assert "failedLabels.push(" in body
    # It must never fabricate success locally -- unassigned bookkeeping
    # always comes from the server's response via applyStationsUpdate().
    assert "unassigned_nodes.push" not in body
    assert "unassigned_nodes.splice" not in body
    assert re.search(r"if\s*\(failedLabels\.length\)", body)
    assert 't("message.assign_all_partial_failure"' in body


def test_assign_all_always_re_enables_dialog_buttons_via_finally():
    source = _script(_read())
    body = _extract_function(
        source, "async function assignAllUnassignedStreams(", STOPS
    )
    match = re.search(r"finally\s*\{([^}]*)\}", body, re.S)
    assert (
        match
    ), "assignAllUnassignedStreams must clear its busy state in a finally block"
    finally_body = match.group(1)
    assert "assignBtn.disabled = false;" in finally_body
    assert "closeBtn.disabled = false;" in finally_body


def test_dialog_assign_button_wired_to_assign_all():
    source = _read()
    assert 'onclick="assignAllUnassignedStreams()"' in source


# ---------------------------------------------------------------------------
# 4. Dialog is always dismissible
# ---------------------------------------------------------------------------


def test_dialog_has_close_button_and_overlay_click_and_escape_handling():
    source = _read()
    assert 'onclick="closeUnassignedDialog()"' in source

    script = _script(source)
    overlay_click = re.search(
        r'\$\("unassigned-dialog"\)\.addEventListener\("click",\s*\(event\)\s*=>\s*\{[^}]*closeUnassignedDialog\(\)',
        script,
        re.S,
    )
    assert overlay_click, "clicking the overlay backdrop must close the dialog"

    escape_handler = re.search(
        r'event\.key === "Escape"[^;]*unassigned-dialog[^;]*closeUnassignedDialog\(\)'
        r"|"
        r'unassigned-dialog[^;]*event\.key === "Escape"[^;]*closeUnassignedDialog\(\)',
        script,
        re.S,
    )
    assert escape_handler, "Escape key must close the unassigned-streams dialog"


# ---------------------------------------------------------------------------
# 5. i18n: new keys exist in both inline dictionaries
# ---------------------------------------------------------------------------

NEW_I18N_KEYS = [
    "warning.unassigned_streams",
    "dialog.unassigned_title",
    "dialog.unassigned_message",
    "button.assign_all_unassigned",
    "message.assign_all_partial_failure",
]


def test_new_i18n_keys_present_in_both_dictionaries():
    source = _read()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 6000]

    for key in NEW_I18N_KEYS:
        assert f'"{key}":' in en_block, f"{key} missing from en-US dictionary"
        assert f'"{key}":' in zh_block, f"{key} missing from zh-TW dictionary"
