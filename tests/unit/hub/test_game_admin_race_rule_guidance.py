"""Game Admin's Race Rules block let an operator pick a Race Type (Distance /
Calories / Time / Max Power Challenge) with no explanation of what each one
means, and let them pair a Completion Rule / Team Scoring / Leaderboard View
choice with a race type that silently ignores it:

  * `hub_server/usecases/race_manager.py::_team_finished` (~line 388) only
    honours `team_completion_policy == "all_members"` for race_type in
    ("distance", "calories") -- for time / max_power / watts the completion
    rule the operator picked does nothing.
  * `hub_server/usecases/race_manager.py::_team_score` (~lines 341, 353)
    returns early on `all_members` for distance/calories, before the
    `policy == "total"` branch -- so "Team Total" scoring paired with
    "Everyone Finishes" silently loses the Total setting.
  * `hub_server/static/index.html::renderRaceTrackLeaderboard` fills every
    lane from `progress_percent`, which for time/max_power races is
    elapsed-time percent, not performance -- so Race Track shows nothing
    about who is winning in those race types.

`hub_server/static/gameAdmin.html` now surfaces all of this: a race-type
explanation note, a disabled (with explanation) Completion Rule field for
time-based race types, an extra sentence in the Team Rule card when Team
Total is silently overridden by Everyone Finishes, and a distinct warning
when Race Track is paired with a time-based race type.

Per CLAUDE.md's "prefer one partition pass" guidance, the Completion Rule
field's disabled-state and its explanatory note are computed together, in
one pass, by a single pure function (`completionFieldState`) -- not by two
independent predicates that could drift apart.

This module extracts the three pure decision helpers
(`raceRuleNoteKey`, `completionFieldState`, `leaderboardModeNoteKey`) out of
gameAdmin.html's inline `<script>` by brace-depth matching (the same
technique as `_matching_brace_end` in tests/unit/hub/test_static_page_i18n.py
and tests/unit/hub/test_dashboard_station_machine_name.py) and runs them
under `node -e`, asserting real return values -- not a source-text grep.
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
    """Mirrors tests/unit/hub/test_dashboard_station_machine_name.py."""
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
    """Same idea as _matching_brace_end but for the parameter-list parens,
    string-aware. Needed because a destructured-object parameter (e.g.
    `function f({ a, b }) {`) has a `{` inside the parameter list, before
    the function body's own `{` -- naively taking the first `{` after the
    function name grabs the destructuring literal's closing `}` instead of
    the body's, silently truncating the extracted source."""
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
    paren_open = start + len(marker) - 1
    paren_close = _matching_paren_end(source, paren_open)
    brace_open = source.index("{", paren_close)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# raceRuleNoteKey
# ---------------------------------------------------------------------------


def _run_race_rule_note_key(race_type: str) -> str:
    source = _stripped_script()
    fn = _extract_function(source, "raceRuleNoteKey")
    script = f"{fn}\nconsole.log(raceRuleNoteKey({json.dumps(race_type)}));"
    return _run_node(script)


def test_race_rule_note_key_distinct_for_all_four_race_types():
    keys = {
        race_type: _run_race_rule_note_key(race_type)
        for race_type in ("distance", "calories", "time", "max_power")
    }
    assert len(set(keys.values())) == 4, (
        "each race type must get a distinct explanation key, got " f"{keys}"
    )


def test_race_rule_note_key_values():
    assert _run_race_rule_note_key("distance") == "text.race_note_distance"
    assert _run_race_rule_note_key("calories") == "text.race_note_calories"
    assert _run_race_rule_note_key("time") == "text.race_note_time"
    assert _run_race_rule_note_key("max_power") == "text.race_note_max_power"


# ---------------------------------------------------------------------------
# completionFieldState
# ---------------------------------------------------------------------------


def _run_completion_field_state(race_type: str, is_team_race: bool) -> dict:
    source = _stripped_script()
    fn = _extract_function(source, "completionFieldState")
    script = (
        f"{fn}\n"
        "console.log(JSON.stringify(completionFieldState("
        f"{json.dumps(race_type)}, {json.dumps(is_team_race)})));"
    )
    return json.loads(_run_node(script))


