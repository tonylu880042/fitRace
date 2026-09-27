"""Progress bars and the race-track marker (`hub_server/static/index.html`)
used to animate `width` (bars) and `left` (marker) via a CSS `transition`,
one per leaderboard row, at ~4Hz telemetry. That forces a Layout on every
animation frame for every row -- on the Raspberry Pi driving the projector
this is the dominant per-tick cost once a venue reaches a handful of
stations. The fix moves all of that motion onto `transform` (compositor-
only): every fill/marker now stays a fixed size and is positioned via
`transform: translateX(...)`, driven by a `--p` CSS custom property that
JS sets (never `.style.width` / `.style.left`).

Per CLAUDE.md's known assertion traps, this module strips CSS `/* ... */`
and JS `//` comments BEFORE any parsing, so a comment that happens to
mention "width" or "left" (explaining why a nearby rule avoids it) can
never satisfy -- or accidentally fail -- these assertions.

Three things are pinned here:

  1. No `transition` declaration anywhere in the page (CSS rules or an
     inline `style="..."` string built by JS) lists `width` or `left` as
     the property being transitioned.
  2. No JS assigns `.style.width` anywhere in the page, and the only
     `.style.left` assignment left is the one-shot confetti piece
     placement (a random starting position, not a telemetry-driven
     progress value, and never transitioned) -- everything else drives
     its progress value through `setProgressVar`, which writes the `--p`
     custom property instead.
  3. The real `clampPercent`/`setProgressVar` helpers, extracted from the
     page and executed under `node -e`, behave correctly on the numbers
     that matter: an ordinary in-range value (37.5), and clamping at both
     ends (130 -> 100, -5 -> 0) -- proving the numeric mapping itself,
     not merely that some text mentioning "width"/"left" is gone.
"""

import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(code: str) -> str:
    """Strips CSS block comments and JS-style whole-line `//` comments.
    CSS never uses `//`, so applying both patterns to the whole file
    (style block + script block together) is safe and matches the
    house style used throughout tests/unit/hub/ (see e.g.
    test_dashboard_compositor_only_animations.py and
    test_dashboard_leaderboard_card_cache.py's _strip_js_comments)."""
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _matching_brace_end(source: str, open_idx: int) -> int:
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
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 1. No `transition` anywhere (CSS or an inline style string) lists width
#    or left.
# ---------------------------------------------------------------------------


def _split_top_level_commas(value: str):
    """Splits a `transition` value on commas that are NOT inside a
    function call's parentheses (e.g. `cubic-bezier(0.1, 0.8, 0.25, 1)`),
    so a multi-property transition like
    `transform 0.38s cubic-bezier(0.2, 0.9, 0.22, 1), background 0.25s ease`
    yields two properties, not five."""
    parts = []
    depth = 0
    current = []
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _transitioned_properties(source: str):
    """Yields every individual CSS property name found in any `transition:
    ...;` declaration across the whole page (style block AND any inline
    `style="..."` string a JS template/concatenation builds), after
    stripping comments. `transition: none ...` (the reduced-motion
    override) contributes no property names, which is correct -- it
    disables motion rather than describing a new animated property."""
    stripped = _strip_comments(source)
    for match in re.finditer(r"transition\s*:\s*([^;\"]+)[;\"]", stripped):
        value = match.group(1)
        for part in _split_top_level_commas(value):
            name_match = re.match(r"([a-zA-Z-]+)", part)
            if name_match:
                yield name_match.group(1).lower()


def test_no_transition_animates_width_or_left():
    props = list(_transitioned_properties(_read_index()))
    assert props, "expected at least one transition declaration in index.html"
    offenders = [p for p in props if p in ("width", "left")]
    assert not offenders, (
        "a transition still animates width/left -- this forces layout on "
        f"every telemetry tick for every row; offending properties: {offenders}"
    )


def test_a_comment_mentioning_transition_width_cannot_satisfy_this_suite():
    """Guards the classic assertion trap: a rule whose real `transition:
    width ...` was deleted, leaving only a comment that happens to
    contain the same substring, must read as having zero transitioned
    properties -- not accidentally pass (nothing to fail) NOR accidentally
    fail (a genuine `transition: width` elsewhere must still be caught)."""
    css_with_only_a_comment = """
        /* this used to be transition: width 0.45s ease; */
        .fake-fill { transition: opacity 0.2s ease; }
        """
    props = list(_transitioned_properties(css_with_only_a_comment))
    assert props == ["opacity"]

    css_with_a_real_offender = """
        .fake-fill { transition: width 0.45s ease; }
        """
    offending_props = list(_transitioned_properties(css_with_a_real_offender))
    assert offending_props == ["width"]


