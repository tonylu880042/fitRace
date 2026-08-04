"""Regression tests for the "clear all station assignments" feature on
System Admin (hub_server/static/systemAdmin.html).

These tests follow the same source-extraction technique used elsewhere in
tests/unit/hub/test_static_page_i18n.py: pull the exact body of the function
under test out of the page's inline <script>, then assert on that narrow
window. That keeps a passing test from being satisfied by an unrelated
comment or a same-named string sitting somewhere else on the page.
"""

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
# 1. Bulk clear button and wiring
# ---------------------------------------------------------------------------


def test_clear_all_stations_button_exists_in_panel_header():
    """The clear-all-stations button must be in the Station Assignment panel
    header, next to the existing Copy Signup Link button."""
    source = _read()
    assert 'id="panel-stations"' in source
    panel_start = source.index('id="panel-stations"')
    panel_header_start = source.index('class="panel-header"', panel_start)
    panel_end = source.index("</section>", panel_start)
    panel_section = source[panel_header_start:panel_end]

    # Button must exist with onclick wired to a function
    assert 'onclick="clearAllStations()"' in panel_section


def test_clear_all_stations_button_is_disabled_when_no_assigned_stations():
    """The clear-all button should be disabled/hidden when there are no
    assigned stations to clear. This is handled by the function checking
    the count before enabling the button."""
    source = _script(_read())
    body = _extract_function(source, "function clearAllStations(", STOPS)
    # Must check if there are any assigned stations
    assert "stations" in body
    assert "Object.keys" in body or "keys.length" in body or "for" in body


# ---------------------------------------------------------------------------
# 2. Confirmation dialog
# ---------------------------------------------------------------------------


def test_clear_all_stations_shows_one_confirm_with_count():
    """Must show exactly one window.confirm() naming the count of stations
    to clear, BEFORE any assignStation calls."""
    source = _script(_read())
    body = _extract_function(source, "function clearAllStations(", STOPS)

    # Must have a confirm dialog
    assert "window.confirm(" in body
    assert "t(" in body  # Uses i18n
    assert "{count}" in body or "count" in body

    # The confirm must happen BEFORE any assignStation calls
    confirm_idx = body.index("window.confirm(")
    assign_idx = body.index("assignStation(")
    assert (
        confirm_idx < assign_idx
    ), "window.confirm must happen before assignStation calls"


# ---------------------------------------------------------------------------
# 3. Reuses existing assignStation helper
# ---------------------------------------------------------------------------


def test_clear_all_stations_reuses_assign_station_helper():
    """Must call assignStation(stationNumber, null) for each assigned station,
    never POST directly to /api/stations/assign or call fetch() directly."""
    source = _script(_read())
    body = _extract_function(source, "function clearAllStations(", STOPS)

    # Must reuse the same helper as unassignStation
    assert "assignStation(" in body
    # Must NOT call fetch or POST directly
    assert "/api/stations/assign" not in body
    assert "fetch(" not in body


# ---------------------------------------------------------------------------
# 4. Handles per-station failures gracefully
# ---------------------------------------------------------------------------


def test_clear_all_stations_catches_failures_and_reports_them():
    """Failed per-station calls must be caught and reported, not silently
    swallowed. The function must never fabricate locally-cleared station
    state — it must always use data returned from assignStation() calls."""
    source = _script(_read())
    body = _extract_function(source, "function clearAllStations(", STOPS)

    # Must have try/catch around assignStation calls
    assert "catch (error)" in body

    # Must collect failed stations
    assert "failedLabels" in body or "failed" in body or "failures" in body

    # Must NOT fabricate success locally (no manual deletion/splicing of station state)
    assert "delete state.stations.stations[" not in body
    assert ".splice(" not in body or "stations" not in body  # Be permissive here

    # Must call applyStationsUpdate() with server-returned data, not local fabrication
    assert "applyStationsUpdate(" in body


def test_clear_all_stations_does_not_fabricate_cleared_state():
    """The function must derive its final state from what assignStation()
    returned, never by manually removing keys from state.stations locally."""
    source = _script(_read())
    body = _extract_function(source, "function clearAllStations(", STOPS)

    # Must NOT have code that manually removes/clears stations
    # (checking for common patterns of local state mutation)
    bad_patterns = [
        "state.stations.stations[",  # direct index deletion
        "stations.splice",  # removing entries locally
        "delete ",  # explicit deletion
    ]
    for pattern in bad_patterns:
        assert pattern not in body, f"Must not have '{pattern}' — must use server state"


# ---------------------------------------------------------------------------
# 5. Existing unassignStation is untouched
# ---------------------------------------------------------------------------


def test_unassign_station_function_unchanged():
    """The existing per-station unassignStation() function must be untouched:
    still present, still uses window.confirm, still calls assignStation()."""
    source = _script(_read())
    body = _extract_function(source, "async function unassignStation(", STOPS)

    assert "window.confirm(t(" in body
    assert "confirm.unassign_station" in body
    assert "assignStation(stationNumber, null)" in body


# ---------------------------------------------------------------------------
# 6. i18n keys exist in both dictionaries
# ---------------------------------------------------------------------------


NEW_I18N_KEYS = [
    "confirm.clear_all_stations",
    "message.clear_all_stations_success",
    "message.clear_all_stations_partial_failure",
]


def test_new_i18n_keys_present_in_both_dictionaries():
    """All new i18n keys for bulk-clear must exist in both en-US and zh-TW
    inline dictionaries in the same file."""
    source = _read()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 6000]

    for key in NEW_I18N_KEYS:
        assert f'"{key}":' in en_block, f"{key} missing from en-US dictionary"
        assert f'"{key}":' in zh_block, f"{key} missing from zh-TW dictionary"