def test_completion_field_disabled_for_time_race_type_even_when_team_race():
    result = _run_completion_field_state("time", True)
    assert result["disabled"] is True
    assert result["noteKey"] == "text.completion_rule_time_based_note"


def test_completion_field_disabled_for_max_power_race_type_even_when_team_race():
    result = _run_completion_field_state("max_power", True)
    assert result["disabled"] is True
    assert result["noteKey"] == "text.completion_rule_time_based_note"


def test_completion_field_disabled_for_watts_race_type_even_when_team_race():
    result = _run_completion_field_state("watts", True)
    assert result["disabled"] is True
    assert result["noteKey"] == "text.completion_rule_time_based_note"


def test_completion_field_disabled_for_individual_race():
    result = _run_completion_field_state("distance", False)
    assert result["disabled"] is True
    assert result["noteKey"] == "text.team_field_note"


def test_completion_field_enabled_for_distance_team_race():
    result = _run_completion_field_state("distance", True)
    assert result["disabled"] is False
    assert result["noteKey"] is None


def test_completion_field_enabled_for_calories_team_race():
    result = _run_completion_field_state("calories", True)
    assert result["disabled"] is False
    assert result["noteKey"] is None


def test_completion_field_note_key_differs_between_individual_and_time_based_reasons():
    individual_reason = _run_completion_field_state("distance", False)
    time_based_reason = _run_completion_field_state("time", True)
    assert individual_reason["disabled"] is True
    assert time_based_reason["disabled"] is True
    assert individual_reason["noteKey"] != time_based_reason["noteKey"], (
        "an individual race and a time-based team race are disabled for "
        "different reasons and must show different explanations, got "
        f"{individual_reason['noteKey']!r} for both"
    )


# ---------------------------------------------------------------------------
# leaderboardModeNoteKey
# ---------------------------------------------------------------------------


def _run_leaderboard_mode_note_key(mode: str, race_type: str) -> str:
    source = _stripped_script()
    fn = _extract_function(source, "leaderboardModeNoteKey")
    script = (
        f"{fn}\nconsole.log(leaderboardModeNoteKey("
        f"{json.dumps(mode)}, {json.dumps(race_type)}));"
    )
    return _run_node(script)


def test_leaderboard_mode_note_key_warns_only_for_race_track_and_time_like_types():
    for race_type in ("time", "max_power", "watts"):
        assert (
            _run_leaderboard_mode_note_key("race_track", race_type)
            == "leaderboard.mode_race_track_time_based_detail"
        )


def test_leaderboard_mode_note_key_normal_for_race_track_with_distance_or_calories():
    assert (
        _run_leaderboard_mode_note_key("race_track", "distance")
        == "leaderboard.mode_race_track_detail"
    )
    assert (
        _run_leaderboard_mode_note_key("race_track", "calories")
        == "leaderboard.mode_race_track_detail"
    )


def test_leaderboard_mode_note_key_other_modes_unaffected_by_race_type():
    for mode, expected in (
        ("classic", "leaderboard.mode_classic_detail"),
        ("team_battle", "leaderboard.mode_team_battle_detail"),
        ("sprint_board", "leaderboard.mode_sprint_board_detail"),
    ):
        for race_type in ("distance", "calories", "time", "max_power"):
            assert _run_leaderboard_mode_note_key(mode, race_type) == expected


# ---------------------------------------------------------------------------
# teamRuleSummaryText -- the Team Rule card composition, extracted out of
# updateControlGuidance so the finished string (including the "Team Total is
# silently overridden by Everyone Finishes" sentence) can be executed and
# asserted directly, rather than only checked by a source-text grep on
# updateControlGuidance's body.
# ---------------------------------------------------------------------------


def _team_rule_t_stub() -> str:
    return (
        "const t = (key, params) => {\n"
        "  if (params && Object.keys(params).length) {\n"
        "    return `T[${key}|${JSON.stringify(params)}]`;\n"
        "  }\n"
        "  return `T[${key}]`;\n"
        "};\n"
    )


def _run_team_rule_summary_text(params: dict) -> str:
    source = _stripped_script()
    fn = _extract_function(source, "teamRuleSummaryText")
    script = (
        _team_rule_t_stub() + fn + "\n"
        f"console.log(teamRuleSummaryText({json.dumps(params)}));"
    )
    return _run_node(script)


