"""Batch 1d: the mixed-race "race groups" editor UI in Game Admin
(hub_server/static/gameAdmin.html).

Covers:
  * "mixed" is a selectable #race-type option, with its own field note
    (text.race_note_mixed) reached through the existing raceRuleNoteKey()
    mapping.
  * syncMixedRaceFields(): selecting "mixed" hides #target-field, shows
    #race-groups-field, forces #competition-mode to "individual", and
    disables its team/relay <option>s -- and switching away from "mixed"
    reverses all of that. Kept as its own function (not folded into
    syncCompetitionFields()) so that function's existing regression tests
    keep passing unmodified; run here against a light fake-DOM harness
    mirroring test_game_admin_relay_config.py's
    _run_sync_competition_fields_relay.
  * The five group-editor mutators (addRaceGroup, removeRaceGroup,
    toggleRaceGroupEquipmentType, setRaceGroupRaceType, setRaceGroupTarget)
    each mutate state.raceGroups correctly and call markRaceConfigDirty(),
    executed for real under node against a stubbed renderRaceGroupsEditor.
  * renderRace() restores state.raceGroups from config.groups (via
    raceGroupsFromConfig) only when config.race_type is "mixed", checked on
    the real body of that function.
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


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
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
# "mixed" race-type option and field note
# ---------------------------------------------------------------------------


def test_mixed_race_type_option_exists_with_i18n_key():
    source = _read()
    assert '<option value="mixed" data-i18n="option.mixed">' in source


def test_race_rule_note_key_maps_mixed_to_its_own_note():
    source = _stripped_script()
    fn = _extract_function(source, "raceRuleNoteKey")
    script = fn + '\nconsole.log(raceRuleNoteKey("mixed"));'
    assert _run_node(script) == "text.race_note_mixed"


def test_mixed_option_and_note_translated_in_both_dictionaries():
    source = _read()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 20000]
    assert '"option.mixed": "Multi-equipment Groups"' in en_block
    assert '"option.mixed": "多器材分組"' in zh_block
    assert (
        '"text.race_note_mixed": "Each equipment group races its own target from the same start and ranks separately."'
        in en_block
    )
    assert (
        '"text.race_note_mixed": "各器材組同時出發、各自的目標、分開排名。"' in zh_block
    )


# ---------------------------------------------------------------------------
# syncCompetitionFields(): mixed show/hide + competition-mode lock
# ---------------------------------------------------------------------------


def _run_sync_mixed_race_fields(
    race_type: str, competition_mode: str, existing_groups=None
):
    """Drives the real syncMixedRaceFields() -- kept as its own function
    (not folded into syncCompetitionFields()) precisely so
    test_game_admin_relay_config.py's and test_game_admin_race_rule_
    guidance.py's existing syncCompetitionFields() harnesses (a smaller,
    pre-existing stub set that doesn't know about isMixedRaceType/state/
    renderRaceGroupsEditor) keep working unmodified. See syncRaceFields()
    for the call site: syncMixedRaceFields() runs before
    syncCompetitionFields() so the forced competition-mode value is what
    that function reasons about."""
    source = _stripped_script()
    is_mixed_type_fn = _extract_function(source, "isMixedRaceType")
    sync_mixed_race_fields_fn = _extract_function(source, "syncMixedRaceFields")
    groups_js = json.dumps(existing_groups if existing_groups is not None else [])
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
        "let renderRaceGroupsEditorCalls = 0;\n"
        "function renderRaceGroupsEditor() { renderRaceGroupsEditorCalls += 1; }\n"
        f"const state = {{ raceGroups: {groups_js} }};\n"
        + is_mixed_type_fn
        + "\n"
        + sync_mixed_race_fields_fn
        + "\n"
        f"mockElements['competition-mode'] = {{ value: {json.dumps(competition_mode)} }};\n"
        f"mockElements['race-type'] = {{ value: {json.dumps(race_type)}, dataset: {{}} }};\n"
        "syncMixedRaceFields();\n"
        "console.log(JSON.stringify({\n"
        "  competitionModeValue: mockElements['competition-mode'].value,\n"
        "  teamOptionDisabled: mockElements['competition-option-team'].disabled,\n"
        "  relayOptionDisabled: mockElements['competition-option-relay'].disabled,\n"
        "  targetFieldHidden: mockElements['target-field'].classList.toggled['field-collapsed'],\n"
        "  groupsFieldHidden: mockElements['race-groups-field'].classList.toggled['field-collapsed'],\n"
        "  raceGroupsLength: state.raceGroups.length,\n"
        "  rendererCalls: renderRaceGroupsEditorCalls,\n"
        "}));\n"
    )
    return json.loads(_run_node(script))


def test_mixed_hides_target_field_and_shows_group_editor():
    result = _run_sync_mixed_race_fields("mixed", "individual")
    assert result["targetFieldHidden"] is True
    assert result["groupsFieldHidden"] is False
    assert result["rendererCalls"] == 1


def test_mixed_forces_competition_mode_to_individual_and_locks_options():
    result = _run_sync_mixed_race_fields("mixed", "team")
    assert result["competitionModeValue"] == "individual"
    assert result["teamOptionDisabled"] is True
    assert result["relayOptionDisabled"] is True


def test_non_mixed_keeps_target_field_visible_and_group_editor_hidden():
    result = _run_sync_mixed_race_fields("distance", "individual")
    assert result["targetFieldHidden"] is False
    assert result["groupsFieldHidden"] is True
    assert result["rendererCalls"] == 0


def test_switching_away_from_mixed_reenables_team_and_relay_options():
    result = _run_sync_mixed_race_fields("distance", "individual")
    assert result["teamOptionDisabled"] is False
    assert result["relayOptionDisabled"] is False


def test_mixed_initializes_two_blank_groups_when_none_exist():
    result = _run_sync_mixed_race_fields("mixed", "individual", existing_groups=[])
    assert result["raceGroupsLength"] == 2


def test_mixed_does_not_reset_existing_groups():
    existing = [
        {"equipmentTypes": ["treadmill"], "raceType": "distance", "targetValue": 800},
        {"equipmentTypes": ["rowing_machine"], "raceType": "time", "targetValue": 300},
        {"equipmentTypes": ["spin_bike"], "raceType": "distance", "targetValue": 400},
    ]
    result = _run_sync_mixed_race_fields(
        "mixed", "individual", existing_groups=existing
    )
    assert result["raceGroupsLength"] == 3


# ---------------------------------------------------------------------------
# Group editor mutators
# ---------------------------------------------------------------------------


def _run_mutator(fn_name: str, initial_groups, call_expr: str):
    source = _stripped_script()
    fn = _extract_function(source, fn_name)
    script = (
        "let markDirtyCalls = 0;\n"
        "function markRaceConfigDirty() { markDirtyCalls += 1; }\n"
        "let renderCalls = 0;\n"
        "function renderRaceGroupsEditor() { renderCalls += 1; }\n"
        f"const state = {{ raceGroups: {json.dumps(initial_groups)} }};\n"
        + fn
        + "\n"
        + call_expr
        + "\n"
        + "console.log(JSON.stringify({ raceGroups: state.raceGroups, markDirtyCalls, renderCalls }));\n"
    )
    return json.loads(_run_node(script))


def test_add_race_group_appends_a_blank_group_and_marks_dirty():
    result = _run_mutator(
        "addRaceGroup",
        [
            {
                "equipmentTypes": ["treadmill"],
                "raceType": "distance",
                "targetValue": 800,
            },
            {
                "equipmentTypes": ["rowing_machine"],
                "raceType": "distance",
                "targetValue": 500,
            },
        ],
        "addRaceGroup();",
    )
    assert len(result["raceGroups"]) == 3
    assert result["raceGroups"][2] == {
        "equipmentTypes": [],
        "raceType": "distance",
        "targetValue": 0,
    }
    assert result["markDirtyCalls"] == 1


def test_add_race_group_refuses_beyond_eight_groups():
    eight_groups = [
        {"equipmentTypes": [f"type{i}"], "raceType": "distance", "targetValue": 1}
        for i in range(8)
    ]
    result = _run_mutator("addRaceGroup", eight_groups, "addRaceGroup();")
    assert len(result["raceGroups"]) == 8
    assert result["markDirtyCalls"] == 0


def test_remove_race_group_removes_the_group_at_index_and_marks_dirty():
    result = _run_mutator(
        "removeRaceGroup",
        [
            {
                "equipmentTypes": ["treadmill"],
                "raceType": "distance",
                "targetValue": 800,
            },
            {
                "equipmentTypes": ["rowing_machine"],
                "raceType": "distance",
                "targetValue": 500,
            },
            {
                "equipmentTypes": ["spin_bike"],
                "raceType": "distance",
                "targetValue": 300,
            },
        ],
        "removeRaceGroup(1);",
    )
    assert [g["equipmentTypes"] for g in result["raceGroups"]] == [
        ["treadmill"],
        ["spin_bike"],
    ]
    assert result["markDirtyCalls"] == 1


def test_remove_race_group_refuses_below_two_groups():
    result = _run_mutator(
        "removeRaceGroup",
        [
            {
                "equipmentTypes": ["treadmill"],
                "raceType": "distance",
                "targetValue": 800,
            },
            {
                "equipmentTypes": ["rowing_machine"],
                "raceType": "distance",
                "targetValue": 500,
            },
        ],
        "removeRaceGroup(0);",
    )
    assert len(result["raceGroups"]) == 2
    assert result["markDirtyCalls"] == 0


def test_toggle_race_group_equipment_type_adds_then_removes():
    added = _run_mutator(
        "toggleRaceGroupEquipmentType",
        [{"equipmentTypes": [], "raceType": "distance", "targetValue": 0}],
        "toggleRaceGroupEquipmentType(0, 'treadmill', true);",
    )
    assert added["raceGroups"][0]["equipmentTypes"] == ["treadmill"]
    assert added["markDirtyCalls"] == 1

    removed = _run_mutator(
        "toggleRaceGroupEquipmentType",
        [{"equipmentTypes": ["treadmill"], "raceType": "distance", "targetValue": 0}],
        "toggleRaceGroupEquipmentType(0, 'treadmill', false);",
    )
    assert removed["raceGroups"][0]["equipmentTypes"] == []


def test_set_race_group_race_type_updates_and_marks_dirty():
    result = _run_mutator(
        "setRaceGroupRaceType",
        [{"equipmentTypes": ["treadmill"], "raceType": "distance", "targetValue": 800}],
        "setRaceGroupRaceType(0, 'time');",
    )
    assert result["raceGroups"][0]["raceType"] == "time"
    assert result["markDirtyCalls"] == 1
    assert result["renderCalls"] == 1


def test_set_race_group_target_coerces_to_number_and_marks_dirty():
    result = _run_mutator(
        "setRaceGroupTarget",
        [{"equipmentTypes": ["treadmill"], "raceType": "distance", "targetValue": 0}],
        "setRaceGroupTarget(0, '650');",
    )
    assert result["raceGroups"][0]["targetValue"] == 650
    assert result["markDirtyCalls"] == 1


# ---------------------------------------------------------------------------
# renderRace() wiring: restore state.raceGroups from a saved mixed config
# ---------------------------------------------------------------------------
#
# The source-text check below is a cheap smoke test only -- it is NOT proof
# the wiring actually runs. A `if (false && config.race_type === "mixed")`
# mutation still contains both substrings it checks for, so that mutation
# survives it undetected. The real proof is
# _run_render_race_with_config()/the tests after it: they extract and
# EXECUTE the real renderRace() under node against a stubbed DOM/
# collaborator set and assert on the resulting state.raceGroups value, so a
# `false &&`-style mutation (or any other way of skipping the assignment)
# is caught.


def test_render_race_restores_groups_from_config_only_for_mixed():
    source = _stripped_script()
    start = source.index("function renderRace(")
    end = source.index("function sessionModeSwitchState", start)
    body = source[start:end]
    assert 'config.race_type === "mixed"' in body
    assert "raceGroupsFromConfig(config.groups)" in body


def _run_render_race_with_config(configs: list, initial_race_groups=None):
    """Executes the REAL renderRace() under node, once per entry in
    `configs`, threading state.raceGroups through successive calls the way
    a real page does across repeated refreshState()/renderRace() calls.
    Every renderRace() collaborator other than the DOM ($) and
    raceGroupsFromConfig (kept real -- see below) is stubbed, so only the
    group-restoration branch under test can affect the result. Returns
    {"snapshots": [...], "syncMixedRaceFieldsCalls": [...]}.

    raceGroupsFromConfig is replaced with a marker-tagging stub rather than
    the real pure function: this isolates "does renderRace() call
    raceGroupsFromConfig(config.groups) and assign the result to
    state.raceGroups" from "is raceGroupsFromConfig itself correct" (the
    latter is already covered for real in
    test_game_admin_race_groups_payload.py's round-trip test).

    The syncMixedRaceFields stub RECORDS every call, capturing both
    $("race-type").value and a snapshot of state.raceGroups at call time --
    not just whether it was called -- so a test can pin that it runs AFTER
    both are set to their new (mixed) values, not before (a "moved earlier
    in renderRace()" mutation would otherwise pass a bare
    call-count/call-happened check while still handing
    syncMixedRaceFields() the stale pre-restoration state)."""
    source = _stripped_script()
    fn = _extract_function(source, "renderRace")
    configs_js = json.dumps(configs)
    initial_groups_js = json.dumps(
        initial_race_groups if initial_race_groups is not None else []
    )
    script = f"""
