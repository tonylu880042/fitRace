"""Regression tests for the dashboard-socket reconnect loop on System Admin
(hub_server/static/systemAdmin.html).

Root-cause context: the live venue hub showed hundreds of simultaneous
GET /api/stations requests sharing one identical `?t=` timestamp -- the
signature of many concurrently-live `/ws/dashboard` sockets all reacting to
one broadcast in the same JS tick, each firing its own refreshStations().
`connectWebSocket()` used to hold no reference to the socket it created and
no reference to the pending reconnect timer, so nothing made a reconnect
attempt idempotent -- there was no guard to stop a second live socket from
being opened, and no way to cancel a stray scheduled reconnect once a fresh
one succeeded.

These tests follow the same source-extraction technique used in
tests/unit/hub/test_system_admin_unassigned_streams.py: pull the exact body
of the function under test out of the page's inline <script>, then assert on
that narrow window, so a passing test can't be satisfied by an unrelated
comment or a same-named string sitting elsewhere on the page.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read() -> str:
    return (STATIC_DIR / "systemAdmin.html").read_text(encoding="utf-8")


def _script(source: str) -> str:
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return source[start:end]


def _extract_function(source: str, signature: str, stop_markers) -> str:
    start = source.index(signature)
    window = source[start : start + 4000]
    stops = [window.index(marker) for marker in stop_markers if marker in window[1:]]
    end = min(stops) + 1 if stops else len(window)
    return window[:end]


NEXT_FN = "\n    function "
NEXT_ASYNC_FN = "\n    async function "
STOPS = [NEXT_FN, NEXT_ASYNC_FN]


def _connect_websocket_body(source: str) -> str:
    script = _script(source)
    return _extract_function(script, "function connectWebSocket(", STOPS)


# ---------------------------------------------------------------------------
# 1. state carries explicit tracking for the live socket + pending timer
# ---------------------------------------------------------------------------


def test_state_declares_websocket_tracking_fields():
    source = _script(_read())
    state_start = source.index("const state = {")
    state_block = source[state_start : source.index("};", state_start)]
    assert "wsSocket: null" in state_block
    assert "wsReconnectTimer: null" in state_block


# ---------------------------------------------------------------------------
# 2. connectWebSocket() refuses to open a second live socket
# ---------------------------------------------------------------------------


def test_connect_websocket_guards_against_a_second_live_socket():
    body = _connect_websocket_body(_read())

    assert "state.wsSocket" in body
    assert "WebSocket.OPEN" in body
    assert "WebSocket.CONNECTING" in body
    assert re.search(r"return;\s*\}", body), (
        "the duplicate-socket guard must bail out (return) before a new "
        "WebSocket is constructed"
    )

    # The guard has to run BEFORE `new WebSocket(` -- checking readyState
    # after already opening a second connection defeats the point.
    guard_idx = body.index("WebSocket.CONNECTING")
    new_socket_idx = body.index("new WebSocket(")
    assert guard_idx < new_socket_idx


def test_connect_websocket_records_the_socket_it_creates():
    body = _connect_websocket_body(_read())
    new_socket_idx = body.index("new WebSocket(")
    store_idx = body.index("state.wsSocket = socket;")
    # Must be recorded right after creation, before onopen/onclose/onmessage
    # get wired up, so a concurrent call sees the in-flight socket too.
    assert new_socket_idx < store_idx
    onopen_idx = body.index("socket.onopen")
    assert store_idx < onopen_idx


# ---------------------------------------------------------------------------
# 3. the reconnect timer can never stack up behind an earlier one
# ---------------------------------------------------------------------------


def test_connect_websocket_clears_any_pending_reconnect_timer_up_front():
    body = _connect_websocket_body(_read())
    # The very first thing the function must do is cancel any reconnect
    # that's still scheduled from an earlier close -- otherwise a manual /
    # event-driven reconnect and a stale timer can both eventually fire.
    clear_idx = body.index("window.clearTimeout(state.wsReconnectTimer)")
    guard_idx = body.index("WebSocket.CONNECTING")
    assert clear_idx < guard_idx


def test_connect_websocket_onclose_reschedules_exactly_one_timer():
    body = _connect_websocket_body(_read())
    onclose_match = re.search(r"socket\.onclose = \(\) => \{(.*?)\};", body, re.S)
    assert onclose_match, "connectWebSocket must define socket.onclose"
    onclose_body = onclose_match.group(1)

    # Must clear any existing timer before scheduling the next one, and the
    # schedule must be tracked in state so a later call can cancel it.
    clear_idx = onclose_body.index("window.clearTimeout(state.wsReconnectTimer)")
    schedule_idx = onclose_body.index(
        "state.wsReconnectTimer = window.setTimeout(connectWebSocket, 3000);"
    )
    assert clear_idx < schedule_idx


# ---------------------------------------------------------------------------
# 4. a closed socket must release its slot so the guard doesn't wedge open
# ---------------------------------------------------------------------------


def test_connect_websocket_onclose_releases_its_own_socket_reference():
    body = _connect_websocket_body(_read())
    onclose_match = re.search(r"socket\.onclose = \(\) => \{(.*?)\};", body, re.S)
    assert onclose_match
    onclose_body = onclose_match.group(1)

    # Must only clear state.wsSocket if it still IS this socket -- an old,
    # already-superseded socket's belated onclose must not null out a
    # newer, currently-live socket's reference.
    assert "state.wsSocket === socket" in onclose_body
    identity_idx = onclose_body.index("state.wsSocket === socket")
    null_idx = onclose_body.index("state.wsSocket = null")
    assert identity_idx < null_idx


# ---------------------------------------------------------------------------
# 5. the assign-all flow's observable contract is untouched by this change
# ---------------------------------------------------------------------------


def test_assign_all_still_reuses_assign_station_and_never_calls_fetch_directly():
    """Guards against a regression that folds the socket fix into the assign
    path -- assignAllUnassignedStreams() must remain a plain sequential loop
    over the existing assignStation() helper."""
    source = _script(_read())
    body = _extract_function(
        source, "async function assignAllUnassignedStreams(", STOPS
    )
    assert "assignStation(candidate, nodeId)" in body
    assert "fetch(" not in body
    assert re.search(r"finally\s*\{[^}]*assignBtn\.disabled = false;", body, re.S)