def test_team_rule_summary_text_individual_race_ignores_scoring_and_completion():
    result = _run_team_rule_summary_text(
        {
            "isTeamRace": False,
            "scoring": "average",
            "completion": "aggregate",
            "raceType": "distance",
            "targetLabel": "distance target",
        }
    )
    assert result == "T[text.individual_race]"


def test_team_rule_summary_text_override_sentence_present_for_all_members_distance_total():
    result = _run_team_rule_summary_text(
        {
            "isTeamRace": True,
            "scoring": "total",
            "completion": "all_members",
            "raceType": "distance",
            "targetLabel": "distance target",
        }
    )
    assert "T[text.team_total_overridden_by_everyone_finishes]" in result


def test_team_rule_summary_text_override_sentence_absent_for_all_members_distance_average():
    """Everyone Finishes + Team Average is not an override -- Average is
    what all_members forces anyway, so no contradiction to call out."""
    result = _run_team_rule_summary_text(
        {
            "isTeamRace": True,
            "scoring": "average",
            "completion": "all_members",
            "raceType": "distance",
            "targetLabel": "distance target",
        }
    )
    assert "team_total_overridden_by_everyone_finishes" not in result


def test_team_rule_summary_text_override_sentence_absent_for_aggregate_distance_total():
    """Team Target (aggregate) + Team Total is a real, honoured combination
    -- no override to warn about."""
    result = _run_team_rule_summary_text(
        {
            "isTeamRace": True,
            "scoring": "total",
            "completion": "aggregate",
            "raceType": "distance",
            "targetLabel": "distance target",
        }
    )
    assert "team_total_overridden_by_everyone_finishes" not in result


def test_team_rule_summary_text_override_sentence_absent_for_time_based_race_types():
    """The completion policy is entirely ignored for time/max_power/watts
    (race_manager._team_finished), so the distance/calories-only override
    warning must not appear there either."""
    for race_type in ("time", "max_power", "watts"):
        result = _run_team_rule_summary_text(
            {
                "isTeamRace": True,
                "scoring": "total",
                "completion": "all_members",
                "raceType": race_type,
                "targetLabel": "challenge window",
            }
        )
        assert "team_total_overridden_by_everyone_finishes" not in result


# ---------------------------------------------------------------------------
# syncRaceFields DOM wiring -- proves the race-type note's rendered
# textContent actually changes with the selected race type, not merely that
# raceRuleNoteKey() is called or that dataset.i18n was written (a
# raceNote.dataset.i18n = raceNoteKey; assignment with the
# raceNote.textContent = t(raceNoteKey); line deleted would still call
# raceRuleNoteKey() and satisfy every prior source-text assertion in this
# module, while leaving stale rules copy on screen after every race-type
# change -- exactly the bug this test exists to catch).
# ---------------------------------------------------------------------------


def _run_sync_race_fields(race_type: str) -> dict:
    source = _stripped_script()
    race_rule_note_key_fn = _extract_function(source, "raceRuleNoteKey")
    sync_race_fields_fn = _extract_function(source, "syncRaceFields")
    script = (
        "const mockElements = {};\n"
        "function $(id) {\n"
        "  if (!mockElements[id]) mockElements[id] = { textContent: '', dataset: {} };\n"
        "  return mockElements[id];\n"
        "}\n"
        "function t(key) { return `T[${key}]`; }\n"
        "function syncCompetitionFields() {}\n"
        + race_rule_note_key_fn
        + "\n"
        + sync_race_fields_fn
        + "\n"
        f"mockElements['race-type'] = {{ value: {json.dumps(race_type)} }};\n"
        "syncRaceFields();\n"
        "console.log(JSON.stringify({\n"
        "  textContent: mockElements['race-type-note'].textContent,\n"
        "  datasetI18n: mockElements['race-type-note'].dataset.i18n,\n"
        "}));\n"
    )
    return json.loads(_run_node(script))


def test_sync_race_fields_renders_max_power_note_text_on_screen():
    result = _run_sync_race_fields("max_power")
    assert result["textContent"] == "T[text.race_note_max_power]"
    assert result["datasetI18n"] == "text.race_note_max_power"


