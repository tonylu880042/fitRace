"""Regression tests for the one-click Hub update feature on System Admin
(hub_server/static/systemAdmin.html).

These tests follow the same source-extraction technique used in
tests/unit/hub/test_system_admin_clear_all_stations.py: pull the exact body
of the function under test out of the page's inline <script>, then assert on
that narrow window.
"""

import re
import subprocess
import tempfile
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read() -> str:
    return (STATIC_DIR / "systemAdmin.html").read_text(encoding="utf-8")


def _script(source: str) -> str:
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return source[start:end]


def _extract_function(source: str, signature: str, stop_markers) -> str:
    """Return source text starting at `signature` (e.g. "function foo(") up
    to (not including) whichever of `stop_markers` appears first after it."""
    start = source.index(signature)
    window = source[start : start + 8000]
    stops = [window.index(marker) for marker in stop_markers if marker in window[1:]]
    end = min(stops) + 1 if stops else len(window)
    return window[:end]


NEXT_FN = "\n    function "
NEXT_ASYNC_FN = "\n    async function "
STOPS = [NEXT_FN, NEXT_ASYNC_FN]

# Comment-stripping helpers (CLAUDE.md: a comment must not satisfy a source assertion)
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    """Strip JS comments so a `//` comment can't silently satisfy a
    source-text assertion."""
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _matching_brace_end(source: str, open_idx: int) -> int:
    """Return the index of the "}" that matches the "{" at open_idx,
    tracking string literals so braces inside quoted values don't throw off
    the depth count."""
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


def _extract_dictionaries_js(source: str) -> str:
    """Pull the `const dictionaries = {...}` and the following
    `dictionaries["zh-TW"] = {...}` statements out of a static page."""
    const_start = source.index("const dictionaries = {")
    const_open = source.index("{", const_start)
    const_close = _matching_brace_end(source, const_open)

    zh_marker = 'dictionaries["zh-TW"] = {'
    zh_start = source.index(zh_marker, const_close)
    zh_open = source.index("{", zh_start)
    zh_close = _matching_brace_end(source, zh_open)

    return source[const_start : zh_close + 1] + ";"


# ---------------------------------------------------------------------------
# 1. One-click button exists and old buttons are gone
# ---------------------------------------------------------------------------


def test_one_click_hub_update_button_exists():
    """The Software panel's #update-actions div must contain exactly ONE
    button with onclick="oneClickHubUpdate()" and id="btn-update-hub"."""
    source = _read()
    panel_start = source.index('id="panel-software"')
    panel_end = source.index("</section>", panel_start)
    panel_section = source[panel_start:panel_end]

    # New button must exist
    assert 'id="btn-update-hub"' in panel_section
    assert 'onclick="oneClickHubUpdate()"' in panel_section

    # Old buttons must be gone from #update-actions div
    update_actions_start = panel_section.index('id="update-actions"')
    update_actions_end = panel_section.index("</div>", update_actions_start)
    update_actions_div = panel_section[update_actions_start:update_actions_end]

    assert 'onclick="downloadUpdates()"' not in update_actions_div
    assert 'onclick="installHubUpdate()"' not in update_actions_div
    assert 'onclick="applyHubUpdate()"' not in update_actions_div


# ---------------------------------------------------------------------------
# 2. New i18n keys exist in both dictionaries
# ---------------------------------------------------------------------------


def test_new_i18n_keys_present_in_both_dictionaries():
    """All new i18n keys for one-click update must exist in both en-US and
    zh-TW inline dictionaries."""
    source = _read()
    dicts_js = _extract_dictionaries_js(source)

    # Required keys
    required_keys = [
        "button.update_hub",
        "confirm.update_hub",
        "message.update_hub_downloading",
        "message.update_hub_restarting",
        "message.update_hub_timeout",
    ]

    en_start = dicts_js.index('"en-US": {')
    zh_start = dicts_js.index('dictionaries["zh-TW"] = {')
    en_block = dicts_js[en_start:zh_start]
    zh_block = dicts_js[zh_start : zh_start + 8000]

    for key in required_keys:
        assert f'"{key}":' in en_block, f"{key} missing from en-US dictionary"
        assert f'"{key}":' in zh_block, f"{key} missing from zh-TW dictionary"

    # Keys should contain {version} placeholder where appropriate
    en_start_idx = dicts_js.index('"button.update_hub"')
    en_end_idx = en_start_idx + 100
    button_key_text = dicts_js[en_start_idx:en_end_idx]
    assert "{version}" in button_key_text

    confirm_start = dicts_js.index('"confirm.update_hub"')
    confirm_end = confirm_start + 150
    confirm_text = dicts_js[confirm_start:confirm_end]
    assert "{version}" in confirm_text


