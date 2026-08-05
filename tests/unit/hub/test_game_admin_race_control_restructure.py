"""Regression tests for the Game Admin Race Control panel restructure
(hub_server/static/gameAdmin.html).

Before this change the panel was one undifferentiated `.grid-3` holding
eight controls that mix three different kinds of thing: STAGED settings
that need an explicit Save (race-type, competition-mode,
team-scoring-policy, team-completion-policy, race-target), IMMEDIATE
settings that apply on change (leaderboard-display-mode,
start-sound-enabled), and a LOCAL-ONLY browser preference that is not a
race setting at all (auto-refresh). It also hid the team-only fields with
`display:none` (causing the panel to reflow/jump when switching
individual/team), used red for both "the whole race is blocked" and
"one check needs attention" (indistinguishable at a glance), and swapped a
single button's own label between Save/Start depending on dirty state
(dangerous mid-race: a mis-timed click could start the race instead of
saving a settings tweak, or vice versa).

This test module pins:
1. Three separately titled, bordered blocks: race rules (STAGED, with a
   dirty badge), live presentation (IMMEDIATE, with an always-visible
   "applies immediately" pill), and local preference (auto-refresh only,
   visually de-emphasised with a "this browser only" note) -- every
   existing element id/onchange/oninput/option-value preserved.
2. Team-only fields stay in the DOM (no more `is-hidden`/display:none)
   when competition is individual; they are visually dimmed/disabled with
   a note, and disabling them (rather than removing them) does not change
   what configureRace() sends to the server.
3. Red is exclusive to the readiness CARD (blocked = cannot start).
   Individual checks use a small status dot with a neutral border, and a
   one-line remedy count is shown above the check list while blocked.
4. Two independent buttons -- a save action gated on unsaved changes, and
   a start action gated on readiness -- replacing the single
   dirty-state-swapping button, while preserving the existing countdown
   and processing states.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _style_block(source: str) -> str:
    start = source.index("<style>") + len("<style>")
    end = source.index("</style>", start)
    return source[start:end]


def _script(source: str) -> str:
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return source[start:end]


def _stripped_script(source: str) -> str:
    return _strip_js_comments(_script(source))


def _race_control_panel(source: str) -> str:
    marker = 'aria-labelledby="race-title"'
    start = source.rfind("<section", 0, source.index(marker))
    end = source.index("</section>", start)
    return source[start:end]


def _extract_function_body(script: str, func_name: str) -> str:
    """Return `function <func_name>(...) { ... }` up to the next top-level
    function definition, from a comment-stripped script -- fails loudly if
    the function isn't genuinely defined (a comment can't satisfy this)."""
    def_pattern = re.compile(
        r"(?:async\s+)?function\s+" + re.escape(func_name) + r"\s*\("
    )
    match = def_pattern.search(script)
    assert match, f"No `function {func_name}(...)` definition found"
    stop_pattern = re.compile(r"\n\s*(?:async\s+)?function\s+\w+\s*\(")
    stop_match = stop_pattern.search(script, match.end())
    end = stop_match.start() if stop_match else len(script)
    return script[match.start() : end]


def _en_zh_blocks(source: str):
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 12000]
    return en_block, zh_block


# ---------------------------------------------------------------------------
# 1. Three separately-titled blocks, correctly grouped, ids/handlers/values
#    preserved.
# ---------------------------------------------------------------------------

STAGED_FIELD_IDS = [
    "race-type",
    "competition-mode",
    "team-scoring-policy",
    "team-completion-policy",
    "race-target",
]
IMMEDIATE_FIELD_IDS = ["leaderboard-display-mode", "start-sound-enabled"]


def _block(panel: str, block_id: str) -> str:
    start = panel.index(f'id="{block_id}"')
    tag_start = panel.rfind("<div", 0, start)
    depth = 0
    tag_re = re.compile(r"</?div\b")
    pos = tag_start
    while True:
        match = tag_re.search(panel, pos)
        assert match, f"unbalanced <div> while extracting block {block_id!r}"
        if panel[match.start() : match.start() + 5] == "</div":
            depth -= 1
            if depth == 0:
                return panel[tag_start : match.end() + 1]
        else:
            depth += 1
        pos = match.end()


def test_race_control_panel_has_three_ordered_blocks_with_i18n_titles():
    panel = _race_control_panel(_read())
    rules_idx = panel.index('id="rules-block"')
    live_idx = panel.index('id="live-block"')
    local_idx = panel.index('id="local-block"')
    assert rules_idx < live_idx < local_idx

    rules_block = _block(panel, "rules-block")
    live_block = _block(panel, "live-block")
    local_block = _block(panel, "local-block")

    assert 'data-i18n="panel.race_rules"' in rules_block
    assert 'data-i18n="panel.live_presentation"' in live_block
    assert 'data-i18n="panel.local_preference"' in local_block


def test_staged_fields_live_in_rules_block_with_original_handlers():
    panel = _race_control_panel(_read())
    rules_block = _block(panel, "rules-block")

    for field_id in STAGED_FIELD_IDS:
        assert f'id="{field_id}"' in rules_block

    assert 'onchange="syncRaceFields(); markRaceConfigDirty()"' in rules_block
    assert (
        'onchange="syncCompetitionFields(); renderStations(); markRaceConfigDirty()"'
        in rules_block
    )
    assert (
        rules_block.count(
            'onchange="updateControlGuidance(); renderStations(); markRaceConfigDirty()"'
        )
        == 2
    )
    assert 'oninput="markRaceConfigDirty()"' in rules_block

    for field_id in IMMEDIATE_FIELD_IDS + ["auto-refresh"]:
        assert f'id="{field_id}"' not in rules_block


def test_immediate_fields_live_in_live_block_with_original_handlers():
    panel = _race_control_panel(_read())
    live_block = _block(panel, "live-block")

    for field_id in IMMEDIATE_FIELD_IDS:
        assert f'id="{field_id}"' in live_block

    assert "setLeaderboardDisplayMode(this.value)" in live_block
    assert "setStartCountdownSound(this.value === 'true')" in live_block
    assert "markRaceConfigDirty" not in live_block

    for field_id in STAGED_FIELD_IDS + ["auto-refresh"]:
        assert f'id="{field_id}"' not in live_block


def test_local_field_lives_alone_in_local_block():
    panel = _race_control_panel(_read())
    local_block = _block(panel, "local-block")

    assert 'id="auto-refresh"' in local_block
    for field_id in STAGED_FIELD_IDS + IMMEDIATE_FIELD_IDS:
        assert f'id="{field_id}"' not in local_block


def test_all_race_types_and_leaderboard_views_preserved():
    source = _read()
    for value in ["distance", "calories", "time", "max_power"]:
        assert f'<option value="{value}"' in source
    for value in ["classic", "race_track", "team_battle", "sprint_board"]:
        assert f'<option value="{value}"' in source


# ---------------------------------------------------------------------------
# 2. Dirty badge on the rules block; always-visible pill on the live block;
#    de-emphasised local block with a browser-only note.
# ---------------------------------------------------------------------------


def test_rules_block_has_a_dirty_badge_hidden_by_default_in_css():
    source = _read()
    panel = _race_control_panel(source)
    rules_block = _block(panel, "rules-block")
    assert 'id="rules-dirty-badge"' in rules_block
    assert 'data-i18n="badge.unsaved_changes"' in rules_block

    style = _style_block(source)
    rule = style[style.index(".badge-dirty {") : style.index(".badge-dirty.show")]
    assert "display: none" in rule


def test_render_race_action_buttons_toggles_dirty_badge_from_state():
    script = _stripped_script(_read())
    body = _extract_function_body(script, "renderRaceActionButtons")
    assert '$("rules-dirty-badge")' in body
    assert "state.raceConfigDirty" in body
    assert ".classList.toggle(" in body


def test_live_block_has_a_static_always_visible_immediate_pill():
    source = _read()
    panel = _race_control_panel(source)
    live_block = _block(panel, "live-block")
    assert "pill-immediate" in live_block
    assert 'data-i18n="pill.applies_immediately"' in live_block

    style = _style_block(source)
    assert ".pill-immediate" in style


def test_local_block_is_visually_deemphasised_with_browser_only_note():
    source = _read()
    panel = _race_control_panel(source)
    local_block = _block(panel, "local-block")
    assert "control-block-muted" in local_block
    assert 'data-i18n="text.local_preference_note"' in local_block


# ---------------------------------------------------------------------------
# 3. Team-only fields stay in place, dimmed/disabled with a note -- no more
#    is-hidden/display:none based hiding.
# ---------------------------------------------------------------------------


def test_team_fields_no_longer_use_is_hidden_toggle():
    script = _stripped_script(_read())
    body = _extract_function_body(script, "syncCompetitionFields")
    assert "is-hidden" not in body
    assert '$("team-scoring-field").classList.toggle("is-disabled"' in body
    assert '$("team-completion-field").classList.toggle("is-disabled"' in body


def test_sync_competition_fields_disables_team_selects_for_individual_mode():
    script = _stripped_script(_read())
    body = _extract_function_body(script, "syncCompetitionFields")
    assert '$("team-scoring-policy").disabled = !isTeamRace;' in body
    assert '$("team-completion-policy").disabled = !isTeamRace;' in body


def test_team_fields_carry_team_only_note_with_i18n():
    source = _read()
    panel = _race_control_panel(source)
    rules_block = _block(panel, "rules-block")
    assert 'id="team-scoring-note"' in rules_block
    assert 'id="team-completion-note"' in rules_block
    assert rules_block.count('data-i18n="text.team_field_note"') == 2


def test_team_fields_stay_in_the_dom_regardless_of_competition_mode():
    """The old behaviour used display:none which removes the fields from
    layout flow entirely; the new behaviour must keep them present (just
    dimmed), so their ids/selects/notes are unconditionally in the markup
    -- there is no runtime toggle that deletes/hides them via 'is-hidden'
    anywhere in the file."""
    source = _read()
    assert "is-hidden" not in source


# ---------------------------------------------------------------------------
# 4. Readiness color semantics: red exclusive to the card; checks use a
#    neutral border + coloured dot; remedy summary above the check list.
# ---------------------------------------------------------------------------


def test_readiness_check_css_no_longer_colors_its_border():
    style = _style_block(_read())
    assert ".readiness-check.ok {" not in style
    assert ".readiness-check.warn {" not in style
    assert ".readiness-check.block {" not in style
    # The card itself keeps its exclusive red outline for "blocked".
    assert ".readiness-card.block {" in style


def test_readiness_check_dot_css_carries_the_status_color():
    style = _style_block(_read())
    assert ".readiness-check-dot.ok {" in style
    assert ".readiness-check-dot.warn {" in style
    assert ".readiness-check-dot.block {" in style


def test_render_readiness_panel_emits_a_dot_per_check_not_a_colored_border():
    script = _stripped_script(_read())
    body = _extract_function_body(script, "renderReadinessPanel")
    assert '<div class="readiness-check">' in body
    assert 'class="readiness-check-dot ${klass}"' in body
    assert 'class="readiness-check ${klass}"' not in body


def test_render_readiness_panel_shows_remedy_count_above_the_checklist_when_blocked():
    script = _stripped_script(_read())
    body = _extract_function_body(script, "renderReadinessPanel")
    assert "readiness-remedy" in body
    assert 't("text.readiness_remedy_summary"' in body

    remedy_idx = body.index("readiness-remedy")
    checks_idx = body.index('<div class="readiness-checks">')
    assert remedy_idx < checks_idx


def test_readiness_remedy_key_present_with_count_param_in_both_dictionaries():
    en_block, zh_block = _en_zh_blocks(_read())
    assert '"text.readiness_remedy_summary":' in en_block
    assert (
        "{count}" in en_block[en_block.index('"text.readiness_remedy_summary"') :][:200]
    )
    assert '"text.readiness_remedy_summary":' in zh_block


# ---------------------------------------------------------------------------
# 5. Two independent buttons replace the single dirty-state-swapping
#    button, preserving countdown/processing states.
# ---------------------------------------------------------------------------


def test_old_single_race_action_button_is_gone():
    source = _read()
    assert 'id="btn-race-action"' not in source
    assert "handleRaceAction" not in source


def test_two_race_action_buttons_exist_with_distinct_onclick_handlers():
    source = _read()
    panel = _race_control_panel(source)
    assert 'id="btn-save-race"' in panel
    assert 'id="btn-start-race"' in panel
    assert 'onclick="saveRaceConfig()"' in panel
    assert 'onclick="startRaceAction()"' in panel
    # Existing destructive actions keep working, unchanged ids/handlers.
    assert 'id="btn-stop"' in panel
    assert 'onclick="stopRace()"' in panel
    assert 'id="btn-reset"' in panel
    assert 'onclick="resetRace()"' in panel


def test_save_race_config_is_a_real_defined_function_that_calls_configure_race():
    script = _stripped_script(_read())
    body = _extract_function_body(script, "saveRaceConfig")
    assert "await configureRace();" in body


def test_start_race_action_is_a_real_defined_function_that_calls_start_race():
    script = _stripped_script(_read())
    body = _extract_function_body(script, "startRaceAction")
    assert "await startRace();" in body


def test_save_button_enabled_only_when_there_are_unsaved_changes():
    script = _stripped_script(_read())
    body = _extract_function_body(script, "renderRaceActionButtons")
    save_disabled_line = next(
        line for line in body.splitlines() if "saveBtn.disabled" in line
    )
    assert "!state.raceConfigDirty" in save_disabled_line


def test_start_button_disabled_when_readiness_not_ready():
    script = _stripped_script(_read())
    body = _extract_function_body(script, "renderRaceActionButtons")
    start_disabled_line = next(
        line for line in body.splitlines() if "startBtn.disabled" in line
    )
    assert "!readinessReady" in start_disabled_line


def test_render_race_action_buttons_preserves_countdown_and_processing_text():
    script = _stripped_script(_read())
    body = _extract_function_body(script, "renderRaceActionButtons")
    assert 't("text.countdown_active")' in body
    assert 't("text.processing_race_action")' in body
    assert 't("button.save_race")' in body
    assert 't("button.start_race")' in body


def test_save_and_start_actions_guard_against_pending_countdown_and_running():
    script = _stripped_script(_read())
    for func_name in ("saveRaceConfig", "startRaceAction"):
        body = _extract_function_body(script, func_name)
        assert (
            'if (state.raceActionPending || state.countdownActive || state.race?.state === "RUNNING") return;'
            in body
        )


# ---------------------------------------------------------------------------
# 6. i18n: every new key present + symmetric in both dictionaries.
# ---------------------------------------------------------------------------

NEW_I18N_KEYS = [
    "panel.race_rules",
    "panel.live_presentation",
    "panel.local_preference",
    "badge.unsaved_changes",
    "pill.applies_immediately",
    "text.local_preference_note",
    "text.team_field_note",
    "text.readiness_remedy_summary",
]


def test_new_restructure_i18n_keys_present_in_both_dictionaries():
    en_block, zh_block = _en_zh_blocks(_read())
    for key in NEW_I18N_KEYS:
        assert f'"{key}":' in en_block, f"{key} missing from en-US dictionary"
        assert f'"{key}":' in zh_block, f"{key} missing from zh-TW dictionary"


def test_i18n_keys_stay_symmetric_between_dictionaries():
    en_block, zh_block = _en_zh_blocks(_read())
    en_keys = set(re.findall(r'"([a-zA-Z0-9_.]+)":\s*"', en_block))
    zh_keys = set(re.findall(r'"([a-zA-Z0-9_.]+)":\s*"', zh_block))
    missing_from_zh = en_keys - zh_keys
    missing_from_en = zh_keys - en_keys
    assert not missing_from_zh, f"keys missing from zh-TW dict: {missing_from_zh}"
    assert not missing_from_en, f"keys missing from en-US dict: {missing_from_en}"