def test_sync_race_fields_renders_distance_note_text_on_screen():
    """Paired with the max_power case above so a hardcoded constant
    textContent (instead of a real per-race-type render) cannot pass both."""
    result = _run_sync_race_fields("distance")
    assert result["textContent"] == "T[text.race_note_distance]"
    assert result["datasetI18n"] == "text.race_note_distance"


# ---------------------------------------------------------------------------
# syncCompetitionFields DOM wiring -- proves the Completion Rule field's
# explanatory note actually renders its textContent, not merely that
# completionFieldState() was called or that dataset.i18n was written (a
# `completionNote.dataset.i18n = completion.noteKey;` assignment with the
# following `completionNote.textContent = t(completion.noteKey);` line
# deleted would still call completionFieldState() and satisfy every prior
# source-text assertion in this module, while leaving the disabled control
# on screen with no explanation of why -- exactly the bug this test exists
# to catch). Also asserts the disabled flag itself, and that the two
# distinct disabled-reasons (individual race vs. time-based team race)
# render genuinely different note text, not just different keys.
#
# It also records every classList.toggle("show", value) call on the note so
# the visibility layer can be pinned separately from the text layer: a
# `completionNote.classList.toggle("show", false)` (text still computed and
# assigned correctly, but the note forced hidden -- `.field-note` is
# `display: none` without the `show` class, CSS line 486) would satisfy the
# textContent assertions above while leaving the operator looking at a
# greyed-out control with no visible explanation. The recorded toggle value
# closes that gap.
# ---------------------------------------------------------------------------


def _run_sync_competition_fields(race_type: str, is_team_race: bool) -> dict:
    source = _stripped_script()
    completion_field_state_fn = _extract_function(source, "completionFieldState")
    sync_competition_fields_fn = _extract_function(source, "syncCompetitionFields")
    script = (
        "const mockElements = {};\n"
        "function makeEl() {\n"
        "  return {\n"
        "    textContent: '',\n"
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
        + completion_field_state_fn
        + "\n"
        + sync_competition_fields_fn
        + "\n"
        f"mockElements['competition-mode'] = {{ value: {json.dumps('team' if is_team_race else 'individual')} }};\n"
        f"mockElements['race-type'] = {{ value: {json.dumps(race_type)} }};\n"
        "syncCompetitionFields();\n"
        "console.log(JSON.stringify({\n"
        "  completionNoteText: mockElements['team-completion-note'].textContent,\n"
        "  completionDisabled: mockElements['team-completion-policy'].disabled,\n"
        "  completionNoteShowToggle: mockElements['team-completion-note'].classList.toggled['show'],\n"
        "}));\n"
    )
    return json.loads(_run_node(script))


def test_sync_competition_fields_renders_completion_note_text_for_individual_race():
    result = _run_sync_competition_fields("distance", is_team_race=False)
    assert result["completionNoteText"] == "T[text.team_field_note]"
    assert result["completionDisabled"] is True


def test_sync_competition_fields_renders_completion_note_text_for_time_based_team_race():
    result = _run_sync_competition_fields("max_power", is_team_race=True)
    assert result["completionNoteText"] == "T[text.completion_rule_time_based_note]"
    assert result["completionDisabled"] is True


def test_sync_competition_fields_individual_and_time_based_notes_render_different_text():
    individual = _run_sync_competition_fields("distance", is_team_race=False)
    time_based = _run_sync_competition_fields("max_power", is_team_race=True)
    assert individual["completionNoteText"] != time_based["completionNoteText"]


def test_sync_competition_fields_enables_completion_for_distance_team_race():
    result = _run_sync_competition_fields("distance", is_team_race=True)
    assert result["completionDisabled"] is False


def test_sync_competition_fields_shows_completion_note_for_individual_race():
    """A disabled control with a computed-but-never-shown note is the exact
    failure mode this test exists to catch: text alone isn't proof the
    operator can actually see the explanation."""
    result = _run_sync_competition_fields("distance", is_team_race=False)
    assert result["completionNoteShowToggle"] is True


def test_sync_competition_fields_shows_completion_note_for_time_based_team_race():
    result = _run_sync_competition_fields("max_power", is_team_race=True)
    assert result["completionNoteShowToggle"] is True