# ---------------------------------------------------------------------------
# 2. No JS assigns .style.width anywhere; the only .style.left assignment
#    left is the one-shot confetti piece placement.
# ---------------------------------------------------------------------------


def _extract_script_body(source: str) -> str:
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return source[start:end]


def test_no_javascript_ever_assigns_style_width():
    js = _strip_comments(_extract_script_body(_read_index()))
    offenders = re.findall(r"[\w.$]+\.style\.width\s*=", js)
    assert not offenders, (
        "a .style.width assignment forces layout on every telemetry tick; "
        f"found: {offenders} -- progress must be driven through "
        "setProgressVar()'s --p custom property instead"
    )


def test_only_the_confetti_piece_assigns_style_left():
    js = _strip_comments(_extract_script_body(_read_index()))
    offenders = re.findall(r"([\w.$]+)\.style\.left\s*=", js)
    assert offenders == ["piece"], (
        "the only acceptable .style.left writer is the one-shot confetti "
        f"piece placement (a random start position, never transitioned); "
        f"found: {offenders}"
    )


def test_confetti_left_assignment_is_not_progress_driven():
    """The surviving `piece.style.left` write must stay a one-shot random
    position (Math.random()), never a telemetry/progress value -- if a
    future edit repurposed this line for a progress bar it would
    reintroduce the exact layout thrash this module exists to prevent."""
    js = _strip_comments(_extract_script_body(_read_index()))
    match = re.search(r"piece\.style\.left\s*=\s*`([^`]*)`", js)
    assert match, "expected piece.style.left = `...` (a template literal)"
    assert "Math.random()" in match.group(1)
    assert "progress" not in match.group(1).lower()


# ---------------------------------------------------------------------------
# 3. The real clampPercent/setProgressVar helpers, executed under node,
#    behave correctly on the numbers that matter.
# ---------------------------------------------------------------------------


def _extract_helpers() -> str:
    source = _read_index()
    return (
        _strip_comments(_extract_function(source, "clampPercent"))
        + "\n"
        + _strip_comments(_extract_function(source, "setProgressVar"))
    )


def _run_clamp_percent(value) -> float:
    script = _extract_helpers() + f"\nconsole.log(clampPercent({value}));"
    return float(_run_node(script))


def test_clamp_percent_passes_through_an_ordinary_in_range_value():
    assert _run_clamp_percent(37.5) == 37.5


def test_clamp_percent_clamps_above_100_down_to_100():
    assert _run_clamp_percent(130) == 100


def test_clamp_percent_clamps_below_0_up_to_0():
    assert _run_clamp_percent(-5) == 0


def test_clamp_percent_treats_non_finite_input_as_zero():
    # clampPercent rejects non-finite input outright (Number.isFinite),
    # rather than trying to clamp Infinity itself -- so +Infinity reads
    # as 0, same as NaN and -Infinity, not 100.
    assert _run_clamp_percent("NaN") == 0
    assert _run_clamp_percent("Infinity") == 0
    assert _run_clamp_percent("-Infinity") == 0


def _run_set_progress_var(value) -> dict:
    """Drives the real setProgressVar against a fake element whose style
    object records every setProperty(name, value) call, so this proves
    setProgressVar (a) calls setProperty (never assigns .style.width or
    .style.left) and (b) passes the CORRECTLY CLAMPED number through --
    not the raw input."""
    script = _extract_helpers() + """
const calls = [];
const el = {
  style: {
    setProperty(name, v) { calls.push([name, v]); },
  },
};
""" + f"setProgressVar(el, {value});" + "\nconsole.log(JSON.stringify(calls));"
    import json

    calls = json.loads(_run_node(script))
    assert len(calls) == 1, f"expected exactly one setProperty call, got {calls}"
    name, written_value = calls[0]
    assert (
        name == "--p"
    ), f"setProgressVar must write the --p custom property, wrote {name!r}"
    return float(written_value)


def test_set_progress_var_writes_an_ordinary_in_range_value():
    assert _run_set_progress_var(37.5) == 37.5


def test_set_progress_var_clamps_130_down_to_100():
    assert _run_set_progress_var(130) == 100


def test_set_progress_var_clamps_negative_5_up_to_0():
    assert _run_set_progress_var(-5) == 0


def test_set_progress_var_is_a_no_op_against_a_null_element():
    """Every call site guards `if (refs.progressFill) { setProgressVar(...) }`
    -- but setProgressVar itself must also tolerate a null/undefined el
    without throwing, since several call sites (e.g. captureClassBoardRefs
    against a container with no querySelectorAll) can legitimately produce
    one."""
    script = _extract_helpers() + "\nsetProgressVar(null, 50);\nconsole.log('ok');"
    assert _run_node(script) == "ok"
