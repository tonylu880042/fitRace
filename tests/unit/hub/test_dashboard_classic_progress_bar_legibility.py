"""The CLASSIC leaderboard's per-row progress bar was unreadable from the
floor of a gym.

1. HEIGHT (`hub_server/static/index.html`). At the low-density tiers the row is deliberately huge -- on a
   1920x1080 projector with two stations racing (`race-board--xl`) the
   athlete name renders at 38px and the progress percentage at 51px --
   but the bar under it stayed 16px, so it read as a hairline rule rather
   than a gauge. Worse, `.race-board--xl .progress-track` was declared
   TWICE: a flat `height: 16px` grouped with `.sprint-board-progress`,
   and, several hundred lines later, `height: clamp(4px, calc(3.333vh -
   20px), 16px)`. The later one won, so any edit to the first was a
   no-op on screen. The duplicate is now collapsed: the grouped rule
   keeps only `.sprint-board-progress` (out of scope, untouched) and the
   single viewport-relative clamp is the one authoritative height per
   tier.

   Raising only the clamp's MAXIMUM would also have been a no-op at the
   measured viewport: the old `calc(3.333vh - 20px)` evaluates to exactly
   16px at 1080px tall, i.e. the bar was already pinned at its cap, so a
   bigger cap alone changes nothing anyone can see. The whole linear term
   is therefore rescaled by the same factor as the cap (xl 16 -> 28px,
   lg 12 -> 20px) so the bar actually reaches the new cap at 1080px while
   still shrinking on short viewports. Hence the tests below assert the
   RESOLVED height at 1080px, not merely the literal cap.

   The base `.progress-track` stays 8px: dense boards (5+ stations) pack
   many rows onto a non-scrolling projector and cannot spend the space.

Scope guard: `.race-track-rail`, `.sprint-board-progress` and
`.class-progress-track` are different bars on different views and are
asserted UNCHANGED here, because the duplicate-collapsing edit above
touches a selector list that `.sprint-board-progress` shares.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

# The projector the venue actually uses, and the viewport every height in
# this module is resolved against.
VIEWPORT_HEIGHT_PX = 1080.0
VH = VIEWPORT_HEIGHT_PX / 100.0


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _matching_brace_end(source: str, open_idx: int) -> int:
    """Index just past the `}` closing the `{` at `open_idx`."""
    depth = 0
    i = open_idx
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise AssertionError("unbalanced braces in stylesheet")


def _rule_bodies(css: str, selector: str) -> list[str]:
    """Bodies of every top-level rule whose selector list contains
    `selector` as a whole comma-separated entry."""
    bodies = []
    for match in re.finditer(re.escape(selector) + r"\s*[,{]", css):
        # Walk back to the start of the selector list: whichever of the
        # previous rule's `}`, an enclosing at-rule's `{` or a preceding
        # comment's `*/` sits closest. Comments matter -- several of these
        # rules are documented in place, and a prelude cut mid-comment
        # would silently drop the rule from this scan.
        prelude_start = (
            max(
                css.rfind("}", 0, match.start()),
                css.rfind("{", 0, match.start()),
                css.rfind("*/", 0, match.start()) + 1,  # past both chars
            )
            + 1
        )
        open_idx = css.find("{", match.start())
        if open_idx < 0:
            continue
        prelude = re.sub(r"/\*.*?\*/", "", css[prelude_start:open_idx], flags=re.DOTALL)
        entries = [part.strip() for part in prelude.split(",")]
        if selector not in entries:
            continue
        bodies.append(css[open_idx + 1 : _matching_brace_end(css, open_idx) - 1])
    return bodies


def _declared(body: str, prop: str) -> list[str]:
    """Every value declared for `prop` in a rule body."""
    return [
        m.group(1).strip()
        for m in re.finditer(r"(?:^|;)\s*" + re.escape(prop) + r"\s*:([^;}]*)", body)
    ]


def _px(expr: str) -> float:
    """Resolve a px / vh / calc() / clamp() length at VIEWPORT_HEIGHT_PX."""
    expr = expr.replace("!important", "").strip()
    clamp = re.fullmatch(r"clamp\((.*)\)", expr, re.DOTALL)
    if clamp:
        parts = _split_args(clamp.group(1))
        assert len(parts) == 3, f"clamp() needs 3 args: {expr}"
        low, preferred, high = (_px(p) for p in parts)
        return min(max(low, preferred), high)
    calc = re.fullmatch(r"calc\((.*)\)", expr, re.DOTALL)
    if calc:
        expr = calc.group(1)
    total = 0.0
    sign = 1.0
    for token in re.findall(r"[+-]|[0-9.]+(?:px|vh)?", expr):
        if token == "+":
            sign = 1.0
        elif token == "-":
            sign = -1.0
        elif token.endswith("vh"):
            total += sign * float(token[:-2]) * VH
        elif token.endswith("px"):
            total += sign * float(token[:-2])
        else:
            total += sign * float(token)
    return total


def _split_args(arg_text: str) -> list[str]:
    parts, depth, current = [], 0, ""
    for char in arg_text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)
    return [p.strip() for p in parts]


def _keyframes_body(css: str, name: str) -> str:
    match = re.search(r"@keyframes\s+" + re.escape(name) + r"\s*\{", css)
    assert match, f"@keyframes {name} is not defined"
    open_idx = match.end() - 1
    return css[open_idx + 1 : _matching_brace_end(css, open_idx) - 1]


# ---------------------------------------------------------------------------
# 1. Height: one authoritative declaration per tier, resolving big at 1080px.
# ---------------------------------------------------------------------------


def test_base_progress_track_stays_eight_px_for_dense_boards():
    bodies = _rule_bodies(_read_index(), ".progress-track")
    heights = [h for body in bodies for h in _declared(body, "height")]
    assert heights == ["8px"], (
        "the untiered .progress-track must stay 8px -- dense boards pack many "
        f"rows onto a non-scrolling projector; got {heights}"
    )


def test_xl_tier_progress_track_height_is_declared_exactly_once():
    bodies = _rule_bodies(_read_index(), ".race-board--xl .progress-track")
    heights = [h for body in bodies for h in _declared(body, "height")]
    assert len(heights) == 1, (
        "the xl classic progress bar height must be declared exactly once; a "
        "second, later declaration silently overrides the first and makes any "
        f"edit to it a no-op. Got {heights}"
    )


def test_lg_tier_progress_track_height_is_declared_exactly_once():
    bodies = _rule_bodies(_read_index(), ".race-board--lg .progress-track")
    heights = [h for body in bodies for h in _declared(body, "height")]
    assert len(heights) == 1, (
        "the lg classic progress bar height must be declared exactly once; "
        f"got {heights}"
    )


def test_xl_tier_progress_track_caps_at_28px():
    bodies = _rule_bodies(_read_index(), ".race-board--xl .progress-track")
    heights = [h for body in bodies for h in _declared(body, "height")]
    assert heights, "xl tier declares no .progress-track height"
    value = heights[-1]
    clamp = re.fullmatch(r"clamp\((.*)\)", value.strip(), re.DOTALL)
    assert clamp, f"xl tier height must stay a clamp(), got {value!r}"
    low, _preferred, high = _split_args(clamp.group(1))
    assert _px(high) == 28.0, f"xl tier bar cap must be 28px, got {high!r}"
    assert _px(low) == 4.0, f"xl tier bar floor must stay 4px, got {low!r}"


def test_lg_tier_progress_track_caps_at_20px():
    bodies = _rule_bodies(_read_index(), ".race-board--lg .progress-track")
    heights = [h for body in bodies for h in _declared(body, "height")]
    assert heights, "lg tier declares no .progress-track height"
    value = heights[-1]
    clamp = re.fullmatch(r"clamp\((.*)\)", value.strip(), re.DOTALL)
    assert clamp, f"lg tier height must stay a clamp(), got {value!r}"
    low, _preferred, high = _split_args(clamp.group(1))
    assert _px(high) == 20.0, f"lg tier bar cap must be 20px, got {high!r}"
    assert _px(low) == 4.0, f"lg tier bar floor must stay 4px, got {low!r}"


def test_xl_tier_bar_actually_reaches_28px_on_the_venue_projector():
    """Raising the cap while leaving the linear term alone would leave the
    bar at its old 16px on the 1080px projector the venue measured, since
    the old term already saturated there. Resolve it."""
    bodies = _rule_bodies(_read_index(), ".race-board--xl .progress-track")
    value = [h for body in bodies for h in _declared(body, "height")][-1]
    assert abs(_px(value) - 28.0) < 0.05, (
        "at a 1080px viewport the xl classic bar must resolve to its full "
        f"28px, got {_px(value)}px from {value!r}"
    )


def test_lg_tier_bar_actually_reaches_20px_on_the_venue_projector():
    bodies = _rule_bodies(_read_index(), ".race-board--lg .progress-track")
    value = [h for body in bodies for h in _declared(body, "height")][-1]
    assert abs(_px(value) - 20.0) < 0.05, (
        "at a 1080px viewport the lg classic bar must resolve to its full "
        f"20px, got {_px(value)}px from {value!r}"
    )


def test_tiered_bars_still_shrink_on_a_short_viewport():
    """The clamp exists so a 768px-tall projector does not get a bar taller
    than its own rows. Rescaling the linear term must not flatten it into
    a constant."""
    global VH
    css = _read_index()
    original = VH
    try:
        VH = 768.0 / 100.0
        for selector, cap in (
            (".race-board--xl .progress-track", 28.0),
            (".race-board--lg .progress-track", 20.0),
        ):
            bodies = _rule_bodies(css, selector)
            value = [h for body in bodies for h in _declared(body, "height")][-1]
            resolved = _px(value)
            assert 4.0 <= resolved < cap, (
                f"{selector} must shrink below its {cap}px cap on a 768px "
                f"viewport, got {resolved}px"
            )
    finally:
        VH = original


# ---------------------------------------------------------------------------
# 2. Scope guard: the other three bars are not ours to resize.
# ---------------------------------------------------------------------------


def test_sprint_board_progress_heights_are_untouched():
    css = _read_index()
    for selector, expected in (
        (".race-board--xl .sprint-board-progress", "16px"),
        (".race-board--lg .sprint-board-progress", "12px"),
    ):
        heights = [
            h for body in _rule_bodies(css, selector) for h in _declared(body, "height")
        ]
        assert heights == [expected], (
            f"{selector} is out of scope for the classic-bar fix and must stay "
            f"{expected}; got {heights}"
        )


def test_race_track_rail_and_class_progress_track_are_untouched():
    css = _read_index()
    assert (
        "height: clamp(30px, calc(11.667vh - 54px), 72px);" in css
    ), ".race-board--xl .race-track-rail is out of scope and must be unchanged"
    assert (
        "height: clamp(22px, calc(10.833vh - 54px), 63px);" in css
    ), ".race-board--lg .race-track-rail is out of scope and must be unchanged"
    assert (
        ".class-board--ultra .class-progress-track { height: 10px !important; }" in css
    )