# ---------------------------------------------------------------------------
# 3. oneClickHubUpdate function implementation
# ---------------------------------------------------------------------------


def test_one_click_hub_update_function_calls_confirm():
    """The oneClickHubUpdate function must call window.confirm() with the
    confirm.update_hub i18n key before calling fetchJson."""
    source = _script(_read())
    body = _extract_function(source, "async function oneClickHubUpdate(", STOPS)

    # Must call window.confirm
    assert "window.confirm(" in body
    assert "confirm.update_hub" in body

    # Must call fetchJson for /api/updates/hub
    assert 'fetchJson("/api/updates/hub"' in body

    # Confirm must happen before the fetch
    confirm_idx = body.index("window.confirm(")
    fetch_idx = body.index('fetchJson("/api/updates/hub"')
    assert confirm_idx < fetch_idx, "confirm must happen before fetchJson"


def test_one_click_hub_update_disables_button():
    """The oneClickHubUpdate function must set btn-update-hub.disabled = true
    before calling fetchJson."""
    source = _script(_read())
    body = _extract_function(source, "async function oneClickHubUpdate(", STOPS)

    # Must disable the button before fetch
    assert "disabled = true" in body or ".disabled = true" in body
    assert "btn-update-hub" in body

    # Button must be disabled before fetch
    disable_idx = body.index(".disabled = true")
    fetch_idx = body.index('fetchJson("/api/updates/hub"')
    assert disable_idx < fetch_idx


def test_one_click_hub_update_handles_error():
    """On error, the function must call setMessage with error.message and
    re-enable the button."""
    source = _script(_read())
    body = _extract_function(source, "async function oneClickHubUpdate(", STOPS)

    # Must have catch block
    assert "catch (error)" in body

    # Must call setMessage with error.message
    assert 'setMessage("update-message", error.message, "error")' in body or (
        'setMessage("update-message"' in body and "error.message" in body
    )

    # Must re-enable button on error (look for .disabled = false in catch)
    catch_match = re.search(
        r"catch\s*\(\s*error\s*\)\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
        body,
        re.DOTALL,
    )
    if catch_match:
        catch_body = catch_match.group(1)
        catch_stripped = _strip_js_comments(catch_body)
        assert ".disabled = false" in catch_stripped


def test_one_click_hub_update_no_hardcoded_strings():
    """The oneClickHubUpdate function must not contain hardcoded English or
    Chinese status strings -- all strings passed to setMessage() and
    window.confirm() must route through t()."""
    source = _script(_read())
    body = _extract_function(source, "async function oneClickHubUpdate(", STOPS)

    body_stripped = _strip_js_comments(body)

    # Assert: no setMessage with a bare string literal as second arg
    # Pattern: setMessage("...", "string literal that's NOT a t(...) call)
    # This catches setMessage("update-message", "Downloading...", "ok")
    bare_setmessage = re.findall(r'setMessage\([^,]+,\s*"[^{]', body_stripped)
    assert (
        not bare_setmessage
    ), f"setMessage calls must use t(...) not bare strings: {bare_setmessage}"

    # Also check for single quotes
    bare_setmessage_single = re.findall(r"setMessage\([^,]+,\s*'[^{]", body_stripped)
    assert (
        not bare_setmessage_single
    ), f"setMessage calls must use t(...) not bare strings: {bare_setmessage_single}"

    # Assert: no window.confirm with a bare string literal
    # Pattern: window.confirm("string literal that's NOT a t(...) call)
    bare_confirm = re.findall(r'window\.confirm\(\s*"[^{]', body_stripped)
    assert (
        not bare_confirm
    ), f"window.confirm calls must use t(...) not bare strings: {bare_confirm}"

    bare_confirm_single = re.findall(r"window\.confirm\(\s*'[^{]", body_stripped)
    assert (
        not bare_confirm_single
    ), f"window.confirm calls must use t(...) not bare strings: {bare_confirm_single}"


def test_one_click_hub_update_calls_polling_helper():
    """After successful /api/updates/hub response, the function must call a
    polling helper (e.g., pollHubRestart) to wait for the Hub to come back."""
    source = _script(_read())
    body = _extract_function(source, "async function oneClickHubUpdate(", STOPS)

    # Must call setMessage with "message.update_hub_restarting"
    assert "message.update_hub_restarting" in body

    # Must call a polling/restart-waiting function
    # Look for something like pollHubRestart or similar
    assert (
        re.search(r"poll\w+\(", body)
        or "setInterval" in body
        or ("setTimeout" in body and "location.reload" in body)
    )


# ---------------------------------------------------------------------------
# 4. Polling helper exists and implements timeout
# ---------------------------------------------------------------------------


