"""Regression tests for the GET /api/stations request storm.

Root-cause context (measured live on the venue hub, tony@192.168.0.130):
4348 req/min (~72/sec) to GET /api/stations with the operator's tabs open,
dropping to ~48/sec after closing the Game Admin tab -- roughly 24/sec per
open tab. All traffic came from one client IP with 15 established
connections, which then starved unrelated POSTs (e.g. the System Admin
"assign all" flow) of the browser's per-host connection budget.

hub_server/static/index.html's dashboard `ws.onmessage` handler ended with
an unconditional fall-through:

    if (data && Object.keys(data).length > 0) {
      renderLeaderboard(data);
    }
    fetchStations(); // Update registrations dynamically

Telemetry broadcasts (hub_server/adapters/mqtt_subscriber.py, both the
populated per-athlete `display_progress` dict broadcast while a race is
RUNNING, and the empty `{}` broadcast used to signal "a not-yet-registered
node just started streaming") carry no `type` field, so every single one
reached this fall-through and re-fetched /api/stations over HTTP. With six
machines streaming telemetry at a 250ms interval that is ~24 broadcasts/sec
-- and therefore ~24 unnecessary refetches/sec -- per open tab.

Only the empty-broadcast case can actually add a new station/node
registration; the populated leaderboard case never does. gameAdmin.html's
`socket.onmessage` had the same shape: `if (!payload.type)` matched both
the empty broadcast AND the populated (but gameAdmin never renders)
leaderboard payload, firing two HTTP refetches (refreshStations +
refreshReadiness) per telemetry tick.

These tests follow the same source-extraction technique used in
tests/unit/hub/test_system_admin_websocket_reconnect.py: pull the exact
body of the handler under test out of the page's inline <script> using
brace-depth matching (not a substring/regex search across the whole page),
so a passing test can't be satisfied by an unrelated comment or a
same-named string sitting elsewhere on the page.
"""

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _script(source: str) -> str:
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return source[start:end]


def _extract_braced(source: str, anchor: str) -> str:
    """Return the exact `{ ... }` block that immediately follows `anchor`.

    Walks brace depth character-by-character from the first `{` after the
    anchor text until depth returns to zero, rather than a non-greedy regex
    stopping at the first `};` -- some handler bodies contain template
    literals like `${data.action}` whose own braces are balanced, but a
    naive `.*?\\};` regex is still fragile against nested arrow-function
    callbacks. Brace counting is exact regardless.
    """
    start = source.index(anchor) + len(anchor)
    brace_start = source.index("{", start)
    depth = 0
    for i in range(brace_start, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start : i + 1]
    raise AssertionError(f"unbalanced braces after anchor {anchor!r}")


def _index_onmessage_body() -> str:
    script = _script(_read("index.html"))
    return _extract_braced(script, "ws.onmessage = (event) =>")


def _game_admin_onmessage_body() -> str:
    script = _script(_read("gameAdmin.html"))
    return _extract_braced(script, "socket.onmessage = (event) =>")


# ---------------------------------------------------------------------------
# index.html dashboard: populated (typed-leaderboard) telemetry must not
# trigger a stations refetch; only the empty "new node" broadcast may.
# ---------------------------------------------------------------------------


def test_index_populated_leaderboard_branch_returns_before_stations_refetch():
    body = _index_onmessage_body()
    branch = _extract_braced(body, "if (data && Object.keys(data).length > 0)")
    assert "renderLeaderboard(data);" in branch
    assert "return;" in branch, (
        "the populated-leaderboard branch must return so this untyped, "
        "high-frequency broadcast (up to ~24/sec across 6 machines) never "
        "falls through into the trailing fetchStations() call"
    )
    render_idx = branch.index("renderLeaderboard(data);")
    return_idx = branch.index("return;")
    assert render_idx < return_idx


def test_index_fetchstations_call_sits_outside_the_leaderboard_branch():
    body = _index_onmessage_body()
    branch = _extract_braced(body, "if (data && Object.keys(data).length > 0)")
    assert "fetchStations()" not in branch, (
        "fetchStations() must not run for populated telemetry payloads -- "
        "those never carry station registration changes"
    )
    # The trailing fetchStations() call must still exist immediately after
    # the branch closes, so it stays reachable for the empty `{}` broadcast
    # (the only untyped message that signals a newly discovered node).
    after_branch = body[body.index(branch) + len(branch) :]
    assert "fetchStations(); // Update registrations dynamically" in after_branch


# ---------------------------------------------------------------------------
# gameAdmin.html: same shape, guarded by payload emptiness instead of type.
# ---------------------------------------------------------------------------


def test_game_admin_untyped_refetch_gated_on_empty_payload():
    body = _game_admin_onmessage_body()
    assert "if (!payload.type && Object.keys(payload).length === 0)" in body, (
        "the untyped-message branch must also require an empty payload -- "
        "otherwise every populated (but unrendered) telemetry tick still "
        "triggers refreshStations()+refreshReadiness() (~24/sec per tab)"
    )
    branch = _extract_braced(
        body, "if (!payload.type && Object.keys(payload).length === 0)"
    )
    assert "refreshStations()" in branch
    assert "refreshReadiness()" in branch
    assert "return;" in branch


def test_game_admin_has_no_bare_untyped_fallthrough():
    body = _game_admin_onmessage_body()
    # A bare `if (!payload.type)` (without the emptiness guard) would match
    # this narrowed condition as a substring, so check the exact bare form
    # does not appear anywhere in the handler.
    assert "if (!payload.type) {" not in body
