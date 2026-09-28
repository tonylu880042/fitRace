"""Pins the compositor-only progress-bar formula added in
5f3ced8 (perf(hub): move dashboard progress bars to compositor-only
transforms) -- until this file, nothing asserted the actual transform
value, so the formula could regress (e.g. dropping the `- 100`, which
would render every bar fully shifted right no matter its real progress)
without failing a single test.

Every fill (`.progress-fill`, `.race-track-fill`, `.team-battle-fill`,
`.sprint-board-progress div`, `.class-effort-fill`, `.class-progress-fill`)
must stay `width: 100%` of its (overflow: hidden) track and be positioned
purely by `transform: translateX(calc((var(--p, 0) - 100) * 1%))` -- never
by an animated `width`. The race-track marker uses the sibling formula
`translateX(calc(var(--p, 0) * 1%))` on its wrapping `.race-track-marker-
lane`, with the marker itself never carrying a `left: <percent>` /
`transition: left`.

CSS `/* ... */` comments are stripped BEFORE any parsing (same approach as
tests/unit/hub/test_dashboard_compositor_only_animations.py), so a comment
that happens to mention the formula text can never satisfy -- or break --
these assertions; only the real, live declaration counts.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _strip_css_comments(css: str) -> str:
    return _BLOCK_COMMENT_RE.sub("", css)


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _extract_style_block(source: str) -> str:
    start = source.index("<style>") + len("<style>")
    end = source.index("</style>", start)
    return source[start:end]


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


def _matching_paren_end(source: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(source):
        char = source[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching closing paren found")


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    paren_open = source.index("(", start)
    paren_end = _matching_paren_end(source, paren_open)
    brace_open = source.index("{", paren_end)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def _flat_rules(css: str):
    """Yields (selector, declarations) for every rule with no nested
    braces in its body -- same parsing style used elsewhere in this suite
    (see test_dashboard_compositor_only_animations.py / test_dashboard_
    class_progress_bar.py). Naturally skips @media/@keyframes wrappers
    while still finding the flat rules nested inside them."""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector = match.group(1).strip()
        if selector.startswith("@"):
            continue
        yield selector, match.group(2)


def _declarations_by_selector(css: str) -> dict:
    """Maps an EXACT selector string (as written, e.g. ".progress-fill")
    to its declaration block text. A selector rule that shares its block
    with others via a comma-separated selector list is stored under the
    comma-joined-and-stripped exact selector text only -- callers of this
    module all use bare, single-class selectors that appear standalone in
    index.html, so this is never ambiguous here."""
    result: dict[str, str] = {}
    for selector, declarations in _flat_rules(css):
        result[selector] = declarations
    return result


def _declaration_value(declarations: str, prop: str) -> str | None:
    """Returns the value text of the LAST `prop: value;` declaration in
    this block (last-wins matches real CSS cascade-within-a-rule), or None
    if the property never appears."""
    matches = list(
        re.finditer(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+);?", declarations)
    )
    if not matches:
        return None
    return matches[-1].group(1).strip()


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


_FILL_SELECTORS = [
    ".progress-fill",
    ".race-track-fill",
    ".team-battle-fill",
    ".sprint-board-progress div",
    ".class-effort-fill",
    ".class-progress-fill",
]

_EXPECTED_FILL_TRANSFORM = "translateX(calc((var(--p, 0) - 100) * 1%))"
_EXPECTED_MARKER_LANE_TRANSFORM = "translateX(calc(var(--p, 0) * 1%))"

_CONTAINER_BY_FILL = {
    ".progress-fill": ".progress-track",
    ".race-track-fill": ".race-track-rail",
    ".team-battle-fill": ".team-battle-meter",
    ".sprint-board-progress div": ".sprint-board-progress",
}


def _css_declarations():
    source = _read_index()
    css = _strip_css_comments(_extract_style_block(source))
    return _declarations_by_selector(css)


def test_every_fill_is_width_100_transform_only_no_width_transition():
    declarations = _css_declarations()
    for selector in _FILL_SELECTORS:
        assert selector in declarations, f"missing rule for {selector!r}"
        block = declarations[selector]

        width = _declaration_value(block, "width")
        assert width == "100%", f"{selector}: width={width!r}, want 100%"

        transform = _declaration_value(block, "transform")
        assert transform is not None, f"{selector}: no transform declared"
        assert (
            _normalize_whitespace(transform) == _EXPECTED_FILL_TRANSFORM
        ), f"{selector}: transform={transform!r}"

        transition = _declaration_value(block, "transition")
        if transition is not None:
            assert (
                "width" not in transition
            ), f"{selector}: transition still mentions width: {transition!r}"


def test_race_track_marker_lane_uses_the_sibling_formula():
    declarations = _css_declarations()
    block = declarations[".race-track-marker-lane"]
    transform = _declaration_value(block, "transform")
    assert transform is not None
    assert _normalize_whitespace(transform) == _EXPECTED_MARKER_LANE_TRANSFORM


def test_race_track_marker_itself_has_no_percent_left_or_left_transition():
    declarations = _css_declarations()
    block = declarations[".race-track-marker"]

    left = _declaration_value(block, "left")
    assert left is not None
    assert "%" not in left, f".race-track-marker: left={left!r} carries a percent"

    transition = _declaration_value(block, "transition")
    if transition is not None:
        assert (
            "left" not in transition
        ), f".race-track-marker: transition still mentions left: {transition!r}"


def test_every_fill_container_clips_with_overflow_hidden():
    declarations = _css_declarations()
    for fill_selector, container_selector in _CONTAINER_BY_FILL.items():
        assert (
            container_selector in declarations
        ), f"missing container rule for {container_selector!r} (fill {fill_selector!r})"
        overflow = _declaration_value(declarations[container_selector], "overflow")
        assert overflow == "hidden", (
            f"{container_selector}: overflow={overflow!r}, want hidden "
            f"(the {fill_selector} technique depends on clipping)"
        )


def test_class_effort_and_progress_track_inline_markup_clips_with_overflow_hidden():
    """.class-effort-fill / .class-progress-fill sit inside markup built
    as an inline-styled string (hub_server/static/index.html, buildClass*
    board HTML), not a CSS rule -- so their containers' overflow: hidden
    has to be checked on the actual inline `style="..."` attribute text
    the page emits, not a stylesheet rule."""
    source = _strip_js_comments(_read_index())

    effort_track_marker = 'class=\\"class-effort-track\\" style=\\"'
    effort_start = source.index(effort_track_marker) + len(effort_track_marker)
    effort_style = source[effort_start : source.index('\\"', effort_start)]
    assert "overflow:hidden" in effort_style.replace(" ", "")

    progress_track_marker = 'class=\\"class-progress-track\\" style=\\"'
    progress_start = source.index(progress_track_marker) + len(progress_track_marker)
    progress_style = source[progress_start : source.index('\\"', progress_start)]
    assert "overflow:hidden" in progress_style.replace(" ", "")


def test_clamp_percent_clamps_both_ends_and_rejects_nan():
    source = _strip_js_comments(_read_index())
    clamp_fn = _extract_function(source, "clampPercent")

    harness = f"""
{clamp_fn}
console.log(JSON.stringify({{
  nan: clampPercent(NaN),
  negative: clampPercent(-5),
  mid: clampPercent(50),
  over: clampPercent(150),
}}));
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["nan"] == 0
    assert result["negative"] == 0
    assert result["mid"] == 50
    assert result["over"] == 100


def test_set_progress_var_writes_the_custom_property_never_style_width():
    source = _strip_js_comments(_read_index())
    clamp_fn = _extract_function(source, "clampPercent")
    set_fn = _extract_function(source, "setProgressVar")
    assert "setProperty" in set_fn  # sanity: real source, not a stub

    harness = f"""
{clamp_fn}
{set_fn}

const calls = [];
const el = {{
  style: {{
    setProperty(name, value) {{ calls.push({{ name, value }}); }},
    set width(value) {{ throw new Error("must never write style.width: " + value); }},
    set left(value) {{ throw new Error("must never write style.left: " + value); }},
  }},
}};

setProgressVar(el, 150);
setProgressVar(el, -5);
setProgressVar(null, 42); // must not throw when el is missing

console.log(JSON.stringify(calls));
"""
    output = _run_node(harness)
    calls = json.loads(output)
    assert calls == [
        {"name": "--p", "value": 100},
        {"name": "--p", "value": 0},
    ]
