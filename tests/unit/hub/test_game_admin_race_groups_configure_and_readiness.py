"""Batch 1d: saving a mixed race from Game Admin and showing its readiness
check (hub_server/static/gameAdmin.html).

Covers:
  * configureRace(), for a mixed race: validates state.raceGroups first
    (via validateRaceGroups) and aborts with an error message -- no HTTP
    call -- on failure; on success posts {race_type: "mixed",
    competition_mode: "individual", groups: buildRaceGroupsPayload(...),
    target_value: 0, duration_sec: 0, relay_legs: null, ...team policy
    fields unchanged}. Run for real under node with a stubbed fetchJson
    that records the exact JSON body posted, mirroring
    test_game_admin_relay_config.py's _run_configure_race.
  * renderReadinessPanel(): the backend's own checks.groups (see
    get_race_readiness_status() in hub_server/infrastructure/fastapi/
    app.py) is shown, with its label routed through t("check.groups"),
    only when present -- absent for a non-mixed race's readiness payload.
    Its message is displayed verbatim (the existing convention for every
    other check; see stations/state/target/etc. in renderReadinessPanel),
    so no additional message-text i18n mapping is added here.
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
# configureRace() -- mixed payload
# ---------------------------------------------------------------------------


def _run_configure_race_mixed(race_groups: list, competition_mode: str = "individual"):
    """Executes the REAL configureRace() under node with a light fake DOM
    and a fetchJson stub that records the exact JSON body posted, plus the
    real buildRaceGroupsPayload/validateRaceGroups it calls -- proves the
    mixed payload actually reaches the wire, not just that the source text
    mentions "groups" somewhere."""
    source = _stripped_script()
    is_relay_mode_fn = _extract_function(source, "isRelayCompetitionMode")
    build_payload_fn = _extract_function(source, "buildRaceGroupsPayload")
    validate_fn = _extract_function(source, "validateRaceGroups")
    configure_race_fn = _extract_function(source, "configureRace", async_fn=True)
    script = (
        "const mockElements = {\n"
        f"  'competition-mode': {{ value: {json.dumps(competition_mode)} }},\n"
        "  'race-type': { value: 'mixed' },\n"
        "  'team-scoring-policy': { value: 'average' },\n"
        "  'team-completion-policy': { value: 'aggregate' },\n"
        "  'race-target': { value: '0' },\n"
        "  'relay-legs': { value: '4' },\n"
        "};\n"
        "function $(id) { return mockElements[id]; }\n"
        "function t(key) { return key; }\n"
        "let messages = [];\n"
        "function setMessage(id, text, kind) { messages.push({ id, text, kind }); }\n"
        "function adminHeaders(headers) { return headers; }\n"
        "async function refreshReadiness() {}\n"
        "function renderRace() {}\n"
        f"const state = {{ raceConfigDirty: true, race: null, raceGroups: {json.dumps(race_groups)} }};\n"
        "let fetchCalls = [];\n"
        "function fetchJson(url, options) {\n"
        "  fetchCalls.push({ url, body: JSON.parse(options.body) });\n"
        "  return Promise.resolve({});\n"
        "}\n"
        + is_relay_mode_fn
        + "\n"
        + build_payload_fn
        + "\n"
        + validate_fn
        + "\n"
        + configure_race_fn
        + "\n"
        + "configureRace().then((result) => {\n"
        + "  console.log(JSON.stringify({ fetchCalls, messages, result }));\n"
        + "});\n"
    )
    return json.loads(_run_node(script))


VALID_GROUPS = [
    {"equipmentTypes": ["treadmill"], "raceType": "distance", "targetValue": 800},
    {"equipmentTypes": ["rowing_machine"], "raceType": "time", "targetValue": 300},
]


def test_configure_race_posts_mixed_payload_with_groups():
    result = _run_configure_race_mixed(VALID_GROUPS)
    assert len(result["fetchCalls"]) == 1
    body = result["fetchCalls"][0]["body"]
    assert body["race_type"] == "mixed"
    assert body["competition_mode"] == "individual"
    assert body["target_value"] == 0
    assert body["duration_sec"] == 0
    assert body["relay_legs"] is None
    assert body["team_scoring_policy"] == "average"
    assert body["team_completion_policy"] == "aggregate"
    assert body["groups"] == [
        {
            "equipment_types": ["treadmill"],
            "race_type": "distance",
            "target_value": 800,
            "duration_sec": 0,
        },
        {
            "equipment_types": ["rowing_machine"],
            "race_type": "time",
            "target_value": 0,
            "duration_sec": 300,
        },
    ]
    assert result["result"] is True


def test_configure_race_mixed_payload_forces_individual_competition_mode():
    """Even if #competition-mode's DOM value is somehow stale (e.g. "team"
    from before syncMixedRaceFields() last ran), the posted payload must
    still be individual -- the backend rejects mixed + non-individual."""
    result = _run_configure_race_mixed(VALID_GROUPS, competition_mode="team")
    body = result["fetchCalls"][0]["body"]
    assert body["competition_mode"] == "individual"


def test_configure_race_blocks_invalid_groups_without_posting():
    too_few_groups = [VALID_GROUPS[0]]
    result = _run_configure_race_mixed(too_few_groups)
    assert result["fetchCalls"] == []
    assert result["result"] is False
    assert result["messages"][-1]["text"] == "message.groups_min"
    assert result["messages"][-1]["kind"] == "error"


def test_configure_race_blocks_duplicate_equipment_without_posting():
    duplicate_groups = [
        {"equipmentTypes": ["treadmill"], "raceType": "distance", "targetValue": 800},
        {"equipmentTypes": ["treadmill"], "raceType": "distance", "targetValue": 500},
    ]
    result = _run_configure_race_mixed(duplicate_groups)
    assert result["fetchCalls"] == []
    assert result["result"] is False
    assert result["messages"][-1]["text"] == "message.group_equipment_duplicate"


# ---------------------------------------------------------------------------
# renderReadinessPanel() -- checks.groups, present only for mixed races
# ---------------------------------------------------------------------------


def _extract_readiness_functions() -> str:
    script = _stripped_script()
    start = script.index("function readinessStatusClass")
    end = script.index("function stationReasonText", start)
    return script[start:end]


_DOM_HARNESS_PREFIX = """
const mockElements = {};
function $(id) {
  if (!mockElements[id]) mockElements[id] = {};
  return mockElements[id];
}
function t(key) { return key; }
function escapeHtml(value) { return String(value ?? ""); }
const state = {};
"""


def _render_with_checks(checks: dict) -> str:
    body = _extract_readiness_functions()
    harness = f"""
{_DOM_HARNESS_PREFIX}
{body}

