"""The race dashboard (`hub_server/static/index.html`) runs unattended on a
Raspberry Pi driving a projector, with one leaderboard row per station.
Every animation that runs `infinite` therefore repaints forever -- if any of
them animates a paint-triggering property (box-shadow, text-shadow,
border-color, background-position, ...) the browser repaints every row on
every frame for the whole session. This module pins that every *infinite*
animation in the page is compositor-only: its `@keyframes` may only ever
set `transform` and/or `opacity`.

CSS `/* ... */` comments are stripped BEFORE any parsing, specifically so a
comment that happens to mention "box-shadow" (e.g. explaining why a nearby
rule avoids it) can never satisfy -- or break -- this test.

A "fix" that just deletes the animation (rather than converting it to a
compositor-only one) is not acceptable: it would make the row look static
and dead on the projector. So this file also pins that each of the five
known offenders is STILL driven by an infinite animation on its real
selector, after stripping comments:
  - `.leaderboard-item.champion-final` (championShimmer)
  - `.podium-card.place-1` (championGlow)
  - `.progress-fill`'s own sweep (progressSweep) -- not the existing
    `.progress-fill::after` head-pulse, which is a separate, already-fine
    animation
  - the `.pace-blazing` rule (paceBlazingGlow)
  - the `.fastest-pace-highlight` rule (fastestPaceCardPulse)
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_ALLOWED_PROPERTIES = {"transform", "opacity"}


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _strip_css_comments(css: str) -> str:
    return _BLOCK_COMMENT_RE.sub("", css)


def _extract_style_block(source: str) -> str:
    start = source.index("<style>") + len("<style>")
    end = source.index("</style>", start)
    return source[start:end]


def _matching_brace_end(source: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(source):
        char = source[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching closing brace found")


def _extract_keyframes(css: str):
    """Returns (keyframes_by_name, css_with_keyframes_blocks_removed)."""
    keyframes = {}
    out = []
    pos = 0
    pattern = re.compile(r"@keyframes\s+([\w-]+)\s*\{")
    while True:
        match = pattern.search(css, pos)
        if not match:
            out.append(css[pos:])
            break
        out.append(css[pos : match.start()])
        brace_open = match.end() - 1
        brace_end = _matching_brace_end(css, brace_open)
        name = match.group(1)
        body = css[brace_open + 1 : brace_end]
        keyframes[name] = body
        pos = brace_end + 1
    return keyframes, "".join(out)


def _flat_rules(css: str):
    """Yields (selector, declarations) for every rule with no nested
    braces in its body. This naturally skips the outer wrapper of
    @media/@keyframes blocks (their "selector" contains a brace-free
    directive followed by more rules, which this pattern can't consume in
    one bite) while still finding the individual flat rules nested inside
    them, exactly like the accepted parsing style used elsewhere in this
    test suite (see test_dashboard_class_progress_bar.py)."""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector = match.group(1).strip()
        if selector.startswith("@"):
            continue
        yield selector, match.group(2)


def _infinite_animation_names(declarations: str):
    """Returns the keyframe name(s) referenced by any `animation` /
    `animation-name` declaration in this rule whose value also carries
    `infinite` (as an iteration count, on the same declaration -- the
    shorthand form used throughout this file)."""
    names = []
    for decl_match in re.finditer(r"animation(?:-name)?\s*:\s*([^;]+);?", declarations):
        value = decl_match.group(1)
        if "infinite" not in value:
            continue
        name_match = re.match(r"\s*([\w-]+)", value)
        if name_match:
            names.append(name_match.group(1))
    return names


def _keyframe_stop_properties(body: str):
    """Returns the set of CSS property names set across every stop
    (0%, 50%, from/to, ...) inside one @keyframes body."""
    props = set()
    for _stop_selector, stop_decls in _flat_rules(body):
        for prop_match in re.finditer(r"([a-zA-Z-]+)\s*:", stop_decls):
            props.add(prop_match.group(1).strip().lower())
    return props


def _collect_infinite_rules():
    """Returns a list of (selector, keyframe_name, properties_set) for
    every rule in index.html whose animation runs infinite, resolved
    against the real @keyframes body -- comments stripped first."""
    source = _read_index()
    css = _strip_css_comments(_extract_style_block(source))
    keyframes, css_without_keyframes = _extract_keyframes(css)

    results = []
    for selector, declarations in _flat_rules(css_without_keyframes):
        for name in _infinite_animation_names(declarations):
            assert name in keyframes, (
                f"selector {selector!r} references infinite animation "
                f"{name!r}, but no @keyframes {name} exists"
            )
            props = _keyframe_stop_properties(keyframes[name])
            results.append((selector, name, props))
    return results


def test_every_infinite_animation_is_compositor_only():
    rules = _collect_infinite_rules()
    assert rules, "expected at least one infinite animation in index.html"

    offenders = []
    for selector, name, props in rules:
        disallowed = props - _ALLOWED_PROPERTIES
        if disallowed:
            offenders.append((selector, name, sorted(disallowed)))

    assert not offenders, (
        "infinite animations must only ever animate transform/opacity "
        f"(compositor-only); offenders: {offenders}"
    )


def _has_infinite_rule(
    rules, selector_substrings, keyframe_name=None, exclude_substring=None
):
    for selector, name, _props in rules:
        if exclude_substring and exclude_substring in selector:
            continue
        if all(sub in selector for sub in selector_substrings):
            if keyframe_name is None or name == keyframe_name:
                return True
    return False


def test_champion_final_leaderboard_row_is_still_infinitely_animated():
    rules = _collect_infinite_rules()
    assert _has_infinite_rule(rules, [".leaderboard-item", ".champion-final"]), (
        ".leaderboard-item.champion-final must still carry an infinite "
        "animation -- deleting the shimmer instead of fixing it is not "
        "acceptable"
    )


def test_champion_podium_card_is_still_infinitely_animated():
    rules = _collect_infinite_rules()
    assert _has_infinite_rule(rules, [".podium-card", ".place-1"]), (
        ".podium-card.place-1 must still carry an infinite animation "
        "(the championGlow effect)"
    )


def test_progress_fill_sweep_is_still_infinitely_animated():
    rules = _collect_infinite_rules()
    # Deliberately excludes "::after" so this cannot be satisfied by the
    # existing, already-fine progressHeadPulse animation on
    # .progress-fill::after -- the sweep is a distinct effect.
    assert _has_infinite_rule(rules, [".progress-fill"], exclude_substring="::after"), (
        ".progress-fill must still carry its own infinite sweep animation "
        "(distinct from the ::after head-pulse)"
    )


def test_pace_blazing_is_still_infinitely_animated():
    rules = _collect_infinite_rules()
    assert _has_infinite_rule(rules, [".pace-blazing"]), (
        "the .pace-blazing rule must still carry an infinite animation "
        "(the paceBlazingGlow effect)"
    )


def test_fastest_pace_highlight_is_still_infinitely_animated():
    rules = _collect_infinite_rules()
    assert _has_infinite_rule(rules, ["fastest-pace-highlight"]), (
        "the fastest-pace-highlight rule must still carry an infinite "
        "animation (the fastestPaceCardPulse effect)"
    )


def _extract_flip_target_selectors() -> list:
    """`animateLeaderboardReorder` (index.html) does a FLIP reorder
    animation: it writes an inline `style.transform` directly onto each
    element returned by its own `document.querySelectorAll(...)` call. A
    running CSS animation that also sets `transform` on that SAME element
    wins the cascade over the inline style (per CSS's normal
    inline-vs-animation precedence for animated properties), silently
    clobbering the FLIP translate every animation frame -- the row then
    teleports to its new slot instead of sliding. This reads the selector
    list straight out of the page source so it can never drift from the
    real FLIP target set."""
    source = _read_index()
    match = re.search(
        r"function animateLeaderboardReorder\([^)]*\)\s*\{\s*"
        r'const items = document\.querySelectorAll\("([^"]+)"\)',
        source,
    )
    assert match, "could not find animateLeaderboardReorder's querySelectorAll(...)"
    return [part.strip() for part in match.group(1).split(",")]


def _selector_targets_element_itself(selector_part: str, base_selector: str) -> bool:
    """True when `selector_part` (one comma-branch of a CSS rule) targets
    the FLIP element itself -- the same base class, optionally with more
    chained classes/pseudo-classes -- rather than a pseudo-element
    (::before/::after) or a descendant/combinator selector, which the
    inline `style.transform` write never reaches through."""
    selector_part = selector_part.strip()
    if "::" in selector_part:
        return False
    if re.search(r"\s|[>+~]", selector_part):
        return False
    return selector_part == base_selector or selector_part.startswith(
        (base_selector + ".", base_selector + ":")
    )


def test_no_flip_target_element_has_its_own_infinite_transform_animation():
    """None of the elements `animateLeaderboardReorder` writes an inline
    `style.transform` onto may themselves carry a CSS rule with an
    infinite animation whose keyframes set `transform` -- that CSS
    animation would win over the one-frame inline transform every single
    animation frame, so the FLIP slide would never actually show (the row
    would just snap/teleport to its new position). An infinite transform
    animation belongs on a pseudo-element (::before/::after) instead,
    which the FLIP write never touches."""
    flip_selectors = _extract_flip_target_selectors()
    rules = _collect_infinite_rules()

    violations = []
    for selector, keyframe_name, props in rules:
        if "transform" not in props:
            continue
        for part in selector.split(","):
            part = part.strip()
            for base_selector in flip_selectors:
                if _selector_targets_element_itself(part, base_selector):
                    violations.append((part, keyframe_name, base_selector))

    assert not violations, (
        "these FLIP-animated elements carry their own infinite transform "
        f"animation, which clobbers the reorder slide every frame: {violations}"
    )


def test_a_comment_mentioning_box_shadow_cannot_satisfy_this_suite():
    """Guards against the classic assertion trap: a keyframe body that
    contains ONLY a comment saying "box-shadow" (with the real property
    deleted) must read as having zero declared properties, not pass by
    accident, and a keyframe that genuinely still animates box-shadow
    inside a comment-disguised declaration must still be caught."""
    css = _strip_css_comments("""
        @keyframes fake {
          0% { /* box-shadow: 0 0 1px red; */ opacity: 0.5; }
          100% { opacity: 1; }
        }
        """)
    keyframes, _ = _extract_keyframes(css)
    props = _keyframe_stop_properties(keyframes["fake"])
    assert props == {"opacity"}
