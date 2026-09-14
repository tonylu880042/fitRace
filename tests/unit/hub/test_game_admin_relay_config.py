"""Game Admin (hub_server/static/gameAdmin.html) gains a "relay" Competition
option: a relay race splits a distance target equally across a team-size
number of legs (relay_legs, 2..10), each leg run by a different roster
member on the SAME machine. This covers:

  * isRelayCompetitionMode(mode) -- trivial predicate.
  * relayMembersFromTextarea(text) -- normalizes the members textarea (one
    name per line) into a clean roster, mirroring
    RegisterAthletePayload._clean_relay_members in
    hub_server/infrastructure/fastapi/app.py.
  * buildRelayRegistrationFormHtml(stationKey, station) -- the pure markup
    builder for a station's team-name + roster registration form, executed
    for real under node (not a source-text grep).
  * syncCompetitionFields() -- when "relay" is selected, forces race type to
    distance (and disables the selector), shows the legs field, and hides
    the team-scoring/completion fields exactly as for "individual" -- run
    against a light fake-DOM harness mirroring
    test_game_admin_race_rule_guidance.py's _run_sync_competition_fields.
  * teamRuleSummaryText -- returns the relay-specific guidance sentence.
  * Wiring: renderStations() calls the form builder only for a relay
    config, and configureRace() includes relay_legs in the POST payload.
  * i18n: every new key exists in both inline dictionaries with genuinely
    different zh-TW copy (test_static_page_i18n.py's symmetry test already
    enforces this generically; this file additionally pins the exact
    copy the spec calls for).
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


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _stripped_script() -> str:
    source = _read()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _matching_brace_end(source: str, open_idx: int) -> int:
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


def _matching_paren_end(source: str, open_idx: int) -> int:
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
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching closing paren found")


def _extract_function(source: str, name: str, async_fn: bool = False) -> str:
    # Finds the end of the parameter list FIRST (via paren matching), then
    # the function body's opening brace after it -- a destructured
    # parameter like `function f({ a, b }) {...}` has a "{" inside its own
    # parameter list that a naive "first { after the marker" search would
    # mistake for the body's opening brace, truncating the extraction.
    #
    # async_fn=True matches on "async function NAME(" starting at "async"
    # itself -- matching on the bare "function NAME(" substring would find
    # that same text starting AFTER "async ", silently dropping the async
    # keyword from the extracted source and turning every `await` inside it
    # into a syntax error under plain node.
    marker = f"{'async ' if async_fn else ''}function {name}("
    start = source.index(marker)
    paren_open = source.index("(", start)
    paren_close = _matching_paren_end(source, paren_open)
    brace_open = source.index("{", paren_close)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# isRelayCompetitionMode -- trivial pure predicate
# ---------------------------------------------------------------------------


def test_is_relay_competition_mode():
    source = _stripped_script()
    fn = _extract_function(source, "isRelayCompetitionMode")
    script = (
        fn + "\n" + 'console.log(JSON.stringify([isRelayCompetitionMode("relay"), '
        'isRelayCompetitionMode("team"), isRelayCompetitionMode("individual")]));'
    )
    assert json.loads(_run_node(script)) == [True, False, False]


# ---------------------------------------------------------------------------
# relayMembersFromTextarea -- pure normalization
# ---------------------------------------------------------------------------


def _run_relay_members_from_textarea(text_js: str) -> list:
    source = _stripped_script()
    fn = _extract_function(source, "relayMembersFromTextarea")
    script = (
        fn + "\n" + f"console.log(JSON.stringify(relayMembersFromTextarea({text_js})));"
    )
    return json.loads(_run_node(script))


def test_relay_members_textarea_trims_and_drops_blank_lines():
    out = _run_relay_members_from_textarea('"  Alice  \\n\\n  Bob\\n \\nCara\\n"')
    assert out == ["Alice", "Bob", "Cara"]


def test_relay_members_textarea_empty_gives_empty_list():
    assert _run_relay_members_from_textarea('""') == []
    assert _run_relay_members_from_textarea("null") == []


# ---------------------------------------------------------------------------
# buildRelayRegistrationFormHtml -- pure markup builder, real execution
# ---------------------------------------------------------------------------


def _t_stub() -> str:
    return (
        "const t = (key) => ({"
        '"label.relay_team_name": "Team Name",'
        '"label.relay_members": "Members (one per line)",'
        '"button.register_relay_team": "Register Team"'
        "}[key] || key);\n"
    )


def _escape_html_stub() -> str:
    return (
        "function escapeHtml(value) {\n"
        "  return String(value === null || value === undefined ? '' : value)\n"
        "    .replace(/&/g, '&amp;')\n"
        "    .replace(/</g, '&lt;')\n"
        "    .replace(/>/g, '&gt;')\n"
        "    .replace(/\"/g, '&quot;');\n"
        "}\n"
    )


def _run_build_relay_form(station_key: str, station_js: str) -> str:
    source = _stripped_script()
    fn = _extract_function(source, "buildRelayRegistrationFormHtml")
    script = (
        _t_stub()
        + _escape_html_stub()
        + fn
        + "\n"
        + f"console.log(buildRelayRegistrationFormHtml({json.dumps(station_key)}, {station_js}));"
    )
    return _run_node(script)


def test_relay_form_shows_existing_team_name_and_members():
    html = _run_build_relay_form(
        "1", '{team_name: "Volt", relay_members: ["Alice", "Bob"]}'
    )
    assert 'value="Volt"' in html
    assert "Alice\nBob" in html
    assert (
        "registerRelayTeam(&#039;1&#039;)" in html or "registerRelayTeam('1')" in html
    )


def test_relay_form_blank_for_unregistered_station():
    html = _run_build_relay_form("2", "{}")
    assert 'value=""' in html
    assert 'id="relay-team-name-2"' in html
    assert 'id="relay-members-2"' in html


# ---------------------------------------------------------------------------
# teamRuleSummaryText -- relay branch
# ---------------------------------------------------------------------------


def _run_team_rule_summary(params_js: str) -> str:
    source = _stripped_script()
    fn = _extract_function(source, "teamRuleSummaryText")
    script = (
        "const t = (key) => ({"
        '"text.relay_race": "Relay guidance text",'
        '"text.individual_race": "Individual guidance text"'
        "}[key] || key);\n"
        + fn
        + "\n"
        + f"console.log(teamRuleSummaryText({params_js}));"
    )
    return _run_node(script)


def test_team_rule_summary_shows_relay_text_when_relay():
    out = _run_team_rule_summary(
        '{isTeamRace: false, isRelayRace: true, scoring: "average", '
        'completion: "aggregate", raceType: "distance", targetLabel: "x"}'
    )
    assert out == "Relay guidance text"


def test_team_rule_summary_still_shows_individual_text_when_not_relay():
    out = _run_team_rule_summary(
        '{isTeamRace: false, isRelayRace: false, scoring: "average", '
        'completion: "aggregate", raceType: "distance", targetLabel: "x"}'
    )
    assert out == "Individual guidance text"


# ---------------------------------------------------------------------------
# syncCompetitionFields -- relay forces distance and shows the legs field
# ---------------------------------------------------------------------------


def _run_sync_competition_fields_relay(competition_mode: str, race_type: str) -> dict:
    source = _stripped_script()
    is_relay_mode_fn = _extract_function(source, "isRelayCompetitionMode")
    completion_field_state_fn = _extract_function(source, "completionFieldState")
    race_rule_note_key_fn = _extract_function(source, "raceRuleNoteKey")
    sync_competition_fields_fn = _extract_function(source, "syncCompetitionFields")
    script = (
        "const mockElements = {};\n"
        "function makeEl() {\n"
        "  return {\n"
        "    textContent: '',\n"
        "    value: '',\n"
        "    dataset: {},\n"
        "    disabled: false,\n"
        "    classList: { toggled: {}, toggle: function (cls, force) { this.toggled[cls] = force; } },\n"
        "  };\n"
        "}\n"
        "function $(id) {\n"
        "  if (!mockElements[id]) mockElements[id] = makeEl();\n"
        "  return mockElements[id];\n"
        "}\n"
        "function t(key) { return `T[${key}]`; }\n"
        "function updateControlGuidance() {}\n"
        + is_relay_mode_fn
        + "\n"
        + completion_field_state_fn
        + "\n"
        + race_rule_note_key_fn
        + "\n"
        + sync_competition_fields_fn
        + "\n"
        f"mockElements['competition-mode'] = {{ value: {json.dumps(competition_mode)} }};\n"
        f"mockElements['race-type'] = {{ value: {json.dumps(race_type)}, dataset: {{}} }};\n"
        "syncCompetitionFields();\n"
        "console.log(JSON.stringify({\n"
        "  raceTypeValue: mockElements['race-type'].value,\n"
        "  raceTypeDisabled: mockElements['race-type'].disabled,\n"
        "  relayLegsFieldDisabled: mockElements['relay-legs-field'].classList.toggled['is-disabled'],\n"
        "  relayLegsInputDisabled: mockElements['relay-legs'].disabled,\n"
        "  teamScoringFieldDisabled: mockElements['team-scoring-field'].classList.toggled['is-disabled'],\n"
        "}));\n"
    )
    return json.loads(_run_node(script))


def test_relay_mode_forces_race_type_to_distance_and_disables_selector():
    result = _run_sync_competition_fields_relay("relay", "max_power")
    assert result["raceTypeValue"] == "distance"
    assert result["raceTypeDisabled"] is True


def test_relay_mode_enables_the_legs_field():
    result = _run_sync_competition_fields_relay("relay", "distance")
    assert result["relayLegsFieldDisabled"] is False
    assert result["relayLegsInputDisabled"] is False


def test_relay_mode_hides_team_scoring_field_like_individual():
    result = _run_sync_competition_fields_relay("relay", "distance")
    assert result["teamScoringFieldDisabled"] is True


def test_individual_mode_keeps_legs_field_hidden_and_race_type_enabled():
    result = _run_sync_competition_fields_relay("individual", "time")
    assert result["raceTypeValue"] == "time"
    assert result["raceTypeDisabled"] is False
    assert result["relayLegsFieldDisabled"] is True
    assert result["relayLegsInputDisabled"] is True


# ---------------------------------------------------------------------------
# Wiring: renderStations() and configureRace() reach the new relay pieces
# ---------------------------------------------------------------------------


def test_render_stations_calls_relay_form_builder_only_when_relay():
    source = _stripped_script()
    start = source.index("function renderStations(")
    end = source.index("async function registerRelayTeam", start)
    body = source[start:end]
    assert "isRelayCompetitionMode(state.race?.config?.competition_mode)" in body
    assert "buildRelayRegistrationFormHtml(key, station)" in body


def test_configure_race_sends_relay_legs_in_payload():
    source = _stripped_script()
    start = source.index("async function configureRace(")
    end = source.index("async function setLeaderboardDisplayMode", start)
    body = source[start:end]
    assert "relay_legs:" in body
    assert 'isRelayCompetitionMode($("competition-mode").value)' in body


def _run_configure_race(
    competition_mode: str, race_type: str, race_target: str, relay_legs_value: str
) -> dict:
    """Executes the REAL configureRace() under node with a light fake DOM
    and a fetchJson stub that records the exact JSON body posted -- proves
    relay_legs actually reaches the wire, not just that the source text
    mentions the key somewhere (test_configure_race_sends_relay_legs_in_
    payload above only pins the latter, and a regression that always sends
    relay_legs: null would still satisfy it)."""
    source = _stripped_script()
    is_relay_mode_fn = _extract_function(source, "isRelayCompetitionMode")
    configure_race_fn = _extract_function(source, "configureRace", async_fn=True)
    script = (
        "const mockElements = {\n"
        f"  'competition-mode': {{ value: {json.dumps(competition_mode)} }},\n"
        f"  'race-type': {{ value: {json.dumps(race_type)} }},\n"
        "  'team-scoring-policy': { value: 'average' },\n"
        "  'team-completion-policy': { value: 'aggregate' },\n"
        f"  'race-target': {{ value: {json.dumps(race_target)} }},\n"
        f"  'relay-legs': {{ value: {json.dumps(relay_legs_value)} }},\n"
        "};\n"
        "function $(id) { return mockElements[id]; }\n"
        "function t(key) { return key; }\n"
        "function setMessage() {}\n"
        "function adminHeaders(headers) { return headers; }\n"
        "async function refreshReadiness() {}\n"
        "function renderRace() {}\n"
        "const state = { raceConfigDirty: true, race: null };\n"
        "let fetchCalls = [];\n"
        "function fetchJson(url, options) {\n"
        "  fetchCalls.push({ url, body: JSON.parse(options.body) });\n"
        "  return Promise.resolve({});\n"
        "}\n"
        + is_relay_mode_fn
        + "\n"
        + configure_race_fn
        + "\n"
        + "configureRace().then(() => {\n"
        + "  console.log(JSON.stringify({ fetchCalls }));\n"
        + "});\n"
    )
    result = json.loads(_run_node(script))
    return result["fetchCalls"][0]["body"]


def test_configure_race_posts_relay_legs_for_a_relay_race():
    body = _run_configure_race(
        competition_mode="relay",
        race_type="time",
        race_target="1000",
        relay_legs_value="3",
    )
    assert body["relay_legs"] == 3
    # A relay race is always distance, regardless of whatever race-type the
    # selector was showing before the operator switched to relay.
    assert body["race_type"] == "distance"


def test_configure_race_posts_null_relay_legs_for_an_individual_race():
    body = _run_configure_race(
        competition_mode="individual",
        race_type="distance",
        race_target="500",
        relay_legs_value="4",
    )
    assert body["relay_legs"] is None
    assert body["race_type"] == "distance"


def test_register_relay_team_posts_to_register_endpoint():
    source = _stripped_script()
    start = source.index("async function registerRelayTeam(")
    end = source.index("\n    }\n", start) + len("\n    }\n")
    body = source[start:end]
    assert "/api/race/register" in body
    assert "relayMembersFromTextarea(" in body


# ---------------------------------------------------------------------------
# i18n: every new key exists in both dictionaries with real zh-TW copy
# ---------------------------------------------------------------------------


def test_relay_option_label_translated():
    source = _read()
    assert '"option.relay": "Relay Race (same machine)"' in source
    assert '"option.relay": "接力賽（同一台機器）"' in source


def test_relay_legs_label_translated():
    source = _read()
    assert '"label.relay_legs": "Legs"' in source
    assert '"label.relay_legs": "棒數"' in source


def test_relay_team_name_label_translated():
    source = _read()
    assert '"label.relay_team_name": "Team Name"' in source
    assert '"label.relay_team_name": "隊伍名稱"' in source


def test_relay_members_label_translated():
    source = _read()
    assert '"label.relay_members": "Members (one per line)"' in source
    assert '"label.relay_members": "隊員（每行一人）"' in source


def test_register_relay_team_button_translated():
    source = _read()
    assert '"button.register_relay_team": "Register Team"' in source
    assert '"button.register_relay_team": "登記隊伍"' in source
