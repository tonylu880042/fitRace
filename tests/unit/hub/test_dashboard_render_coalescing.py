"""Tests for coalescing telemetry-driven dashboard renders.

Root-cause context: hub_server/static/index.html's ws.onmessage handler used
to call renderLeaderboard(data) once per untyped telemetry broadcast, and a
second, unconditional requestAnimationFrame(renderSmoothLeaderboardFrame)
loop called it again on every animation frame -- up to 60 times a second --
regardless of whether new data had actually arrived. On a venue with several
machines streaming telemetry at a few Hz each, this meant a full leaderboard
rebuild (string-concatenated innerHTML plus getBoundingClientRect reads) many
times more often than the data itself changed, which is what drove Chrome's
"tab is using significant resources" warning and made the Pi unresponsive.

The fix: the untyped-telemetry branch now stashes the latest payload and
coalesces a burst of messages into a single requestAnimationFrame-deferred
render of the newest data (a plain Node context, as used by tests, has no
animation frame concept, so the branch falls back to rendering immediately
in that case -- this is also what keeps every pre-existing synchronous test
of this branch, e.g. tests/unit/hub/test_dashboard_stations_refetch_storm.py
and tests/unit/hub/test_dashboard_class_board.py, passing unmodified). The
old unconditional 60fps loop (renderSmoothLeaderboardFrame) is gone outright
-- there is no longer any per-frame work when nothing has changed.

These tests extract the real ws.onmessage handler (same brace-depth
technique used in test_dashboard_stations_refetch_storm.py and
test_dashboard_class_board.py) and drive it under node with a *controllable*
requestAnimationFrame stub -- one that records the callback instead of
running it immediately -- so a burst of synthetic telemetry events can be
fed in before the frame ever fires, and the coalescing behaviour can be
verified by manually flushing the captured callback.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _script(source: str) -> str:
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return source[start:end]


def _extract_braced(source: str, anchor: str) -> str:
    """Return the exact `{ ... }` block that immediately follows `anchor`,
    walking brace depth character-by-character so a template literal's own
    braces can't throw off the match (mirrors
    test_dashboard_stations_refetch_storm.py's technique)."""
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
    return _strip_js_comments(
        _extract_braced(_script(_read_index()), "ws.onmessage = (event) =>")
    )


def _telemetry_event(payload: dict) -> str:
    """JS source for the *event.data* string of one WS message -- i.e. a
    JSON-encoded telemetry payload, itself re-encoded as a JS string
    literal so it can sit inside a generated script."""
    return json.dumps(json.dumps(payload))


