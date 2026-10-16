"""The /teams page (hub_server/static/teams.html): relay team library, team to
station assignment and Start/Stop/Next Race.

Decision logic lives in small pure functions that are pulled out of the
page's inline <script> by brace matching and executed under `node` (same
technique as test_game_admin_roster_panel.py) -- real code, never a source
text grep, so a nearby comment cannot satisfy these tests.
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi.app import app
from hub_server.infrastructure.locales import SUPPORTED_LOCALES, load_locale

PAGE = Path(__file__).resolve().parents[3] / "hub_server" / "static" / "teams.html"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _source() -> str:
    return PAGE.read_text(encoding="utf-8")


def _script() -> str:
    match = re.search(r"<script>(.*?)</script>", _source(), re.DOTALL)
    assert match, "no inline <script> in teams.html"
    return match.group(1)


def _strip_js_comments(code: str) -> str:
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", code))


def _matching_end(source: str, open_idx: int, open_ch: str, close_ch: str) -> int:
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
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching closer")


def _extract_function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    paren_open = source.index("(", start)
    paren_end = _matching_end(source, paren_open, "(", ")")
    brace_open = source.index("{", paren_end)
    brace_end = _matching_end(source, brace_open, "{", "}")
    return source[start : brace_end + 1]


def _run(functions: list[str], expression: str):
    code = _strip_js_comments(_script())
    bodies = "\n".join(_extract_function(code, name) for name in functions)
    script = f"{bodies}\nconsole.log(JSON.stringify({expression}));"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"node failed: {result.stderr}\n{script}"
    return json.loads(result.stdout)


# ---- page serves ----


def test_teams_route_redirects_to_the_static_page():
    response = TestClient(app).get("/teams", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/static/teams.html"
    assert TestClient(app).get("/static/teams.html").status_code == 200


def test_inline_script_is_syntactically_valid_js():
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(_script())
        path = handle.name
    try:
        result = subprocess.run(
            ["node", "--check", path], capture_output=True, text=True, timeout=10
        )
    finally:
        Path(path).unlink()
    assert result.returncode == 0, result.stderr


# ---- i18n ----


def test_page_has_no_hardcoded_cjk_text():
    assert not re.search(r"[一-鿿]", _strip_js_comments(_source()))


def test_every_teams_key_used_by_the_page_exists_in_every_locale():
    used = set(re.findall(r"""["'](teams\.[a-z_]+)["']""", _source()))
    assert len(used) > 20, "suspiciously few teams.* keys found"
    for locale in SUPPORTED_LOCALES:
        messages = load_locale(locale)["messages"]
        missing = sorted(k for k in used if k not in messages)
        assert not missing, f"{locale} lacks {missing}"


def test_zh_tw_and_en_us_texts_are_really_translated():
    zh = load_locale("zh-TW")["messages"]
    en = load_locale("en-US")["messages"]
    teams_keys = [k for k in en if k.startswith("teams.")]
    assert teams_keys
    assert en["teams.title"] == "Relay Teams"
    for key in teams_keys:
        assert re.search(r"[一-鿿]", zh[key]), f"{key} is not zh-TW text"
        assert not re.search(r"[一-鿿]", en[key]), f"{key} has CJK in en-US"


# ---- pure helpers ----


def test_parse_members_trims_and_drops_blank_lines():
    assert _run(
        ["parseMembers"], 'parseMembers("  Alice \\n\\n  \\nBob\\r\\n Cara")'
    ) == ["Alice", "Bob", "Cara"]
    assert _run(["parseMembers"], 'parseMembers("")') == []


def test_member_counter_state_flags_wrong_size():
    fns = ["parseMembers", "memberCounterState"]
    assert _run(fns, 'memberCounterState("a\\nb", 2)') == {
        "count": 2,
        "legs": 2,
        "ok": True,
    }
    assert _run(fns, 'memberCounterState("a\\nb\\nc", 2)')["ok"] is False
    assert _run(fns, 'memberCounterState("a", 2)')["ok"] is False
    # Legs unknown (no relay config yet) -> nothing to compare against.
    unknown = _run(fns, 'memberCounterState("a\\nb", null)')
    assert unknown["count"] == 2 and unknown["legs"] is None and unknown["ok"] is True


TEAMS = '[{id:"a",name:"A"},{id:"b",name:"B"},{id:"c",name:"C"}]'


def test_available_teams_hides_teams_chosen_in_other_selects():
    ids = _run(
        ["availableTeamsFor"],
        f'availableTeamsFor(2, {{1: "a", 2: "b", 3: ""}}, {TEAMS}).map(t => t.id)',
    )
    assert ids == ["b", "c"]  # "a" is taken by station 1; own choice stays


def test_available_teams_ignores_empty_and_missing_selections():
    ids = _run(
        ["availableTeamsFor"],
        f"availableTeamsFor(1, {{}}, {TEAMS}).map(t => t.id)",
    )
    assert ids == ["a", "b", "c"]


def test_build_assignments_payload_sends_every_station_with_null_for_empty():
    payload = _run(
        ["buildAssignmentsPayload"],
        'buildAssignmentsPayload([1, 2, 3], {1: "a", 2: "", 3: undefined})',
    )
    assert payload == {"assignments": {"1": "a", "2": None, "3": None}}


def test_selections_from_stations_match_registered_team_names():
    selections = _run(
        ["assignedStationNumbers", "selectionsFromStations"],
        "selectionsFromStations({"
        '"1": {node_id: "n1", registered: true, team_name: "B"},'
        '"2": {node_id: "n2", registered: false, team_name: null},'
        '"3": {node_id: "n3", registered: true, team_name: "Ghost"},'
        '"4": {node_id: null, registered: true, team_name: "A"}'
        f"}}, {TEAMS})",
    )
    # Station 4 has no node, so it is not an assignable station at all.
    assert selections == {"1": "b", "2": "", "3": ""}


def test_selections_from_stations_resolves_names_case_insensitively():
    selections = _run(
        ["assignedStationNumbers", "selectionsFromStations"],
        "selectionsFromStations({"
        '"1": {node_id: "n1", registered: true, team_name: "a"},'
        '"2": {node_id: "n2", registered: false, team_name: null}'
        f"}}, {TEAMS})",
    )
    assert selections == {"1": "a", "2": ""}


def _control(state: str, stations: str) -> dict:
    return _run(
        ["assignedStationNumbers", "controlState"],
        f'controlState("{state}", {stations})',
    )


ALL_REGISTERED = (
    '{"1": {node_id: "n1", registered: true},'
    ' "2": {node_id: "n2", registered: true}}'
)
ONE_MISSING = (
    '{"1": {node_id: "n1", registered: true},'
    ' "2": {node_id: "n2", registered: false}}'
)


NONE_REGISTERED = (
    '{"1": {node_id: "n1", registered: false},'
    ' "2": {node_id: "n2", registered: false}}'
)


def test_start_needs_ready_and_at_least_one_registered_assigned_station():
    assert _control("READY", ALL_REGISTERED)["canStart"] is True
    assert _control("READY", ONE_MISSING)["canStart"] is True
    assert _control("READY", NONE_REGISTERED)["canStart"] is False
    assert _control("READY", "{}")["canStart"] is False
    for state in ("IDLE", "RUNNING", "STOPPED"):
        assert _control(state, ALL_REGISTERED)["canStart"] is False


def test_start_with_8_stations_and_6_teams_warns_about_the_2_empty_ones():
    stations = (
        "(() => { const s = {};"
        " for (let i = 1; i <= 8; i++)"
        " s[String(i)] = {node_id: 'n' + i, registered: i <= 6};"
        " return s; })()"
    )
    result = _control("READY", stations)
    assert result["canStart"] is True
    assert result["unregisteredCount"] == 2


def test_unregistered_count_is_zero_when_all_registered_or_no_stations():
    assert _control("READY", ALL_REGISTERED)["unregisteredCount"] == 0
    assert _control("READY", "{}")["unregisteredCount"] == 0
    assert _control("READY", ONE_MISSING)["unregisteredCount"] == 1


def test_start_ignores_registered_stations_without_a_node():
    stations = (
        '{"1": {node_id: "n1", registered: true},'
        ' "7": {node_id: null, registered: false}}'
    )
    assert _control("READY", stations)["canStart"] is True


def test_stop_only_when_running_and_next_race_only_when_stopped():
    for state in ("IDLE", "READY", "RUNNING", "STOPPED"):
        result = _control(state, ALL_REGISTERED)
        assert result["canStop"] is (state == "RUNNING")
        assert result["canNextRace"] is (state == "STOPPED")


def test_editing_and_assignment_locked_while_running_only():
    for state in ("IDLE", "READY", "RUNNING", "STOPPED"):
        assert _control(state, ALL_REGISTERED)["locked"] is (state == "RUNNING")
