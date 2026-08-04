"""Regression tests for remotely reloading the dashboard from Game Admin.

The operator has no way to force the dashboard's projection screen to
refresh without physically rebooting the Pi. This adds POST
/api/dashboard/reload (admin-gated, like the other Game Admin actions),
which broadcasts a `dashboard_reload` WebSocket event; the dashboard
(hub_server/static/index.html) reacts to that event by reloading itself.
Per CLAUDE.md's page-boundary rule, the dashboard has no local controls of
its own -- it only listens for the backend-driven event. The trigger button
lives on Game Admin (hub_server/static/gameAdmin.html).

These tests verify:
1. POST /api/dashboard/reload broadcasts exactly one message, and it is
   exactly {"type": "dashboard_reload"}.
2. It is gated by require_admin like the other admin POST routes (401
   without the token, 200 with the correct token).
3. It works even while the race is RUNNING (no race-state restriction).
4. index.html's onmessage handler has a dashboard_reload branch that calls
   location.reload() -- with JS comments stripped first so a comment can't
   satisfy the assertion while the real branch is missing.
5. gameAdmin.html has the reload button wired with the right data-i18n hook,
   and both the en-US and zh-TW dictionaries define every new i18n key --
   modeled on tests/unit/hub/test_game_admin_station_signpost.py, which
   protects the same kind of i18n hookup.
6. The button's wiring is *actually connected end to end*: its onclick names
   a function that is genuinely defined in gameAdmin.html's script (not
   satisfied by a same-named comment, and not left dangling by a rename),
   and that function's body POSTs to a path that is a real route registered
   on the FastAPI app (so frontend/backend path drift is caught), using
   method "POST". A button with correct i18n but a broken handler still
   looks fine and silently does nothing -- exactly the failure mode this
   feature exists to fix.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub_server.domain.models import RaceConfig
from hub_server.infrastructure.fastapi import app as app_module

client = TestClient(app_module.app)

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

# Comment-stripping helpers (CLAUDE.md: a comment must not satisfy a source
# assertion). Mirrors the technique used in
# tests/unit/hub/test_system_admin_clear_all_stations.py.
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


class FakeWebSocketManager:
    """Captures broadcasts instead of touching real WebSocket connections,
    same technique as tests/unit/hub/test_mqtt_subscriber.py's
    FakeWebSocketManager and tests/unit/hub/test_station_management.py."""

    def __init__(self):
        self.broadcasts = []

    async def broadcast(self, message):
        self.broadcasts.append(message)


@pytest.fixture(autouse=True)
def _reset_race_state():
    app_module.race_manager.reset_race()
    yield
    app_module.race_manager.reset_race()


# ---------------------------------------------------------------------------
# 1. Exactly one broadcast, exact shape
# ---------------------------------------------------------------------------


def test_dashboard_reload_broadcasts_exactly_one_correct_message(monkeypatch):
    fake_ws = FakeWebSocketManager()
    monkeypatch.setattr(app_module, "ws_manager", fake_ws)

    res = client.post("/api/dashboard/reload")

    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    assert fake_ws.broadcasts == [{"type": "dashboard_reload"}]


# ---------------------------------------------------------------------------
# 2. Admin token gate
# ---------------------------------------------------------------------------


def test_dashboard_reload_requires_admin_token_when_configured(monkeypatch):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "admin-secret")
    fake_ws = FakeWebSocketManager()
    monkeypatch.setattr(app_module, "ws_manager", fake_ws)

    res = client.post("/api/dashboard/reload")
    assert res.status_code == 401

    res = client.post(
        "/api/dashboard/reload",
        headers={"X-FitRace-Admin-Token": "admin-secret"},
    )
    assert res.status_code == 200
    assert fake_ws.broadcasts == [{"type": "dashboard_reload"}]


# ---------------------------------------------------------------------------
# 3. Works even while race is RUNNING
# ---------------------------------------------------------------------------


def test_dashboard_reload_succeeds_while_race_running(monkeypatch):
    fake_ws = FakeWebSocketManager()
    monkeypatch.setattr(app_module, "ws_manager", fake_ws)

    app_module.race_manager.configure(
        RaceConfig(race_type="distance", target_value=100.0)
    )
    app_module.race_manager.start_race()
    assert app_module.race_manager.get_state().value == "RUNNING"

    res = client.post("/api/dashboard/reload")

    assert res.status_code == 200
    assert fake_ws.broadcasts == [{"type": "dashboard_reload"}]


# ---------------------------------------------------------------------------
# 4. index.html onmessage handles dashboard_reload
# ---------------------------------------------------------------------------


def test_index_html_onmessage_reloads_on_dashboard_reload_event():
    source = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    script = _strip_js_comments(source[start:end])

    onmessage_start = script.index("ws.onmessage")
    # The handler is a reasonably-sized block; grab a generous window after
    # its start so all typed branches are included.
    window = script[onmessage_start : onmessage_start + 4000]

    assert 'data.type === "dashboard_reload"' in window
    branch_start = window.index('data.type === "dashboard_reload"')
    branch_window = window[branch_start : branch_start + 200]
    assert "location.reload()" in branch_window


# ---------------------------------------------------------------------------
# 5. gameAdmin.html button + i18n hookup
# ---------------------------------------------------------------------------

NEW_I18N_KEYS = [
    "button.reload_dashboard",
    "confirm.reload_dashboard",
]


def _read_game_admin() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def test_reload_dashboard_button_has_data_i18n_attribute():
    source = _read_game_admin()
    assert 'data-i18n="button.reload_dashboard"' in source

    button_marker_idx = source.index('data-i18n="button.reload_dashboard"')
    tag_start = source.rfind("<button", 0, button_marker_idx)
    tag_end = source.index(">", button_marker_idx)
    button_tag = source[tag_start : tag_end + 1]

    assert "onclick=" in button_tag


def test_new_i18n_keys_present_in_both_dictionaries():
    source = _read_game_admin()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 8000]

    for key in NEW_I18N_KEYS:
        assert (
            f'"{key}":' in en_block
        ), f"{key} missing from en-US dictionary in gameAdmin.html"
        assert (
            f'"{key}":' in zh_block
        ), f"{key} missing from zh-TW dictionary in gameAdmin.html"


def test_i18n_keys_are_symmetric():
    source = _read_game_admin()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 8000]

    en_keys = set(re.findall(r'"([^"]+)":\s*"', en_block))
    zh_keys = set(re.findall(r'"([^"]+)":\s*"', zh_block))

    missing_in_zh = en_keys - zh_keys
    missing_in_en = zh_keys - en_keys

    assert not missing_in_zh, f"Keys in en-US but missing in zh-TW: {missing_in_zh}"
    assert not missing_in_en, f"Keys in zh-TW but missing in en-US: {missing_in_en}"


# ---------------------------------------------------------------------------
# 6. The button is really wired: onclick -> a real function -> the real route
# ---------------------------------------------------------------------------


def _game_admin_script() -> str:
    """gameAdmin.html's <script> contents with // and /* */ comments
    stripped, so a comment mentioning a function/path name can't satisfy an
    assertion in place of the real code."""
    source = _read_game_admin()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _reload_button_onclick_handler_name() -> str:
    """Parse the function name out of the onclick="..." attribute on the
    element carrying data-i18n="button.reload_dashboard". Deliberately does
    not hardcode "reloadDashboard" -- if the button's handler is renamed,
    this returns whatever name the button actually calls, so the later
    "is that function defined" check is against the real reference, not a
    literal that would silently stay in sync with nothing."""
    source = _read_game_admin()
    marker = 'data-i18n="button.reload_dashboard"'
    marker_idx = source.index(marker)
    tag_start = source.rfind("<button", 0, marker_idx)
    tag_end = source.index(">", marker_idx)
    tag = source[tag_start : tag_end + 1]

    match = re.search(r'onclick="([A-Za-z_$][\w$]*)\(\)"', tag)
    assert match, f'reload-dashboard button has no onclick="fn()" handler: {tag}'
    return match.group(1)


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
        f"No `function {func_name}(...)` definition found in gameAdmin.html's "
        "script (checked against comment-stripped source, so a comment "
        "mentioning the name does not count)."
    )
    stop_pattern = re.compile(r"\n\s*(?:async\s+)?function\s+\w+\s*\(")
    stop_match = stop_pattern.search(script, match.end())
    end = stop_match.start() if stop_match else len(script)
    return script[match.start() : end]


def test_reload_dashboard_button_onclick_resolves_to_a_defined_function():
    """The button must call a function that genuinely exists in the page's
    script -- not a stale name left dangling by a rename, and not a name
    that only appears inside a comment."""
    script = _game_admin_script()
    handler_name = _reload_button_onclick_handler_name()

    # _extract_function_body already asserts the definition exists; calling
    # it is the check.
    _extract_function_body(script, handler_name)


def test_reload_dashboard_handler_posts_to_registered_dashboard_reload_route():
    """The handler named by the button's onclick must POST to a path that
    is an actual route on the FastAPI app -- pinned against app.routes
    rather than a hardcoded literal, so the frontend and backend paths can
    never silently drift apart -- using method "POST"."""
    script = _game_admin_script()
    handler_name = _reload_button_onclick_handler_name()
    body = _extract_function_body(script, handler_name)

    path_match = re.search(r'fetchJson\(\s*"([^"]+)"', body)
    assert path_match, (
        f'{handler_name}() does not call fetchJson("<path>", ...) with a '
        "literal path"
    )
    called_path = path_match.group(1)

    backend_routes = [
        route
        for route in app_module.app.routes
        if getattr(route, "path", None) == called_path
    ]
    assert backend_routes, (
        f"{handler_name}() POSTs to {called_path!r}, which is not a route "
        "registered on the FastAPI app -- frontend/backend path drift."
    )
    assert any(
        "POST" in getattr(route, "methods", set()) for route in backend_routes
    ), (
        f"{handler_name}() targets {called_path!r}, but no registered route "
        "for that path accepts POST."
    )

    assert re.search(r'method:\s*["\']POST["\']', body), (
        f'{handler_name}() must send its request with method: "POST" '
        f"(found no such literal in its body: {body[:300]!r})"
    )
