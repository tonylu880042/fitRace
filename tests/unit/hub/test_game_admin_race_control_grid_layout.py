"""Regression tests for the Game Admin Race Control panel's two-column grid
layout (hub_server/static/gameAdmin.html), matching a reviewed Google Stitch
design's ARRANGEMENT only (CLAUDE.md: local-network-only venue deployment,
so no CDN/Google Fonts/Material Symbols/new dependency -- the page's
existing fonts/colors/CSS idioms are reused unchanged; only positions move).

Before this change the rules/live/local control blocks and the readiness
panel were all stacked full-width in a single column, and the six
readiness checks rendered in a horizontal 3-across strip. This moves to:

- A 12-column grid for the panel body: a span-8 left column holding the
  three control blocks (race rules, live presentation, local preference,
  in that order -- local preference last, smallest/most muted), and a
  span-4 right column holding the readiness panel as a persistent
  side-by-side checklist (not stacked below the settings).
- Inside the readiness card, the six checks stack one-per-row (vertical),
  not in a horizontal strip -- required by the narrower right column.
- Below the page's existing large breakpoint (1100px, already used by
  `.layout`/`.topbar` elsewhere in this file and in systemAdmin.html), the
  grid collapses to a single column, with the right column's content
  following the left column's in normal flow (so it renders below it).

Every existing element id, onchange/oninput handler, and option value is
unchanged -- this is a pure rearrangement, not a behaviour change.
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
    """Return the exact `<selector> { ... }` rule body (brace-matched), so a
    comment mentioning the selector can't satisfy an assertion about its
    actual declarations."""
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


def _named_div(container: str, div_id: str) -> str:
    """Return the full `<div id="div_id" ...> ... </div>` element (brace
    -- well, tag -- matched via a <div> depth counter), starting from its
    opening tag."""
    start = container.index(f'id="{div_id}"')
    tag_start = container.rfind("<div", 0, start)
    depth = 0
    tag_re = re.compile(r"</?div\b")
    pos = tag_start
    while True:
        match = tag_re.search(container, pos)
        assert match, f"unbalanced <div> while extracting {div_id!r}"
        if container[match.start() : match.start() + 5] == "</div":
            depth -= 1
            if depth == 0:
                return container[tag_start : match.end() + 1]
        else:
            depth += 1
        pos = match.end()


# ---------------------------------------------------------------------------
# 1. The 12-column grid: left column span 8, right column span 4.
# ---------------------------------------------------------------------------


def test_race_control_grid_is_twelve_columns():
    style = _style_block(_read())
    rule = _rule(style, ".race-control-grid")
    assert "display: grid" in rule
    assert "repeat(12, minmax(0, 1fr))" in rule


def test_left_column_spans_eight_and_right_column_spans_four():
    style = _style_block(_read())
    left_rule = _rule(style, ".race-control-left")
    right_rule = _rule(style, ".race-control-right")
    assert "grid-column: span 8" in left_rule
    assert "grid-column: span 4" in right_rule


def test_panel_body_has_left_and_right_columns_in_dom_order():
    panel = _race_control_panel(_read())
    assert 'class="race-control-grid"' in panel
    left_idx = panel.index('class="race-control-left"')
    right_idx = panel.index('class="race-control-right"')
    assert left_idx < right_idx


# ---------------------------------------------------------------------------
# 2. Left column holds the three control blocks in order; local preference
#    is last (bottom of the column) and stays visually de-emphasised.
# ---------------------------------------------------------------------------


def test_left_column_contains_rules_live_local_blocks_in_order():
    panel = _race_control_panel(_read())
    left_start = panel.index('class="race-control-left"')
    right_start = panel.index('class="race-control-right"')
    left_column = panel[left_start:right_start]

    rules_idx = left_column.index('id="rules-block"')
    live_idx = left_column.index('id="live-block"')
    local_idx = left_column.index('id="local-block"')
    assert rules_idx < live_idx < local_idx
    assert "control-block-muted" in _named_div(left_column, "local-block")


def test_right_column_contains_the_readiness_panel():
    panel = _race_control_panel(_read())
    right_start = panel.index('class="race-control-right"')
    right_column = panel[right_start:]
    assert 'id="race-readiness-panel"' in right_column[:2000]


def test_readiness_panel_is_not_inside_the_left_column():
    panel = _race_control_panel(_read())
    left_start = panel.index('class="race-control-left"')
    right_start = panel.index('class="race-control-right"')
    left_column = panel[left_start:right_start]
    assert 'id="race-readiness-panel"' not in left_column


# ---------------------------------------------------------------------------
# 3. Race rules block now uses a 2-column field grid (was 3), fields/ids
#    unchanged. Live presentation keeps its existing 2-column grid.
# ---------------------------------------------------------------------------

STAGED_FIELD_IDS = [
    "race-type",
    "competition-mode",
    "team-scoring-policy",
    "team-completion-policy",
    "race-target",
]


def test_rules_block_field_grid_is_two_columns_not_three():
    panel = _race_control_panel(_read())
    rules_block = _named_div(panel, "rules-block")
    assert 'class="grid-3"' not in rules_block
    assert 'class="grid-2"' in rules_block
    for field_id in STAGED_FIELD_IDS:
        assert f'id="{field_id}"' in rules_block


def test_live_block_field_grid_stays_two_columns():
    panel = _race_control_panel(_read())
    live_block = _named_div(panel, "live-block")
    assert 'class="grid-2"' in live_block
    assert 'id="leaderboard-display-mode"' in live_block
    assert 'id="start-sound-enabled"' in live_block


def test_all_race_types_and_leaderboard_views_preserved():
    source = _read()
    for value in ["distance", "calories", "time", "max_power"]:
        assert f'<option value="{value}"' in source
    for value in ["classic", "race_track", "team_battle", "sprint_board"]:
        assert f'<option value="{value}"' in source


# ---------------------------------------------------------------------------
# 4. Readiness checks stack vertically (one per row) -- required by the
#    narrower right-hand column -- with their coloured dot / neutral border
#    kept from the previous restructure.
# ---------------------------------------------------------------------------


def test_readiness_checks_are_a_single_column():
    style = _style_block(_read())
    rule = _rule(style, ".readiness-checks")
    assert (
        "grid-template-columns: 1fr" in rule
        or "grid-template-columns: repeat(1" in rule
    )
    assert "repeat(3" not in rule


def test_readiness_check_dot_and_neutral_border_still_present():
    style = _style_block(_read())
    assert ".readiness-check-dot.ok {" in style
    assert ".readiness-check-dot.warn {" in style
    assert ".readiness-check-dot.block {" in style
    assert ".readiness-card.block {" in style
    assert ".readiness-check.block {" not in style


# ---------------------------------------------------------------------------
# 5. Responsive collapse at the page's existing large breakpoint (1100px,
#    already used by .layout/.topbar), right column falls below the left.
# ---------------------------------------------------------------------------


def test_grid_collapses_to_single_column_at_existing_large_breakpoint():
    style = _style_block(_read())
    media_start = style.index("@media (max-width: 1100px)")
    media_end = style.index("@media (max-width: 640px)")
    media_block = style[media_start:media_end]
    assert ".race-control-grid" in media_block
    assert "grid-template-columns: 1fr" in _rule(media_block, ".race-control-grid")


def test_no_new_breakpoint_values_introduced():
    """The page (and systemAdmin.html) already standardize on 1100px and
    640px; this change must reuse them, not invent a third breakpoint."""
    style = _style_block(_read())
    breakpoints = set(re.findall(r"@media \(max-width: (\d+)px\)", style))
    assert breakpoints == {"1100", "640"}
