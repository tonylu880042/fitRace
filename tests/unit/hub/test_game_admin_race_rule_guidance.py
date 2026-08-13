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


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
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
    assert "text.team_total_overridden_by_everyone_finishes" in fn
