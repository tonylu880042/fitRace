"""Regression tests for the GET /api/stations request storm.

Root-cause context (measured live on the venue hub, tony@192.168.0.130):
GET /api/race/readiness ran at 1452/min (~24/sec) -- only Game Admin calls
that endpoint, and 24/sec is exactly the telemetry rate (six machines at a
250ms interval), and GET /api/stations ran at 5803/min.

A first attempted fix (commit 2553aba) gated the untyped-broadcast refetch
on `Object.keys(payload).length === 0`, on the premise that "only the empty
broadcast can add a new station." That premise was wrong:
hub_server/adapters/mqtt_subscriber.py::_handle_telemetry broadcasts the
empty `{}` on *every* telemetry row whenever the race isn't RUNNING
(`race_manager.ingest_telemetry` returns `None` for every row unless the
race state is RUNNING) -- so gating on emptiness changed nothing; the empty
broadcast is exactly as high-frequency as the populated one.

The actual fix: stop deciding "refetch stations?" by inspecting the *shape*
of an untyped telemetry broadcast at all. Station/node membership changes
already have dedicated typed events -- `registration_success`,
`node_status`, and `state_change` -- which each page already dispatches on.
No untyped payload, populated or empty, should trigger an HTTP request.

One real gap this surfaced: hub_server/static/index.html's `node_status`
branch used to only call `renderEdgeNodes(...)` and never refreshed
`/api/stations`. A newly-active (not yet assigned to a station) node only
shows up in `/api/stations`' `unassigned_nodes` list -- driven by
`race_manager._active_nodes`, populated purely from telemetry -- which
`node_status` (the edge node's heartbeat, reporting *configured* bindings)
never directly announces. Since the dashboard has no other periodic refresh
while its WebSocket is connected (see `startFallbackRefresh`, which is a
no-op whenever `wsConnected`), removing the untyped fallback without first
wiring `fetchStations()` into `node_status` would have broken that
discovery path. gameAdmin.html's `node_status` branch already refreshed
stations/readiness, so no equivalent gap existed there.

These tests follow the same source-extraction technique used in
tests/unit/hub/test_system_admin_websocket_reconnect.py: pull the exact
body of the handler under test out of the page's inline <script> using
brace-depth matching (not a substring/regex search across the whole page),
so a passing test can't be satisfied by an unrelated comment or a
same-named string sitting elsewhere on the page.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

# Matches a whole line whose first non-whitespace characters are `//` --
# i.e. a comment-only line, not a trailing `code(); // note` comment and
# not a `//` that appears mid-line inside a string/template literal (e.g.
# `` `http://${host}` ``). See `_strip_js_comments` for why this
# conservative shape was chosen over a full tokenizer.
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
# Block comments never legitimately contain `://`-style substrings (a
# literal `/*` requires an asterisk immediately after the slash, which no
# URL scheme or template literal in these pages produces), so a plain
# non-greedy strip is safe here.
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    """Strip JS comments so a `//` comment can't silently satisfy a
    source-text assertion (CLAUDE.md's known "comment satisfies the
    assertion" trap).

    Chosen approach: whole-line `//` comments and `/* */` block comments
    are removed; a same-line trailing comment (`fetchStations(); // done`)
    is left alone. Distinguishing a trailing comment from a `//` that is
    part of a string/template literal (`` `http://...` ``) requires a real
    JS tokenizer; restricting the line-comment strip to lines that *start*
    with `//` avoids that ambiguity entirely, at the cost of not catching
    a hypothetical trailing same-line comment. No trailing-comment usage
    exists in the pages this test module reads today (verified by
    inspection of index.html, gameAdmin.html, and systemAdmin.html's
    inline `<script>` bodies), so this covers the actual contamination
    case without the misfire risk a blanket `//`-to-end-of-line strip
    would carry.
    """
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _script(source: str) -> str:
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return source[start:end]


def _extract_braced(source: str, anchor: str) -> str:
    """Return the exact `{ ... }` block that immediately follows `anchor`,
    with JavaScript comments stripped so no assertion downstream can be
    satisfied by a comment instead of real code.

    Walks brace depth character-by-character from the first `{` after the
    anchor text until depth returns to zero, rather than a non-greedy regex
    stopping at the first `};` -- some handler bodies contain template
    literals like `${data.action}` whose own braces are balanced, but a
    naive `.*?\\};` regex is still fragile against nested arrow-function
    callbacks. Brace counting is exact regardless, and runs over the raw
    (un-stripped) source so a comment could never distort brace depth --
    comment stripping happens only on the already-extracted slice, after
    matching, before it is returned.
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
                return _strip_js_comments(source[brace_start : i + 1])
    raise AssertionError(f"unbalanced braces after anchor {anchor!r}")


def _index_onmessage_body() -> str:
    script = _script(_read("index.html"))
    return _extract_braced(script, "ws.onmessage = (event) =>")


def _game_admin_onmessage_body() -> str:
    script = _script(_read("gameAdmin.html"))
    return _extract_braced(script, "socket.onmessage = (event) =>")


# HTTP-triggering call sites we never want to see in code that runs for an
# untyped broadcast. Checking these specific identifiers (rather than a
# blanket "fetch(" substring) means the assertion still pins down the exact
# calls even if a future refactor renames the wrapper -- each must be
# updated deliberately, not silently dropped from this list.
_HTTP_CALL_NAMES = (
    "fetch(",
    "fetchStations(",
    "fetchState(",
    "fetchNodes(",
    "refreshStations(",
    "refreshReadiness(",
    "refreshState(",
    "refreshOperationalState(",
    "refreshAll(",
)


def _assert_no_http_calls(label: str, code: str) -> None:
    for name in _HTTP_CALL_NAMES:
        assert name not in code, (
            f"{label} must issue no HTTP request, but found a call to " f"{name!r}"
        )


def _tail_after_typed_branches(body: str, anchors: list) -> str:
    """Return the code that runs once none of the typed `if` branches (each
    of which anchors a `{...}` block) matched -- i.e. the untyped handling
    left in the function after every named typed branch.
    """
    end = 0
    for anchor in anchors:
        branch = _extract_braced(body, anchor)
        idx = body.index(branch) + len(branch)
        end = max(end, idx)
    return body[end:]


INDEX_TYPED_ANCHORS = [
    'if (data.type === "registration_success")',
    'if (data.type === "state_change")',
    'if (data.type === "race_countdown")',
    'if (data.type === "node_status")',
    'if (data.type === "race_event")',
    'if (data.type === "system_power")',
]

GAME_ADMIN_TYPED_ANCHORS = [
    'if (payload.type === "state_change")',
    'if (payload.type === "node_status")',
    'if (payload.type === "registration_success")',
]


# ---------------------------------------------------------------------------
# index.html dashboard
# ---------------------------------------------------------------------------


def test_index_populated_leaderboard_branch_only_renders():
    body = _index_onmessage_body()
    branch = _extract_braced(body, "if (data && Object.keys(data).length > 0)")
    assert "renderLeaderboard(data);" in branch
    _assert_no_http_calls("index.html's populated-untyped (leaderboard) branch", branch)


def test_index_untyped_tail_never_issues_an_http_request():
    """Neither the populated-leaderboard case nor the truly-empty `{}` case
    may reach an HTTP call. This is the property the storm was caused by
    violating -- pin the property itself, not one line's spelling."""
    body = _index_onmessage_body()
    tail = _tail_after_typed_branches(body, INDEX_TYPED_ANCHORS)
    _assert_no_http_calls("index.html's untyped broadcast handling", tail)


def test_index_node_status_branch_refreshes_stations():
    """node_status is the typed event that must now cover new-node
    discovery on the dashboard (see module docstring): a not-yet-assigned
    node only appears in /api/stations' unassigned_nodes list, and the
    dashboard has no other periodic refresh while its socket is connected."""
    body = _index_onmessage_body()
    branch = _extract_braced(body, 'if (data.type === "node_status")')
    assert "renderEdgeNodes(" in branch
    assert "fetchStations()" in branch
    assert "return;" in branch


# ---------------------------------------------------------------------------
# gameAdmin.html
# ---------------------------------------------------------------------------


def test_game_admin_untyped_broadcast_never_issues_an_http_request():
    body = _game_admin_onmessage_body()
    tail = _tail_after_typed_branches(body, GAME_ADMIN_TYPED_ANCHORS)
    _assert_no_http_calls("gameAdmin.html's untyped broadcast handling", tail)


def test_game_admin_has_no_bare_untyped_fallthrough():
    body = _game_admin_onmessage_body()
    assert "if (!payload.type)" not in body
    assert "Object.keys(payload).length" not in body


def test_game_admin_node_status_branch_still_refreshes_stations_and_readiness():
    """Unchanged: gameAdmin's node_status branch already covered discovery
    before this fix and must keep doing so."""
    body = _game_admin_onmessage_body()
    branch = _extract_braced(body, 'if (payload.type === "node_status")')
    assert "refreshStations()" in branch
    assert "refreshReadiness()" in branch
    assert "return;" in branch


# ---------------------------------------------------------------------------
# systemAdmin.html: confirmed to have no per-broadcast untyped refetch --
# its `socket.onmessage` only dispatches on node_status/registration_success,
# and station freshness instead comes from its own unconditional 5s
# `refreshAll()` -> `scheduleRefresh()` loop, independent of any broadcast.
# This test pins that shape so a future change can't quietly reintroduce an
# untyped branch here.
# ---------------------------------------------------------------------------


def _system_admin_onmessage_body() -> str:
    script = _script(_read("systemAdmin.html"))
    return _extract_braced(script, "socket.onmessage = (event) =>")


def test_system_admin_onmessage_has_no_untyped_branch():
    body = _system_admin_onmessage_body()
    assert "payload.type ===" in body
    assert "!payload.type" not in body
    assert "Object.keys(payload)" not in body