def test_sync_competition_fields_hides_completion_note_for_distance_team_race():
    """Completion Rule is enabled (noteKey is null) for a distance team
    race, so the note must be hidden -- there is nothing to explain."""
    result = _run_sync_competition_fields("distance", is_team_race=True)
    assert result["completionNoteShowToggle"] is False


# ---------------------------------------------------------------------------
# i18n: every new key must exist in BOTH inline dictionaries (and, per the
# existing symmetry/untranslated tests in test_static_page_i18n.py, carry a
# genuinely different zh-TW value). This module additionally asserts the
# zh-TW value is genuinely Chinese, closing the gap where a key could exist
# in both dicts with the same *English* string copy-pasted into zh-TW without
# actually differing character-for-character enough to look suspicious.
# ---------------------------------------------------------------------------

NEW_I18N_KEYS = [
    "text.race_note_distance",
    "text.race_note_calories",
    "text.race_note_time",
    "text.race_note_max_power",
    "text.completion_rule_time_based_note",
    "text.team_total_overridden_by_everyone_finishes",
    "leaderboard.mode_race_track_time_based_detail",
]

_CJK_RE = re.compile(r"[一-鿿]")


def _load_dictionaries() -> dict:
    source = _read()
    const_start = source.index("const dictionaries = {")
    const_open = source.index("{", const_start)
    const_close = _matching_brace_end(source, const_open)

    zh_marker = 'dictionaries["zh-TW"] = {'
    zh_start = source.index(zh_marker, const_close)
    zh_open = source.index("{", zh_start)
    zh_close = _matching_brace_end(source, zh_open)

    js = source[const_start : zh_close + 1] + ";"
    js += "\nconsole.log(JSON.stringify(dictionaries));"
    result = subprocess.run(
        ["node", "-e", js], capture_output=True, text=True, timeout=5
    )
    assert (
        result.returncode == 0
    ), f"node failed to evaluate dictionaries: {result.stderr}"
    return json.loads(result.stdout)


def test_new_i18n_keys_exist_in_both_inline_dictionaries():
    dictionaries = _load_dictionaries()
    en = dictionaries["en-US"]
    zh = dictionaries["zh-TW"]
    for key in NEW_I18N_KEYS:
        assert key in en, f"{key} missing from gameAdmin.html en-US dictionary"
        assert key in zh, f"{key} missing from gameAdmin.html zh-TW dictionary"
        assert en[key], f"{key} has an empty en-US value"
        assert (
            zh[key] != en[key]
        ), f"{key} zh-TW value is untranslated (identical to en-US)"
        assert _CJK_RE.search(
            zh[key]
        ), f"{key} zh-TW value has no CJK character: {zh[key]!r}"


# ---------------------------------------------------------------------------
# Secondary net: the new note element and the DOM-wiring call sites exist in
# source. Not a substitute for the executing tests above -- just confirms the
# pure helpers are actually reachable from the page.
# ---------------------------------------------------------------------------


def test_race_type_note_element_present_and_always_shown():
    source = _read()
    assert 'id="race-type-note"' in source
    match = re.search(r'<p class="field-note[^"]*"\s+id="race-type-note"', source)
    assert match, "race-type-note element not found with expected class"
    assert "show" in match.group(0), "race-type-note must always carry the show class"


def test_sync_race_fields_calls_sync_competition_fields_not_update_control_guidance_directly():
    source = _stripped_script()
    fn = _extract_function(source, "syncRaceFields")
    assert "syncCompetitionFields()" in fn
    assert "updateControlGuidance()" not in fn


def test_sync_competition_fields_uses_completion_field_state_helper():
    source = _stripped_script()
    fn = _extract_function(source, "syncCompetitionFields")
    assert "completionFieldState(" in fn


def test_update_control_guidance_uses_leaderboard_mode_note_key_helper():
    source = _stripped_script()
    fn = _extract_function(source, "updateControlGuidance")
    assert "leaderboardModeNoteKey(" in fn
    assert "teamRuleSummaryText(" in fn


def test_team_rule_summary_text_contains_the_override_sentence_key():
    source = _stripped_script()
    fn = _extract_function(source, "teamRuleSummaryText")
    assert "text.team_total_overridden_by_everyone_finishes" in fn