def test_hub_restart_polling_helper_exists():
    """Must have a polling function (pollHubRestart or similar) that:
    - Calls fetchJson("/health")
    - Compares the returned version to target version
    - Calls window.location.reload() on match
    - Times out after ~3 minutes with a setMessage call"""
    source = _script(_read())

    # Look for a polling function
    polling_fn = None
    for marker in ["pollHubRestart", "waitForRestart", "pollHealth"]:
        if f"function {marker}(" in source:
            polling_fn = marker
            break

    # If no exact match, look for an async function that fetches /health
    if not polling_fn:
        health_fetch_match = re.search(
            r"async function (\w+).*?fetchJson.*?/health", source, re.DOTALL
        )
        if health_fetch_match:
            polling_fn = health_fetch_match.group(1)

    assert polling_fn, "No polling/restart-wait function found in JS"

    # Extract the polling function
    body = _extract_function(source, f"async function {polling_fn}(", STOPS)

    # Must fetch /health
    assert 'fetchJson("/health"' in body or 'fetchJson("/health"' in body

    # Must check version
    assert "version" in body

    # Must call window.location.reload()
    assert "window.location.reload()" in body or "location.reload()" in body

    # Must have a timeout (~3 minutes = 180000ms)
    # Look for a numeric constant around 180000, 3*60*1000, or similar
    timeout_patterns = [
        r"3\s*\*\s*60\s*\*\s*1000",
        r"180000",
        r"180_000",
        r"setInterval.*?\d{4,}",
    ]
    timeout_found = any(re.search(pattern, body) for pattern in timeout_patterns)
    assert timeout_found, "No timeout constant (~3 minutes) found in polling helper"

    # Must have setMessage call with timeout message
    assert "message.update_hub_timeout" in body


def test_hub_restart_polling_helper_has_delay():
    """The polling helper must delay between polls (e.g., setTimeout or await
    sleep) to avoid hammering the Hub."""
    source = _script(_read())

    # Find the polling function
    polling_fn = None
    for marker in ["pollHubRestart", "waitForRestart", "pollHealth"]:
        if f"function {marker}(" in source:
            polling_fn = marker
            break

    if not polling_fn:
        health_fetch_match = re.search(
            r"async function (\w+).*?fetchJson.*?/health", source, re.DOTALL
        )
        if health_fetch_match:
            polling_fn = health_fetch_match.group(1)

    assert polling_fn, "No polling function found"

    body = _extract_function(source, f"async function {polling_fn}(", STOPS)

    # Must have either await asyncio.sleep or setTimeout
    # Look for setTimeout, setInterval, or await sleep patterns
    assert (
        "setTimeout" in body or "setInterval" in body or "await" in body
    ), "Polling helper must delay between polls"

    # Look for a delay constant around 2000ms (2 seconds)
    assert re.search(
        r"2000|2_000|2\s*\*\s*1000", body
    ), "Polling helper should delay ~2000ms between polls"


# ---------------------------------------------------------------------------
# 4b. oneClickHubUpdate preserves prior state fields (no clobbering)
# ---------------------------------------------------------------------------


def test_one_click_hub_update_merges_state_not_clobbers():
    """The oneClickHubUpdate function must merge the response into state.update,
    not replace it entirely. This preserves latest_hub_version, current_version,
    and other fields so the detail display doesn't collapse to all '--' during
    the update."""
    source = _script(_read())
    body = _extract_function(source, "async function oneClickHubUpdate(", STOPS)

    body_stripped = _strip_js_comments(body)

    # Must use spread/merge syntax: state.update = { ...state.update, ...response }
    # or similar, NOT a bare assignment like state.update = response
    has_merge = re.search(r"state\.update\s*=\s*\{.*\.\.\.state\.update", body_stripped)
    assert (
        has_merge
    ), "state.update must use merge syntax { ...state.update, ...response } to preserve fields"

    # Assert that the bare-replace anti-pattern is absent
    # (but allow it if it's clearly being used to set up a merge, e.g. in a loop or condition)
    bare_replace_pattern = r"state\.update\s*=\s*(?!.*\.\.\.state\.update)(?!.*\.\.\.response)\s*(?:response|await)"
    assert not re.search(
        bare_replace_pattern, body_stripped
    ), "state.update must not be replaced by a bare assignment (await fetchJson(...)); must use merge syntax"


# ---------------------------------------------------------------------------
# 5. JavaScript syntax check
# ---------------------------------------------------------------------------


def test_system_admin_page_js_syntax_is_valid():
    """The <script> block in systemAdmin.html must be valid JavaScript,
    verifiable by running `node --check` on it."""
    source = _read()
    script_content = _script(source)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp:
        tmp.write(script_content)
        tmp.flush()
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ["node", "--check", tmp_path], capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0, f"node --check failed: {result.stderr}"
    finally:
        Path(tmp_path).unlink()