def _run_burst(payloads, flush: bool) -> dict:
    """Feed a burst of synthetic untyped-telemetry events through the real
    ws.onmessage handler, with requestAnimationFrame stubbed to *capture*
    its callback rather than run it, then optionally invoke the captured
    callback exactly once (flush) and report how renderLeaderboard and
    requestAnimationFrame were called."""
    onmessage_body = _index_onmessage_body()
    events_js = "[" + ", ".join(_telemetry_event(p) for p in payloads) + "]"
    script = (
        "let leaderboardRenderScheduled = false;\n"
        "let pendingLeaderboardData = null;\n"
        "let renderLeaderboardCalls = [];\n"
        "function renderLeaderboard(data) { renderLeaderboardCalls.push(data); }\n"
        "let rafCallbacks = [];\n"
        "let rafCallCount = 0;\n"
        "function requestAnimationFrame(cb) { rafCallCount += 1; rafCallbacks.push(cb); }\n"
        "const ws = {};\n"
        f"ws.onmessage = (event) => {onmessage_body}\n"
        f"const events = {events_js};\n"
        "events.forEach((raw) => ws.onmessage({ data: raw }));\n"
        + ("while (rafCallbacks.length) { rafCallbacks.shift()(); }\n" if flush else "")
        + "console.log(JSON.stringify({ renderLeaderboardCalls, rafCallCount }));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


_PAYLOAD_1 = {"n1": {"node_id": "n1", "power_watts": 100}}
_PAYLOAD_2 = {"n1": {"node_id": "n1", "power_watts": 200}}
_PAYLOAD_3 = {"n1": {"node_id": "n1", "power_watts": 300}}


def test_burst_before_a_frame_requests_only_one_animation_frame():
    """Several telemetry messages arriving before the frame ever fires must
    request requestAnimationFrame exactly once, not once per message --
    this is the coalescing itself, independent of what happens when the
    frame later runs."""
    result = _run_burst([_PAYLOAD_1, _PAYLOAD_2, _PAYLOAD_3], flush=False)
    assert result["rafCallCount"] == 1, (
        f"expected exactly one requestAnimationFrame call for a 3-message "
        f"burst, got {result['rafCallCount']}"
    )
    # Nothing has rendered yet -- the frame has not fired.
    assert result["renderLeaderboardCalls"] == []


def test_burst_of_n_messages_renders_exactly_once_with_latest_payload():
    """The actual required property: N messages arriving between frames
    produce exactly ONE render, not N -- and that render carries the
    *newest* payload, not the first one that arrived."""
    result = _run_burst([_PAYLOAD_1, _PAYLOAD_2, _PAYLOAD_3], flush=True)
    assert len(result["renderLeaderboardCalls"]) == 1, (
        f"expected exactly 1 renderLeaderboard call for a 3-message burst, "
        f"got {len(result['renderLeaderboardCalls'])}: "
        f"{result['renderLeaderboardCalls']}"
    )
    assert result["renderLeaderboardCalls"][0] == _PAYLOAD_3, (
        "the single coalesced render must use the latest payload in the "
        f"burst, got {result['renderLeaderboardCalls'][0]}"
    )


def test_single_message_still_renders():
    """Coalescing a burst must not accidentally coalesce away a lone
    message -- one telemetry event still produces one render once its
    frame fires."""
    result = _run_burst([_PAYLOAD_1], flush=True)
    assert len(result["renderLeaderboardCalls"]) == 1
    assert result["renderLeaderboardCalls"][0] == _PAYLOAD_1


def test_idle_after_a_flush_requests_no_further_frames():
    """Once a burst has been flushed and no new telemetry arrives, the
    handler must not request another animation frame on its own -- the
    old defect was an unconditional self-rescheduling loop that kept
    waking the tab even with nothing new to render."""
    onmessage_body = _index_onmessage_body()
    script = (
        "let leaderboardRenderScheduled = false;\n"
        "let pendingLeaderboardData = null;\n"
        "let renderLeaderboardCalls = [];\n"
        "function renderLeaderboard(data) { renderLeaderboardCalls.push(data); }\n"
        "let rafCallbacks = [];\n"
        "let rafCallCount = 0;\n"
        "function requestAnimationFrame(cb) { rafCallCount += 1; rafCallbacks.push(cb); }\n"
        "const ws = {};\n"
        f"ws.onmessage = (event) => {onmessage_body}\n"
        f"ws.onmessage({{ data: {json.dumps(json.dumps(_PAYLOAD_1))} }});\n"
        "while (rafCallbacks.length) { rafCallbacks.shift()(); }\n"
        "const rafCallCountAfterFlush = rafCallCount;\n"
        # Idle: the empty {} tick the hub sends whenever no race is
        # running must never itself schedule a frame.
        f"ws.onmessage({{ data: {json.dumps(json.dumps({}))} }});\n"
        "console.log(JSON.stringify({ rafCallCountAfterFlush, rafCallCountAfterIdleTick: rafCallCount }));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    payload = json.loads(result.stdout)
    assert payload["rafCallCountAfterFlush"] == 1
    assert payload["rafCallCountAfterIdleTick"] == 1, (
        "an idle (empty payload) tick must not request a new animation "
        f"frame; count went from 1 to {payload['rafCallCountAfterIdleTick']}"
    )


def test_new_telemetry_after_idle_resumes_scheduling():
    """After a flushed, idle period, real telemetry arriving again must
    resume scheduling -- coalescing must not permanently wedge itself off
    after its first render."""
    onmessage_body = _index_onmessage_body()
    script = (
        "let leaderboardRenderScheduled = false;\n"
        "let pendingLeaderboardData = null;\n"
        "let renderLeaderboardCalls = [];\n"
        "function renderLeaderboard(data) { renderLeaderboardCalls.push(data); }\n"
        "let rafCallbacks = [];\n"
        "let rafCallCount = 0;\n"
        "function requestAnimationFrame(cb) { rafCallCount += 1; rafCallbacks.push(cb); }\n"
        "const ws = {};\n"
        f"ws.onmessage = (event) => {onmessage_body}\n"
        f"ws.onmessage({{ data: {json.dumps(json.dumps(_PAYLOAD_1))} }});\n"
        "while (rafCallbacks.length) { rafCallbacks.shift()(); }\n"
        f"ws.onmessage({{ data: {json.dumps(json.dumps(_PAYLOAD_2))} }});\n"
        "while (rafCallbacks.length) { rafCallbacks.shift()(); }\n"
        "console.log(JSON.stringify({ rafCallCount, renderLeaderboardCalls }));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    payload = json.loads(result.stdout)
    assert payload["rafCallCount"] == 2, (
        "a second, later telemetry message must request a second animation "
        f"frame (resuming after idle), got {payload['rafCallCount']} total"
    )
    assert payload["renderLeaderboardCalls"] == [_PAYLOAD_1, _PAYLOAD_2]


def test_no_requestanimationframe_global_falls_back_to_synchronous_render():
    """A context with no requestAnimationFrame at all (the shape every
    pre-existing synchronous test of this branch relies on) must still
    render immediately -- proving the coalescing addition is additive, not
    a behaviour change for environments without animation frames."""
    onmessage_body = _index_onmessage_body()
    script = (
        "let leaderboardRenderScheduled = false;\n"
        "let pendingLeaderboardData = null;\n"
        "let renderLeaderboardCalls = [];\n"
        "function renderLeaderboard(data) { renderLeaderboardCalls.push(data); }\n"
        "const ws = {};\n"
        f"ws.onmessage = (event) => {onmessage_body}\n"
        f"ws.onmessage({{ data: {json.dumps(json.dumps(_PAYLOAD_1))} }});\n"
        "console.log(JSON.stringify({ renderLeaderboardCalls }));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    payload = json.loads(result.stdout)
    assert payload["renderLeaderboardCalls"] == [_PAYLOAD_1]


# -- Source-level regression: the old unconditional 60fps loop is gone
# entirely, not merely made conditional -- there must be no per-frame work
# left running when nothing has changed.


def test_unconditional_smoothing_loop_no_longer_exists():
    source = _strip_js_comments(_read_index())
    assert "renderSmoothLeaderboardFrame" not in source, (
        "the unconditional per-animation-frame full-rerender loop must be "
        "removed outright, not merely gated"
    )
    assert "isSmoothLeaderboardFrame" not in source


def test_dashboard_init_no_longer_starts_a_perpetual_raf_loop():
    """initializeDashboard used to kick off the unconditional loop with a
    bare requestAnimationFrame(renderSmoothLeaderboardFrame) call. Assert
    the specific startup call site is gone (the coalescing scheduler is
    entirely reactive to incoming telemetry, so nothing needs to be primed
    at startup)."""
    source = _read_index()
    fn_start = source.index("async function initializeDashboard(")
    body = source[fn_start : fn_start + 800]
    assert "requestAnimationFrame(renderSmoothLeaderboardFrame)" not in body
