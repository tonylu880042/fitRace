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
    if source[max(0, start - 6) : start] == "async ":
        start -= 6
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


READY_OK = "{ready: true}"


def _control(state: str, stations: str, readiness: str = READY_OK) -> dict:
    return _run(
        ["assignedStationNumbers", "controlState"],
        f'controlState("{state}", {stations}, false, {readiness})',
    )


THREE_TEAMS = (
    '{"1": {node_id: "n1", registered: true},'
    ' "2": {node_id: "n2", registered: true},'
    ' "3": {node_id: "n3", registered: true}}'
)


def test_start_stays_disabled_until_the_hub_says_ready():
    assert _control("READY", THREE_TEAMS, "{ready: true}")["canStart"] is True
    assert _control("READY", THREE_TEAMS, "{ready: false}")["canStart"] is False
    assert _control("READY", THREE_TEAMS, "null")["canStart"] is False
    assert _control("READY", THREE_TEAMS, "undefined")["canStart"] is False
    # Readiness only gates Start; Stop / Next Race / Apply are unaffected.
    blocked = _control("STOPPED", THREE_TEAMS, "{ready: false}")
    assert blocked["canNextRace"] is True
    assert _control("READY", THREE_TEAMS, "{ready: false}")["canAssign"] is True


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


def _control_during_countdown(state: str, countdown_active: str) -> dict:
    return _run(
        ["assignedStationNumbers", "controlState"],
        f'controlState("{state}", {ALL_REGISTERED}, {countdown_active}, {READY_OK})',
    )


def test_countdown_disables_apply_next_race_and_start():
    during = _control_during_countdown("READY", "true")
    assert during["canAssign"] is False
    assert during["canNextRace"] is False
    assert during["canStart"] is False
    # Library editing is still allowed in READY; only RUNNING locks it.
    assert during["locked"] is False


def test_countdown_flag_off_or_missing_changes_nothing():
    for flag in ("false", "undefined"):
        result = _control_during_countdown("READY", flag)
        assert result["canAssign"] is True
        assert result["canStart"] is True
    stopped = _control_during_countdown("STOPPED", "false")
    assert stopped["canNextRace"] is True
    assert _control_during_countdown("STOPPED", "true")["canNextRace"] is False


def test_editing_and_assignment_locked_while_running_only():
    for state in ("IDLE", "READY", "RUNNING", "STOPPED"):
        assert _control(state, ALL_REGISTERED)["locked"] is (state == "RUNNING")


# ---- localized readiness ----

JS_MESSAGES = """
const messages = {
  "readiness.no_config": "請先儲存設定",
  "readiness.relay_wrong_size": "第 {station} 站需要 {legs} 位，目前 {count} 位",
  "readiness.sound_disabled": "開始音效已關閉",
  "teams.readiness_blocked_title": "尚未就緒：",
  "teams.readiness_ready": "可以開始",
  "teams.start_blocked": "目前無法開始，請查看整備清單。",
  "state.idle": "待機",
  "state.ready": "準備就緒",
  "state.running": "進行中",
  "state.stopped": "已結束",
  "teams.state_ready": "待開賽",
  "teams.hint_need_one_team": "請至少為一個站位分配隊伍。",
  "teams.warn_stations_without_team": "有 {count} 個站位沒有隊伍",
};
function t(key, params = {}) {
  let value = messages[key] || key;
  Object.entries(params).forEach(([name, replacement]) => {
    value = value.replaceAll(`{${name}}`, String(replacement));
  });
  return value;
}
"""

READINESS_PAYLOAD = """{
  ready: false,
  blocking_issues: ["EN no config", "EN relay size"],
  blocking_items: [
    {code: "no_config", params: {}},
    {code: "relay_wrong_size", params: {station: 2, legs: 3, count: 1}},
  ],
  warnings: ["EN sound", "EN future warning"],
  warning_items: [
    {code: "sound_disabled", params: {}},
    {code: "code_without_translation", params: {}},
  ],
}"""

READINESS_FNS = ["translateIfKnown", "readinessLines"]


def _run_script(functions: list[str], body: str):
    code = _strip_js_comments(_script())
    bodies = "\n".join(_extract_function(code, name) for name in functions)
    script = f"{JS_MESSAGES}\n{bodies}\n{body}"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"node failed: {result.stderr}\n{script}"
    return json.loads(result.stdout)