const mockElements = {{}};
function makeEl() {{
  return {{
    textContent: '',
    value: '',
    className: '',
    disabled: false,
    dataset: {{}},
    classList: {{ toggle: function () {{}} }},
  }};
}}
function $(id) {{
  if (!mockElements[id]) mockElements[id] = makeEl();
  return mockElements[id];
}}
function t(key) {{ return key; }}
function normalizeLeaderboardDisplayMode(mode) {{ return mode || "classic"; }}
function metricNumber(value, fallback) {{ return Number(value) || fallback || 0; }}
function raceGroupsFromConfig(groups) {{
  return (groups || []).map((g) => ({{ __restoredFrom: g }}));
}}
const syncMixedRaceFieldsCalls = [];
function syncMixedRaceFields() {{
  syncMixedRaceFieldsCalls.push({{
    raceType: $("race-type").value,
    raceGroups: JSON.parse(JSON.stringify(state.raceGroups)),
  }});
}}
function syncRaceFields() {{}}
function syncCompetitionFields() {{}}
function renderRaceActionButtons() {{}}
function updateControlGuidance() {{}}
function renderReadinessPanel() {{}}
function syncSessionModeControl() {{}}
function syncVisibility() {{}}
function renderRoster() {{}}
function renderChallengeMode() {{}}

