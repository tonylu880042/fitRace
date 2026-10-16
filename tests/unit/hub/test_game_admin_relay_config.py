"""Game Admin (hub_server/static/gameAdmin.html) gains a "relay" Competition
option: a relay race splits a distance target equally across a team-size
number of legs (relay_legs, 2..10), each leg run by a different roster
member on the SAME machine. This covers:

  * isRelayCompetitionMode(mode) -- trivial predicate.
  * buildRelayTeamReadOnlyHtml(station) -- the pure markup builder for a
    station's read-only team block (team, members, link to /teams), executed
    for real under node (not a source-text grep). The per-station
    registration form was removed: teams are assigned on /teams.
  * syncCompetitionFields() -- when "relay" is selected, forces race type to
    distance (and disables the selector), shows the legs field, and hides
    the team-scoring/completion fields exactly as for "individual" -- run
    against a light fake-DOM harness mirroring
    test_game_admin_race_rule_guidance.py's _run_sync_competition_fields.
  * teamRuleSummaryText -- returns the relay-specific guidance sentence.
  * Wiring: renderStations() shows the read-only block only for a relay
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
# buildRelayTeamReadOnlyHtml -- the station card's read-only team block.
# Teams are now assigned on /teams; Game Admin only shows the result.
# ---------------------------------------------------------------------------


def _t_stub() -> str:
    return (
        "const t = (key) => ({"
        '"text.relay_no_team": "No team assigned",'
        '"link.relay_manage_teams": "Manage teams"'
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


def _run_build_relay_readonly(station_js: str) -> str:
    source = _stripped_script()
    fn = _extract_function(source, "buildRelayTeamReadOnlyHtml")
    script = (
        _t_stub()
        + _escape_html_stub()
        + fn
        + "\n"
        + f"console.log(buildRelayTeamReadOnlyHtml({station_js}));"
    )
    return _run_node(script)


def test_relay_readonly_block_shows_team_name_members_and_teams_link():
    html = _run_build_relay_readonly(
        '{registered: true, team_name: "Volt", relay_members: ["Alice", "Bob"]}'
    )
    assert "Volt" in html
    assert "Alice, Bob" in html
    assert 'href="/teams"' in html
    assert "Manage teams" in html


def test_relay_readonly_block_has_no_form_controls():
    html = _run_build_relay_readonly(
        '{registered: true, team_name: "Volt", relay_members: ["Alice", "Bob"]}'
    )
    for tag in ("<input", "<textarea", "<button", "<select"):
        assert tag not in html


def test_relay_readonly_block_for_unassigned_station_says_so_and_still_links():
    html = _run_build_relay_readonly("{registered: false, team_name: null}")
    assert "No team assigned" in html
    assert 'href="/teams"' in html


def test_relay_readonly_block_escapes_team_and_member_names():
    html = _run_build_relay_readonly(
        '{registered: true, team_name: "<b>x</b>", relay_members: ["<i>y</i>"]}'
    )
    assert "<b>x</b>" not in html
    assert "<i>y</i>" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


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
    disclosure_state_fn = _extract_function(
        source, "rulesOpenAfterTransition"
    ) + _extract_function(source, "disclosureState")
    sync_visibility_fn = _extract_function(source, "syncVisibility")
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
        "const state = { race: { session_mode: 'race' }, roster: { counts: {} } };\n"
        + disclosure_state_fn
        + "\n"
        + sync_visibility_fn
        + "\n"
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
        "syncVisibility();\n"
        "console.log(JSON.stringify({\n"
        "  raceTypeValue: mockElements['race-type'].value,\n"
        "  raceTypeDisabled: mockElements['race-type'].disabled,\n"
        "  relayLegsFieldCollapsed: mockElements['relay-legs-field'].classList.toggled['field-collapsed'],\n"
        "  teamScoringFieldCollapsed: mockElements['team-scoring-field'].classList.toggled['field-collapsed'],\n"
        "}));\n"
    )
    return json.loads(_run_node(script))


def test_relay_mode_forces_race_type_to_distance_and_disables_selector():
    result = _run_sync_competition_fields_relay("relay", "max_power")
    assert result["raceTypeValue"] == "distance"
    assert result["raceTypeDisabled"] is True


def test_relay_mode_enables_the_legs_field():
    result = _run_sync_competition_fields_relay("relay", "distance")
    assert result["relayLegsFieldCollapsed"] is False


def test_relay_mode_hides_team_scoring_field_like_individual():
    result = _run_sync_competition_fields_relay("relay", "distance")
    assert result["teamScoringFieldCollapsed"] is True


def test_individual_mode_keeps_legs_field_hidden_and_race_type_enabled():
    result = _run_sync_competition_fields_relay("individual", "time")
    assert result["raceTypeValue"] == "time"
    assert result["raceTypeDisabled"] is False
    assert result["relayLegsFieldCollapsed"] is True


# ---------------------------------------------------------------------------
# Wiring: renderStations() and configureRace() reach the new relay pieces
# ---------------------------------------------------------------------------


def _render_stations_html(competition_mode: str) -> str:
    """Run the REAL renderStations() against a stub DOM and return the
    station list markup it produced."""
    source = _stripped_script()
    fns = "\n".join(
        _extract_function(source, name)
        for name in (
            "isRelayCompetitionMode",
            "buildRelayTeamReadOnlyHtml",
            "getStationHealth",
            "renderStations",
        )
    )
    script = (
        _t_stub()
        + _escape_html_stub()
        + "const out = {};\n"
        + "function $(id) {\n"
        + "  if (!out[id]) out[id] = { textContent: '', innerHTML: '' };\n"
        + "  return out[id];\n"
        + "}\n"
        + "const window = { location: { origin: 'http://test.local' } };\n"
        + "const state = {\n"
        + f"  race: {{ config: {{ competition_mode: {json.dumps(competition_mode)} }} }},\n"
        + "  readiness: { station_health: [] },\n"
        + "  stations: { stations: { '1': { node_id: 'n1', equipment_type: 'treadmill',"
        + " registered: true, team_name: 'Volt', relay_members: ['Alice', 'Bob'] } },"
        + " unassigned_nodes: [] },\n"
        + "};\n"
        + fns
        + "\nrenderStations();\n"
        + "console.log(out['station-list'].innerHTML);\n"
    )
    return _run_node(script)


def test_render_stations_shows_readonly_team_block_only_for_relay():
    relay_html = _render_stations_html("relay")
    assert 'href="/teams"' in relay_html
    assert "Alice, Bob" in relay_html

    individual_html = _render_stations_html("individual")
    assert 'href="/teams"' not in individual_html
    assert "Alice, Bob" not in individual_html


def test_game_admin_no_longer_has_a_relay_registration_form():
    source = _stripped_script()
    assert "registerRelayTeam" not in source
    assert "buildRelayRegistrationFormHtml" not in source
    assert "relayMembersFromTextarea" not in source
    page = _read()
    assert "relay-team-name-" not in page
    assert "relay-members-" not in page


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


def test_relay_manage_teams_link_translated():
    source = _read()
    assert '"link.relay_manage_teams": "Manage teams"' in source
    assert '"link.relay_manage_teams": "管理隊伍"' in source


def test_relay_no_team_text_translated():
    source = _read()
    assert '"text.relay_no_team": "No team assigned"' in source
    assert '"text.relay_no_team": "尚未分配隊伍"' in source


def test_removed_relay_form_keys_are_gone_from_both_dictionaries():
    source = _read()
    for key in (
        "label.relay_team_name",
        "label.relay_members",
        "button.register_relay_team",
        "message.relay_team_registered",
    ):
        assert f'"{key}"' not in source
