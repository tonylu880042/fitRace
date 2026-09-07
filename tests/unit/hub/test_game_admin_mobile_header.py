"""Regression tests for the Game Admin header/summary chrome on narrow
viewports (hub_server/static/gameAdmin.html).

At <=640px `.summary-grid { grid-template-columns: 1fr }` turned the four
summary cards into four full-width rows, and `.top-actions`' five nav
buttons wrapped into a tall stack, so Race Control started around y=677 --
most of the first screen was chrome.

Fix, inside the EXISTING `@media (max-width: 640px)` block only (pinned to
{1100, 640} by test_no_new_breakpoint_values_introduced in
test_game_admin_race_control_grid_layout.py):

1. `.summary-grid` moves to 2 columns instead of 1, halving its height.
   `.grid-2`/`.grid-3`/`.guidance-grid` stay at 1 column.
2. `.top-actions` becomes a single horizontally-scrollable row
   (`flex-wrap: nowrap; overflow-x: auto;`) with `flex: 0 0 auto` on its
   children, instead of wrapping into a tall stack.

Nothing else changes: no nav button or summary card is hidden, reordered,
or removed (checked below).

Assertions parse the exact rule bodies out of the 640px media block via
brace matching (same technique as
test_game_admin_race_control_grid_layout.py / test_game_admin_mobile_
action_bar.py), so a comment mentioning a property can't satisfy them.
"""

from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _style_block(source: str) -> str:
    start = source.index("<style>") + len("<style>")
    end = source.index("</style>", start)
    return source[start:end]


def _brace_match(text: str, brace_start: int) -> str:
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace_start : i + 1]
        i += 1
    raise AssertionError("unbalanced braces")


def _media_block_640(style: str) -> str:
    start = style.index("@media (max-width: 640px)")
    brace_start = style.index("{", start)
    return _brace_match(style, brace_start)


def _rule(block: str, selector: str) -> str:
    idx = block.index(selector + " {")
    brace_start = block.index("{", idx)
    return _brace_match(block, brace_start)


def _rule_containing_selector(block: str, selector: str) -> str:
    """Return the `{ ... }` body of whichever rule's selector LIST contains
    `selector` as one of its comma-separated entries -- for rules like
    `.grid-2, .grid-3, .guidance-grid { ... }` where the exact selector
    text isn't followed directly by ` {`."""
    idx = 0
    while True:
        idx = block.index(selector, idx)
        # Selector must be a whole token, not a substring of a longer class
        after = block[idx + len(selector) : idx + len(selector) + 1]
        before_ok = idx == 0 or not block[idx - 1].isalnum()
        if before_ok and after in (",", " ", "\n", "\t"):
            brace_start = block.index("{", idx)
            between = block[idx:brace_start]
            if "{" not in between:
                return _brace_match(block, brace_start)
        idx += len(selector)


def test_summary_grid_is_two_columns_on_mobile():
    style = _style_block(_read())
    mobile = _media_block_640(style)
    rule = _rule_containing_selector(mobile, ".summary-grid")
    assert "repeat(2, minmax(0, 1fr))" in rule


def test_grid_2_grid_3_guidance_grid_stay_single_column_on_mobile():
    style = _style_block(_read())
    mobile = _media_block_640(style)
    for selector in (".grid-2", ".grid-3", ".guidance-grid"):
        rule = _rule_containing_selector(mobile, selector)
        assert "grid-template-columns: 1fr" in rule, selector


def test_top_actions_scrolls_horizontally_instead_of_wrapping():
    style = _style_block(_read())
    mobile = _media_block_640(style)
    rule = _rule_containing_selector(mobile, ".top-actions")
    assert "flex-wrap: nowrap" in rule
    assert "overflow-x: auto" in rule


def test_top_actions_children_do_not_shrink_or_grow():
    style = _style_block(_read())
    mobile = _media_block_640(style)
    rule = _rule_containing_selector(mobile, ".top-actions > *")
    assert "flex: 0 0 auto" in rule


def test_no_nav_button_or_summary_card_removed():
    source = _read()
    for nav_key in (
        "nav.dashboard",
        "nav.system_admin",
        "nav.class_admin",
        "nav.signup",
        "nav.operator_unlock",
    ):
        assert f'data-i18n="{nav_key}"' in source
    for summary_id in (
        "summary-state",
        "summary-stations",
        "summary-readiness",
        "summary-sound",
    ):
        assert f'id="{summary_id}"' in source


def test_no_new_breakpoint_values_introduced_still_holds():
    import re

    style = _style_block(_read())
    breakpoints = set(re.findall(r"@media \(max-width: (\d+)px\)", style))
    assert breakpoints == {"1100", "640"}
