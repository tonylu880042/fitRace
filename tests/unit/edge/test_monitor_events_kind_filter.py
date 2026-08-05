"""Regression test: the edge Monitor tab's poll loop must request the
server-side telemetry filter.

/api/monitor/events accepts a `kind` query parameter (edge_node/
infrastructure/fastapi/app.py, get_monitor_events); when kind=="telemetry"
it applies `_is_telemetry_event`, a predicate that exists specifically
because a UART pairing scan or MQTT heartbeat stream can flood the raw
event log and evict a still-connected machine's telemetry event from the
trailing `limit` window (see the predicate's own docstring/comment). If the
page's poll loop (refreshMonitorEvents, in EDGE_SETUP_HTML's <script>)
never sends kind=telemetry, that server-side filter is dead code and the
monitor cards can show "waiting" for a machine that is actively streaming.

This pins the fetch call itself, with JS comments stripped first, so a
comment mentioning "kind=telemetry" near the call could not satisfy the
assertion while the real query string is missing it. (The backend's own
honoring of kind=telemetry -- that the predicate actually filters -- is
covered separately by
tests/integration/test_edge_api.py::test_edge_monitor_events_kind_telemetry_survives_a_scan_noise_flood.)
"""

import re

from edge_node.infrastructure.fastapi import app as edge_app_module

# Comment-stripping helpers (CLAUDE.md: a comment must not satisfy a source
# assertion). Same technique as tests/unit/hub/test_dashboard_remote_reload.py.
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _edge_setup_script() -> str:
    source = edge_app_module.EDGE_SETUP_HTML
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _extract_function_body(script: str, func_name: str) -> str:
    """Return the source of `function <func_name>(...) { ... }` (async or
    not) up to the start of the next top-level function definition, from a
    *comment-stripped* script. Fails loudly if the function isn't actually
    defined -- a comment mentioning the name doesn't count, since `script`
    has already had comments removed."""
    def_pattern = re.compile(
        r"(?:async\s+)?function\s+" + re.escape(func_name) + r"\s*\("
    )
    match = def_pattern.search(script)
    assert match, (
        f"No `function {func_name}(...)` definition found in EDGE_SETUP_HTML's "
        "script (checked against comment-stripped source, so a comment "
        "mentioning the name does not count)."
    )
    stop_pattern = re.compile(r"\n\s*(?:async\s+)?function\s+\w+\s*\(")
    stop_match = stop_pattern.search(script, match.end())
    end = stop_match.start() if stop_match else len(script)
    return script[match.start() : end]


def test_refresh_monitor_events_requests_telemetry_kind_filter():
    script = _edge_setup_script()
    body = _extract_function_body(script, "refreshMonitorEvents")

    fetch_match = re.search(r'adminFetch\(\s*"([^"]+)"', body)
    assert fetch_match, (
        'refreshMonitorEvents() does not call adminFetch("<path>", ...) '
        "with a literal path"
    )
    requested_path = fetch_match.group(1)

    assert requested_path.startswith("/api/monitor/events"), requested_path
    assert "kind=telemetry" in requested_path, (
        "refreshMonitorEvents() must request kind=telemetry so the server "
        "applies _is_telemetry_event and UART scan / MQTT heartbeat noise "
        f"cannot evict real telemetry from the trailing limit window "
        f"(requested path was {requested_path!r})"
    )
