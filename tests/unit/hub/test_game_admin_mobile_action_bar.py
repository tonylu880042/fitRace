"""Regression tests for the Game Admin race action bar on narrow viewports
(hub_server/static/gameAdmin.html).

Measured at a narrow viewport: the page is 2487px tall and the Start Race
button sits at y=2181 -- roughly three screens of scrolling below every
config field, on the control an operator presses most.

Fix, inside the EXISTING `@media (max-width: 640px)` block only (this file
and systemAdmin.html already standardize on {1100, 640}; see
test_no_new_breakpoint_values_introduced in
test_game_admin_race_control_grid_layout.py, which this change must not
break):

1. `.action-bar` (the row holding Save/Start/Stop/Reset) becomes
   `position: sticky; bottom: 0;` with an opaque background (not
   `transparent`, since it overlays scrolling content) and a `z-index`
   above the panel content, plus bottom padding on the page shell so the
   sticky bar never permanently covers the last panel.
2. The destructive Stop/Reset pair (the first `.button-group` in
   `.action-bar`) is visually separated from Save/Start (the second
   `.button-group`) by a border, so a thumb overshooting while reaching for
   Start cannot land on Reset.

Every assertion here parses the exact `.action-bar` (and
`.button-group + .button-group`) rule bodies out of the 640px media block
via brace matching, per the technique in
test_game_admin_race_control_grid_layout.py -- so a comment mentioning
"sticky" or "border" can't satisfy it; the parsed rule must actually
declare it.
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
    """Return the exact `<selector> { ... }` rule body (brace-matched) from
    within `block`, so a comment mentioning the selector can't satisfy an
    assertion about its actual declarations."""
    idx = block.index(selector + " {")
    brace_start = block.index("{", idx)
    return _brace_match(block, brace_start)


def test_action_bar_is_sticky_with_opaque_background_on_mobile():
    style = _style_block(_read())
    mobile = _media_block_640(style)
    rule = _rule(mobile, ".action-bar")
    assert "position: sticky" in rule
    assert "bottom: 0" in rule
    assert "z-index" in rule
    assert "background: transparent" not in rule
    assert "background: none" not in rule
    # Must declare its own (opaque) background, not merely inherit one.
    assert "background:" in rule


def test_page_shell_gets_bottom_padding_for_the_sticky_bar():
    style = _style_block(_read())
    mobile = _media_block_640(style)
    assert "padding-bottom" in mobile


def test_destructive_button_group_is_visually_separated_from_save_start():
    style = _style_block(_read())
    mobile = _media_block_640(style)
    rule = _rule(mobile, ".action-bar .button-group + .button-group")
    assert "border-top" in rule
    assert "none" not in rule.split("border-top")[1].split(";")[0]


def test_no_new_breakpoint_values_introduced_still_holds():
    import re

    style = _style_block(_read())
    breakpoints = set(re.findall(r"@media \(max-width: (\d+)px\)", style))
    assert breakpoints == {"1100", "640"}