def test_readiness_lines_use_localized_codes_with_english_fallback():
    lines = _run_script(
        READINESS_FNS,
        f"console.log(JSON.stringify(readinessLines({READINESS_PAYLOAD}, translateIfKnown)));",
    )
    assert lines["blocking"] == ["請先儲存設定", "第 2 站需要 3 位，目前 1 位"]
    # Known code -> localized; code the locale lacks -> the English sentence.
    assert lines["warnings"] == ["開始音效已關閉", "EN future warning"]


def test_readiness_lines_fall_back_to_english_for_an_older_hub_without_items():
    lines = _run_script(
        READINESS_FNS,
        "console.log(JSON.stringify(readinessLines("
        '{ready: false, blocking_issues: ["EN a", "EN b"], warnings: ["EN w"]},'
        " translateIfKnown)));",
    )
    assert lines == {"blocking": ["EN a", "EN b"], "warnings": ["EN w"]}


RENDER_BAR_FNS = [
    "translateIfKnown",
    "readinessLines",
    "assignedStationNumbers",
    "controlState",
    "raceStateName",
    "relayLegs",
    "countdownActive",
    "escapeHtml",
    "readinessListHtml",
    "renderRaceBar",
]


def _render_race_bar(readiness_js: str) -> dict:
    return _run_script(
        RENDER_BAR_FNS,
        f"""
const out = {{}};
function $(id) {{
  if (!out[id]) out[id] = {{ textContent: "", disabled: false, className: "" }};
  return out[id];
}}
const state = {{
  race: {{ state: "READY", config: {{ competition_mode: "relay", relay_legs: 2 }} }},
  stations: {{ stations: {{}} }},
  readiness: {readiness_js},
  busy: false,
}};
renderRaceBar();
console.log(JSON.stringify({{
  line: out["readiness-line"].innerHTML,
  warnings: out["readiness-warnings"].innerHTML,
}}));
""",
    )


def _li(html: str) -> list[str]:
    return re.findall(r"<li>(.*?)</li>", html)


def test_race_bar_lists_each_localized_blocking_issue_in_its_own_li():
    result = _render_race_bar(READINESS_PAYLOAD)
    assert result["line"].startswith("<div")
    assert "尚未就緒：" in result["line"].split("<ul")[0]
    assert _li(result["line"]) == ["請先儲存設定", "第 2 站需要 3 位，目前 1 位"]
    assert "EN" not in result["line"]
    # Warnings too: one <li> each, English only where no translation exists.
    assert _li(result["warnings"]) == ["開始音效已關閉", "EN future warning"]


def test_three_blocking_items_render_three_list_items():
    result = _render_race_bar(
        "{ready: false,"
        ' blocking_issues: ["EN a", "EN b", "EN c"],'
        ' blocking_items: [{code: "no_config", params: {}},'
        ' {code: "no_config", params: {}}, {code: "sound_disabled", params: {}}],'
        " warnings: [], warning_items: []}"
    )
    assert _li(result["line"]) == ["請先儲存設定", "請先儲存設定", "開始音效已關閉"]
    assert result["warnings"] == ""


def test_issue_text_is_html_escaped_in_the_lists():
    result = _render_race_bar(
        '{ready: false, blocking_issues: ["bad <b>bold</b> & more"],'
        ' blocking_items: [], warnings: ["warn <i>x</i>"], warning_items: []}'
    )
    assert "<b>" not in result["line"] and "<i>" not in result["warnings"]
    assert _li(result["line"]) == ["bad &lt;b&gt;bold&lt;/b&gt; &amp; more"]
    assert _li(result["warnings"]) == ["warn &lt;i&gt;x&lt;/i&gt;"]


def test_race_bar_shows_ready_text_and_no_warnings_when_ready():
    result = _render_race_bar(
        "{ready: true, blocking_issues: [], blocking_items: [],"
        " warnings: [], warning_items: []}"
    )
    assert "可以開始" in result["line"]
    assert "<li>" not in result["line"]
    assert result["warnings"] == ""


def _bar(raceState: str, readiness_js: str) -> dict:
    return _run_script(
        RENDER_BAR_FNS,
        f"""
const out = {{}};
function $(id) {{
  if (!out[id]) out[id] = {{ textContent: "", innerHTML: "", disabled: false, className: "" }};
  return out[id];
}}
const state = {{
  race: {{ state: "{raceState}", config: {{ competition_mode: "relay", relay_legs: 2 }} }},
  stations: {{ stations: {{ "1": {{ node_id: "n1", registered: true }} }} }},
  readiness: {readiness_js},
  busy: false,
}};
renderRaceBar();
console.log(JSON.stringify({{
  badge: out["race-state-badge"].textContent,
  startDisabled: out["btn-start"].disabled,
}}));
""",
    )


