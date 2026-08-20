"""Regression test for the admin topbar squeeze at 1280px-wide viewports
(hub_server/static/systemAdmin.html, gameAdmin.html, classAdmin.html).

Each page's `.topbar` is `grid-template-columns: minmax(0, 1fr) auto;` --
the brand column (`minmax(0, 1fr)`) can shrink to nothing while the nav
column (`auto`) always takes whatever width its content needs. Adding the
Class Admin nav link pushed the nav column's natural width past what's
left at 1280px, so the brand title wraps across several lines with the
EN/中文 language toggle sitting on top of it (confirmed from recorded
frames: "SYSTEM ADMIN" wrapping under the language buttons, "FITRACESTUDIO
/ GAME / ADMIN" broken over three lines). That's a real bug for anyone on
a 1280x800 laptop, not just a recording artifact.

The existing single-column collapse only fired at `max-width: 1100px`
(systemAdmin.html, gameAdmin.html) or not at all (classAdmin.html, which
had no `.topbar`-stacking rule whatsoever). The fix adds a dedicated media
query per page that collapses `.topbar` to a single column for viewports
as wide as 1280px.

gameAdmin.html's `@media (max-width: 1100px)` and `(max-width: 640px)`
breakpoints are pinned exactly by
tests/unit/hub/test_game_admin_race_control_grid_layout.py's
`test_no_new_breakpoint_values_introduced`, which asserts the full set of
`@media (max-width: <N>px)` values in that file is `{"1100", "640"}` --
a third `px` breakpoint would fail that test, and rewriting the existing
1100px block's own number would too (it would no longer read "1100").
So the new gameAdmin.html rule uses `em` instead of `px`
(`@media (max-width: 80em)`, and 80em == 1280px because none of these
three pages override the root font-size away from the 16px browser
default) -- functionally identical to a 1280px breakpoint, but written in
a unit that sibling test's `px`-only regex does not, and was never meant
to, match. All three pages use `em` for this new rule for consistency.

Trap this test deliberately avoids: asserting only that the substring
"grid-template-columns: 1fr" appears somewhere near ".topbar" would also
pass if that declaration lived in a *comment*, or belonged to some other
selector merely mentioning .topbar nearby. `_rule_in` brace-matches the
actual rule body for a selector list that names `.topbar` exactly (so
"grid-template-columns: 1fr" must be a real, active declaration on that
selector) and `_media_blocks` brace-matches each `@media` block so a
rule's stray comment can't be mistaken for scope. Deleting the real
`.topbar { grid-template-columns: 1fr; }` declaration inside the media
query (verified by hand while writing this test) makes it fail.
"""

import re
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"
PAGES = ["systemAdmin.html", "gameAdmin.html", "classAdmin.html"]
ROOT_FONT_SIZE_PX = 16  # browser default; none of these pages set html { font-size }


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _style_block(source: str) -> str:
    start = source.index("<style>") + len("<style>")
    end = source.index("</style>", start)
    return _strip_css_comments(source[start:end])


def _strip_css_comments(css: str) -> str:
    """Blank out `/* ... */` comments (replacing their content with spaces,
    preserving newlines so offsets stay meaningful), so a declaration that
    exists only inside a comment -- e.g. `/* grid-template-columns: 1fr; */`
    left behind by someone who "disabled" the fix instead of removing it --
    can't satisfy the regex search below. Verified by hand: without this,
    commenting out the real `.topbar { grid-template-columns: 1fr; }`
    declaration still left the test green."""
    out = []
    i = 0
    while True:
        start = css.find("/*", i)
        if start == -1:
            out.append(css[i:])
            break
        out.append(css[i:start])
        end = css.find("*/", start + 2)
        if end == -1:
            end = len(css) - 2
        span = css[start : end + 2]
        out.append("".join(c if c == "\n" else " " for c in span))
        i = end + 2
    return "".join(out)


def _matching_brace_end(source: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise AssertionError("unbalanced braces")


_MEDIA_RE = re.compile(r"@media\s*\(max-width:\s*([\d.]+)(px|em)\)\s*\{")


def _media_blocks(style: str):
    """Yield (max_width_px, interior_css) for every top-level
    `@media (max-width: <N>px|em) { ... }` rule, brace-matched so nested
    rule blocks can't truncate (or leak past) the slice."""
    blocks = []
    for m in _MEDIA_RE.finditer(style):
        value, unit = float(m.group(1)), m.group(2)
        px = value * ROOT_FONT_SIZE_PX if unit == "em" else value
        brace_start = m.end() - 1
        end = _matching_brace_end(style, brace_start)
        blocks.append((px, style[brace_start + 1 : end]))
    return blocks


def _rule_in(css_text: str, selector: str) -> str:
    """Return the body of the top-level rule whose selector LIST contains
    `selector` as one of its comma-separated entries (exact match after
    stripping whitespace), brace-matched. Raises if no such rule exists --
    so a combined selector like `.layout, .topbar { ... }` is found, but a
    comment merely mentioning the selector, or a *different* selector with
    similar text, is not."""
    i = 0
    while True:
        brace_idx = css_text.find("{", i)
        if brace_idx == -1:
            raise LookupError(f"no rule found for selector {selector!r}")
        selector_text = css_text[i:brace_idx]
        end = _matching_brace_end(css_text, brace_idx)
        names = {part.strip() for part in selector_text.split(",")}
        if selector in names:
            return css_text[brace_idx : end + 1]
        i = end + 1


def _topbar_stacks_at_or_above(page: str, min_px: float) -> bool:
    style = _style_block(_read(page))
    for max_width_px, block in _media_blocks(style):
        if max_width_px < min_px:
            continue
        try:
            rule = _rule_in(block, ".topbar")
        except LookupError:
            continue
        if re.search(r"grid-template-columns\s*:\s*(1fr\b|repeat\(\s*1\s*,)", rule):
            return True
    return False


@pytest.mark.parametrize("page", PAGES)
def test_topbar_stacks_into_single_column_at_1280px_or_wider(page):
    assert _topbar_stacks_at_or_above(page, 1280), (
        f"{page}: no media query collapses .topbar to a single column for "
        "viewports as wide as 1280px -- the brand title and nav can "
        "overlap on a 1280-wide laptop"
    )


@pytest.mark.parametrize("page", PAGES)
def test_topbar_base_rule_still_two_columns_above_the_breakpoint(page):
    """Sanity check on the other side: above the breakpoint, .topbar is
    still the original two-column layout (brand + nav side by side) -- the
    fix must not have flattened it to one column unconditionally."""
    style = _style_block(_read(page))
    base_start = style.index(".topbar {")
    base_end = _matching_brace_end(style, style.index("{", base_start))
    base_rule = style[base_start : base_end + 1]
    assert "grid-template-columns: minmax(0, 1fr) auto;" in base_rule