const state = {{
  race: null,
  raceConfigDirty: false,
  raceGroups: {initial_groups_js},
  readiness: null,
  countdownActive: false,
}};

{fn}

const configs = {configs_js};
const snapshots = [];
for (const config of configs) {{
  state.race = {{ state: "READY", config }};
  renderRace();
  snapshots.push(JSON.parse(JSON.stringify(state.raceGroups)));
}}
console.log(JSON.stringify({{ snapshots, syncMixedRaceFieldsCalls }}));
"""
    return json.loads(_run_node(script))


def test_render_race_actually_restores_groups_from_a_saved_mixed_config():
    saved_groups = [
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
    result = _run_render_race_with_config(
        [
            {
                "race_type": "mixed",
                "competition_mode": "individual",
                "groups": saved_groups,
            }
        ]
    )
    restored = result["snapshots"][0]
    assert restored == [{"__restoredFrom": g} for g in saved_groups]


def test_render_race_does_not_resurrect_stale_groups_after_a_non_mixed_config_loads():
    """After a mixed config's groups are loaded, loading a NON-mixed config
    (the operator switched race types and saved) must not leave the old
    group editor state sitting in state.raceGroups as if it were still the
    saved setup -- otherwise switching race-type back to "mixed" later would
    silently resurrect stale, no-longer-saved groups (see
    syncMixedRaceFields(), which only seeds two blank groups when
    state.raceGroups is empty)."""
    saved_groups = [
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
    result = _run_render_race_with_config(
        [
            {
                "race_type": "mixed",
                "competition_mode": "individual",
                "groups": saved_groups,
            },
            {"race_type": "distance", "competition_mode": "individual", "groups": []},
        ]
    )
    snapshots = result["snapshots"]
    assert snapshots[0] != []
    assert snapshots[1] == []


def test_render_race_calls_sync_mixed_race_fields_after_race_type_and_groups_are_restored():
    """Pins the ORDER inside renderRace(): syncMixedRaceFields() must run
    AFTER both #race-type is set to "mixed" and state.raceGroups holds the
    freshly restored groups -- not before. If it ran first (or not at all),
    syncMixedRaceFields() would see a stale/empty state.raceGroups and (per
    its own logic) seed two blank groups instead of showing the operator
    their actual saved setup."""
    saved_groups = [
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
    stale_sentinel_groups = [
        {"equipmentTypes": ["OLD_SENTINEL"], "raceType": "distance", "targetValue": 1}
    ]
    result = _run_render_race_with_config(
        [
            {
                "race_type": "mixed",
                "competition_mode": "individual",
                "groups": saved_groups,
            }
        ],
        initial_race_groups=stale_sentinel_groups,
    )
    calls = result["syncMixedRaceFieldsCalls"]
    assert len(calls) == 1
    assert calls[0]["raceType"] == "mixed"
    assert calls[0]["raceGroups"] == result["snapshots"][0]
    assert calls[0]["raceGroups"] != stale_sentinel_groups