def test_start_button_is_disabled_while_readiness_blocks_and_enabled_when_ready():
    blocked = _bar("READY", READINESS_PAYLOAD)
    assert blocked["startDisabled"] is True
    ready = _bar(
        "READY",
        "{ready: true, blocking_issues: [], blocking_items: [],"
        " warnings: [], warning_items: []}",
    )
    assert ready["startDisabled"] is False
    assert _bar("READY", "null")["startDisabled"] is True


def _notices(stations_js: str, readiness_js: str = "{ready: false}") -> dict:
    return _run_script(
        RENDER_BAR_FNS,
        f"""
const out = {{}};
function $(id) {{
  if (!out[id]) out[id] = {{ textContent: "", innerHTML: "", disabled: false, className: "" }};
  return out[id];
}}
const state = {{
  race: {{ state: "READY", config: {{ competition_mode: "relay", relay_legs: 2 }} }},
  stations: {{ stations: {stations_js} }},
  readiness: {readiness_js},
  busy: false,
}};
renderRaceBar();
console.log(JSON.stringify({{
  hint: out["race-notice"].textContent,
  warning: out["start-warning"].textContent,
}}));
""",
    )


def test_ready_with_no_teams_shows_the_assign_hint_and_no_empty_station_warning():
    # Even with readiness blocking, the operator is told what to do about
    # teams; the "N stations have no team" warning would be nonsense here.
    result = _notices(NONE_REGISTERED)
    assert result == {"hint": "請至少為一個站位分配隊伍。", "warning": ""}


def test_ready_with_some_teams_warns_about_empty_stations_and_shows_no_hint():
    # 2 of 3 stations have a team, readiness blocking for another reason.
    stations = (
        '{"1": {node_id: "n1", registered: true},'
        ' "2": {node_id: "n2", registered: true},'
        ' "3": {node_id: "n3", registered: false}}'
    )
    for readiness in ("{ready: false}", "{ready: true}", "null"):
        result = _notices(stations, readiness)
        assert result == {"hint": "", "warning": "有 1 個站位沒有隊伍"}


def test_ready_with_every_station_registered_shows_neither_notice():
    assert _notices(ALL_REGISTERED) == {"hint": "", "warning": ""}


def test_badge_names_the_race_state_not_readiness():
    # READY must not read "ready" (就緒) next to a "not ready" line.
    assert _bar("READY", "null")["badge"] == "待開賽"
    assert _bar("IDLE", "null")["badge"] == "待機"
    assert _bar("RUNNING", "null")["badge"] == "進行中"
    assert _bar("STOPPED", "null")["badge"] == "已結束"


START_FAILURE_FNS = [
    "translateIfKnown",
    "readinessLines",
    "startFailureMessage",
    "explainStartFailure",
    "fetchJson",
    "refreshReadiness",
    "raceCommand",
    "startRace",
]


def _start_with_409(readiness_js: str) -> list:
    return _run_script(
        START_FAILURE_FNS,
        f"""
const calls = [];
const state = {{ busy: false, race: {{}}, stations: {{ stations: {{}} }}, readiness: null }};
function renderAll() {{}}
function openLogin() {{}}
function adminHeaders() {{ return {{}}; }}
function setMessage(id, text, kind) {{ calls.push([id, text, kind]); }}
async function refreshRace() {{}}
async function refreshStations() {{}}
globalThis.fetch = async (url) => {{
  if (url.startsWith("/api/race/countdown-start")) {{
    return {{ ok: false, status: 409, statusText: "Conflict",
      text: async () => JSON.stringify({{ detail: "EN raw 409 detail" }}) }};
  }}
  return {{ ok: true, status: 200, statusText: "OK",
    text: async () => JSON.stringify({readiness_js}) }};
}};
startRace().then(() => console.log(JSON.stringify(calls)));
""",
    )


def test_start_409_shows_the_localized_line_instead_of_the_raw_detail():
    calls = _start_with_409(READINESS_PAYLOAD)
    assert calls == [["race-message", "目前無法開始，請查看整備清單。", "error"]]


def test_start_409_for_a_non_readiness_reason_keeps_the_hub_detail():
    calls = _start_with_409(
        "{ready: true, blocking_issues: [], blocking_items: [],"
        " warnings: [], warning_items: []}"
    )
    assert calls == [["race-message", "EN raw 409 detail", "error"]]
