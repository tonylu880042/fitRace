"""Owner visual tweak on the idle live telemetry view
(hub_server/static/index.html):

1. The equipment icon on every idle station card must be clearly bigger
   than the "Station N" header text (.idle-station-name, 1.05rem) --
   roughly 1.6-2x that text's font size -- while the header stays on one
   line (it is a flex row, so a taller icon never causes a wrap).
2. The unit inside a 本節最佳 (mini leaderboard) row -- /km, km/h, W --
   must be readable on a projector: about 0.7-0.75x the best value's own
   font size, not the tiny 0.5em .idle-metric-unit already uses for the
   per-station card tiles.

Both must be scoped to the idle view only: class mode's own
.class-station-icon sizing must not change, and the per-station card
tiles (where 0.5em already reads fine against their much larger
clamp()'d number) must not change either -- only the mini leaderboard row
context.

A real rendered-layout assertion (computed pixel height, line count) isn't
practical from this text-extraction harness (no CSS engine, no DOM
layout) -- these tests instead parse the actual CSS rule text and check
the declared values satisfy the requested ratios, which is what changes
under the reviewer's mutation below.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _extract_css_rule(source: str, selector: str) -> str:
    """First `<selector> { ... }` block's declaration text (selector must
    appear literally, e.g. '.idle-best-row .idle-metric-unit')."""
    marker = selector + " {"
    start = source.index(marker)
    body_start = start + len(marker)
    body_end = source.index("}", body_start)
    return source[body_start:body_end]


def _rem_value(css_body: str, prop: str) -> float:
    match = re.search(rf"{prop}\s*:\s*([\d.]+)rem", css_body)
    assert match, f"no {prop} in rem found in: {css_body!r}"
    return float(match.group(1))


def _em_value(css_body: str, prop: str) -> float:
    match = re.search(rf"{prop}\s*:\s*([\d.]+)em\b", css_body)
    assert match, f"no {prop} in em found in: {css_body!r}"
    return float(match.group(1))


# ---------------------------------------------------------------------------
# 1. Idle station icon size, scoped to .idle-station-icon only.
# ---------------------------------------------------------------------------


def test_idle_station_icon_has_exactly_one_scoped_rule():
    source = _read_index()
    # Exactly the base rule -- no stray duplicate that could silently win
    # cascade order and undo the sizing.
    assert source.count(".idle-station-icon {") == 1


def test_idle_station_icon_is_1_6_to_2x_the_header_text_size():
    source = _read_index()
    icon_css = _extract_css_rule(source, ".idle-station-icon")
    name_css = _extract_css_rule(source, ".idle-station-name")

    icon_width = _rem_value(icon_css, "width")
    icon_height = _rem_value(icon_css, "height")
    name_font_size = _rem_value(name_css, "font-size")

    assert icon_width == icon_height, "icon must stay square"
    ratio = icon_width / name_font_size
    assert 1.6 <= ratio <= 2.0, f"icon/header ratio {ratio} outside 1.6-2.0"


def test_idle_station_icon_stays_a_flex_none_row_item_so_header_never_wraps():
    source = _read_index()
    header_css = _extract_css_rule(source, ".idle-station-header")
    icon_css = _extract_css_rule(source, ".idle-station-icon")
    # display: flex (row, the default) with no wrap property, and the icon
    # itself refusing to grow/shrink -- this is what keeps a taller icon
    # from ever pushing the header onto a second line.
    assert "display: flex" in header_css
    assert "flex-wrap" not in header_css
    assert "flex: none" in icon_css


def test_class_mode_station_icon_size_is_unchanged_and_separate():
    # The owner explicitly required class mode's own icon sizing (a
    # 24-station board has no room to spare) to be untouched -- it must
    # keep its own distinct selector/rule, not share .idle-station-icon's.
    source = _read_index()
    class_icon_css = _extract_css_rule(source, ".class-station-icon")
    # .class-station-icon is declared in px, not rem -- if the owner's
    # scoped idle change ever leaked into this rule, .class-station-icon
    # would no longer declare its own 18px width.
    assert "18px" in class_icon_css


# ---------------------------------------------------------------------------
# 2. 本節最佳 row unit size, scoped to .idle-best-row .idle-metric-unit.
# ---------------------------------------------------------------------------


def test_best_row_unit_has_a_scoped_override():
    source = _read_index()
    assert ".idle-best-row .idle-metric-unit {" in source


def test_best_row_unit_is_0_7_to_0_75x_the_best_value_size():
    source = _read_index()
    override_css = _extract_css_rule(source, ".idle-best-row .idle-metric-unit")
    ratio = _em_value(override_css, "font-size")
    assert 0.70 <= ratio <= 0.75, f"best-row unit ratio {ratio} outside 0.70-0.75"


def test_per_station_card_unit_size_is_unchanged():
    # The base .idle-metric-unit rule (used by the per-station card tiles)
    # must stay at its original, smaller size -- only the best-row context
    # gets the bump.
    source = _read_index()
    base_css = _extract_css_rule(source, ".idle-metric-unit")
    ratio = _em_value(base_css, "font-size")
    assert ratio == 0.5
