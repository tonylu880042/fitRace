"""Regression tests for the Game Admin Race Control panel's bottom action
bar (hub_server/static/gameAdmin.html), the second half of the Stitch-design
ARRANGEMENT-only restructure (see also
tests/unit/hub/test_game_admin_race_control_grid_layout.py for the 12-col
settings/readiness split).

Before this change the four race-action buttons (save, start, stop, reset)
sat in one `.button-row` with the destructive pair pushed right via
`.button-row-danger`'s `margin-left: auto`. This moves them into a
dedicated bar pinned to the bottom of the panel, separated from the panel
content by a top border, with the destructive/lower-priority pair
(stop, reset) on the LEFT and the primary pair (save, start) on the
RIGHT -- opposite ends, so a mis-click on a destructive action during a
live race is much less likely than when the two pairs sit side by side.

Every existing element id, onclick handler, and the countdown/processing
text wiring in renderRaceActionButtons()/saveRaceConfig()/
startRaceAction() (pinned separately by
tests/unit/hub/test_game_admin_race_control_restructure.py) is unchanged
-- this is a pure rearrangement.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _style_block(source: str) -> str:
    start = source.index("<style>") + len("<style>")
    end = source.index("</style>", start)
    return source[start:end]


def _rule(style: str, selector: str) -> str:
    idx = style.index(selector + " {")
    brace_start = style.index("{", idx)
    depth = 0
    i = brace_start
    while i < len(style):
        if style[i] == "{":
            depth += 1
        elif style[i] == "}":
            depth -= 1
            if depth == 0:
                return style[brace_start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces for selector {selector!r}")


def _race_control_panel(source: str) -> str:
    marker = 'aria-labelledby="race-title"'
    start = source.rfind("<section", 0, source.index(marker))
    end = source.index("</section>", start)
    return source[start:end]


# ---------------------------------------------------------------------------
# 1. The action bar exists, with a top border separating it from content.
# ---------------------------------------------------------------------------


def test_action_bar_css_has_a_top_border_and_pushes_groups_to_opposite_ends():
    style = _style_block(_read())
    rule = _rule(style, ".action-bar")
    assert "border-top:" in rule
    assert "justify-content: space-between" in rule


def test_race_control_panel_uses_action_bar_not_the_old_button_row():
    panel = _race_control_panel(_read())
    assert 'class="action-bar"' in panel
    # The old wrapper class for this specific bar is gone; .button-row is
    # still a legitimate generic class used by the (unrelated) login modal
    # elsewhere in the file, so we only assert it is absent from this panel.
    assert 'class="button-row"' not in panel


# ---------------------------------------------------------------------------
# 2. Destructive pair (stop, reset) on the left; primary pair (save, start)
#    on the right -- opposite ends, in that DOM order.
# ---------------------------------------------------------------------------


def test_destructive_buttons_are_the_first_group_in_the_action_bar():
    panel = _race_control_panel(_read())
    bar_start = panel.index('class="action-bar"')
    bar_start = panel.rfind("<div", 0, bar_start)
    bar_end = panel.index("</div>", panel.index('id="race-message"'))
    action_bar = panel[bar_start:bar_end]

    stop_idx = action_bar.index('id="btn-stop"')
    reset_idx = action_bar.index('id="btn-reset"')
    save_idx = action_bar.index('id="btn-save-race"')
    start_idx = action_bar.index('id="btn-start-race"')

    assert stop_idx < reset_idx < save_idx < start_idx


def test_action_bar_buttons_keep_their_ids_handlers_and_i18n_keys():
    panel = _race_control_panel(_read())
    assert 'id="btn-save-race"' in panel
    assert 'onclick="saveRaceConfig()"' in panel
    assert 'data-i18n="button.save_race"' in panel
    assert 'id="btn-start-race"' in panel
    assert 'onclick="startRaceAction()"' in panel
    assert 'data-i18n="button.start_race"' in panel
    assert 'id="btn-stop"' in panel
    assert 'onclick="stopRace()"' in panel
    assert 'data-i18n="button.stop_race"' in panel
    assert 'id="btn-reset"' in panel
    assert 'onclick="resetRace()"' in panel
    assert 'data-i18n="button.reset_race"' in panel


def test_render_race_action_buttons_still_targets_the_same_ids():
    """The disabled-state/text logic in renderRaceActionButtons() (pinned
    in detail by test_game_admin_race_control_restructure.py) must still
    look up the same button ids after the bar was moved -- a rename here
    without updating that function would silently break Save/Start."""
    source = _read()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    script = source[start:end]
    func_start = script.index("function renderRaceActionButtons()")
    func_end = script.index("function readinessStatusClass", func_start)
    body = script[func_start:func_end]
    assert '$("btn-save-race")' in body
    assert '$("btn-start-race")' in body


# ---------------------------------------------------------------------------
# 3. Panel content order: countdown banner, then the action bar, then the
#    status text -- unchanged relative positions.
# ---------------------------------------------------------------------------


def test_countdown_banner_precedes_action_bar_precedes_status_text():
    panel = _race_control_panel(_read())
    countdown_idx = panel.index('id="countdown-status"')
    action_bar_idx = panel.index('class="action-bar"')
    message_idx = panel.index('id="race-message"')
    assert countdown_idx < action_bar_idx < message_idx


# ---------------------------------------------------------------------------
# 4. Responsive: reuses the page's existing 640px breakpoint, no new one.
# ---------------------------------------------------------------------------


def test_action_bar_stacks_at_the_existing_small_breakpoint():
    style = _style_block(_read())
    media_start = style.index("@media (max-width: 640px)")
    media_block = style[media_start:]
    assert ".action-bar" in media_block


def test_no_new_breakpoint_values_introduced():
    style = _style_block(_read())
    breakpoints = set(re.findall(r"@media \(max-width: (\d+)px\)", style))
    assert breakpoints == {"1100", "640"}
