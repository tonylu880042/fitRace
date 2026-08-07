"""Regression tests for the System Admin `.dialog-overlay` vertical
positioning (hub_server/static/systemAdmin.html).

Reported defect: the operator's attention is on the assign form near the
top of the page, but `.dialog-overlay` used `align-items: center`, so any
dialog using it (the unassigned-streams dialog) opened mid-viewport,
disconnected from the control that triggered it. It also had no
`overflow-y`, so a dialog taller than the viewport clipped with no way to
reach the rest of its content.

Fix: anchor the overlay near the top of the viewport (`align-items:
flex-start` with top-heavy padding) and let it scroll
(`overflow-y: auto`) -- the same pattern already used by
edge_node/infrastructure/fastapi/operator.html's `.batch-progress-overlay`
for the identical problem (see that file's comment above the rule, ~line
547).

`.login` (the operator-unlock modal) uses its own separate CSS rule, not
`.dialog-overlay` -- it must be unaffected by this change.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read() -> str:
    return (STATIC_DIR / "systemAdmin.html").read_text(encoding="utf-8")


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


def test_dialog_overlay_anchors_near_top_not_vertically_centered():
    rule = _rule(_style_block(_read()), ".dialog-overlay")
    assert "align-items: flex-start" in rule
    assert "align-items: center" not in rule


def test_dialog_overlay_scrolls_when_taller_than_viewport():
    rule = _rule(_style_block(_read()), ".dialog-overlay")
    assert "overflow-y: auto" in rule


def test_dialog_overlay_still_horizontally_centered():
    rule = _rule(_style_block(_read()), ".dialog-overlay")
    assert "justify-content: center" in rule


def test_dialog_overlay_keeps_background_blur_and_z_index():
    """The overlay's visual identity (dim + blur + stacking order) must
    survive the positioning fix unchanged."""
    rule = _rule(_style_block(_read()), ".dialog-overlay")
    assert "background: rgba(0,0,0,0.74)" in rule
    assert "backdrop-filter: blur(6px)" in rule
    assert "z-index: 11" in rule
    assert "position: fixed" in rule
    assert "inset: 0" in rule


def test_login_modal_overlay_is_a_separate_rule_and_stays_centered():
    """`.login` (the operator-unlock modal) is a distinct selector from
    `.dialog-overlay` and must not be touched by this fix -- it keeps its
    existing vertical centering."""
    rule = _rule(_style_block(_read()), ".login")
    assert "align-items: center" in rule
    assert "align-items: flex-start" not in rule


def test_unassigned_dialog_still_uses_the_shared_dialog_overlay_class():
    """Pin that the unassigned-streams dialog markup still opts into the
    fixed `.dialog-overlay` rule (regression guard against the dialog
    silently switching to some other, unfixed overlay class)."""
    source = _read()
    marker = 'id="unassigned-dialog"'
    idx = source.index(marker)
    tag_start = source.rfind("<div", 0, idx)
    tag_end = source.index(">", idx)
    opening_tag = source[tag_start : tag_end + 1]
    assert 'class="dialog-overlay"' in opening_tag


def test_no_new_breakpoint_or_selector_side_effects():
    """The fix is scoped to `.dialog-overlay`'s own declarations; it must
    not introduce a new media query or duplicate selector block."""
    style = _style_block(_read())
    occurrences = len(re.findall(r"\.dialog-overlay\s*{", style))
    assert occurrences == 1