state.readiness = {{
  ready: true,
  checks: {json.dumps(checks)},
  blocking_issues: [],
  warnings: [],
  station_health: [],
}};
renderReadinessPanel();
console.log(mockElements["race-readiness-panel"].innerHTML);
"""
    return _run_node(harness)


def test_groups_check_rendered_when_present():
    html = _render_with_checks(
        {
            "groups": {
                "status": "warn",
                "message": "1 group(s) have no assigned station.",
            }
        }
    )
    assert "check.groups" in html
    assert "1 group(s) have no assigned station." in html
    assert "readiness-check-dot warn" in html


def test_groups_check_absent_for_a_non_mixed_readiness_payload():
    html = _render_with_checks({})
    assert "check.groups" not in html


def test_groups_check_message_shown_verbatim_not_remapped():
    """Confirms the backend's exact block-status message string (from
    get_race_readiness_status() in hub_server/infrastructure/fastapi/
    app.py) is shown as-is -- the existing convention for every other
    check -- rather than being looked up as its own i18n key."""
    message = "2 station(s) are not in any race group."
    html = _render_with_checks({"groups": {"status": "block", "message": message}})
    assert message in html


# ---------------------------------------------------------------------------
# i18n: check.groups exists in both dictionaries with the spec's exact copy
# ---------------------------------------------------------------------------


def test_check_groups_label_translated_in_both_dictionaries():
    source = _read()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 20000]
    assert '"check.groups": "Equipment Groups"' in en_block
    assert '"check.groups": "器材分組"' in zh_block


def test_check_order_routes_groups_label_through_t():
    script = _stripped_script()
    start = script.index("function renderReadinessPanel")
    end = script.index("function stationReasonText", start)
    body = script[start:end]
    assert 't("check.groups")' in body
