"""Regression tests for the station assignment signpost on Game Admin
(hub_server/static/gameAdmin.html).

When a station is missing or unassigned on the Game Admin "Station Status" panel,
the operator needs to know where to fix it. This page has no assignment controls
(station assignment lives on System Admin per CLAUDE.md). Instead, it points to
System Admin with a clear, clickable navigation element.

These tests verify:
1. The Station Status panel contains a navigation element targeting /systemAdmin#stations
2. It is a real clickable target (not just inert text)
3. gameAdmin.html contains no station assignment controls
4. New i18n keys exist in both inline dictionaries
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _extract_panel(source: str, panel_id: str) -> str:
    """Extract a <section> or <div> panel from source by its id or aria-labelledby.
    Returns the slice from the opening tag to the closing </section> or </div>."""
    # For aria-labelledby, search for the id attribute
    if f'aria-labelledby="{panel_id}"' in source:
        # Find the section start
        marker = f'aria-labelledby="{panel_id}"'
        start = source.rfind("<", 0, source.index(marker))
        # Find the closing section tag
        section_start = source.index(">", start) + 1
        # Count opening/closing tags to find the matching close
        depth = 1
        tag_pattern = re.compile(r"</?section[>\s]")
        for match in tag_pattern.finditer(source[section_start:]):
            if (
                source[section_start + match.start()] == "<"
                and source[section_start + match.start() + 1] == "/"
            ):
                depth -= 1
                if depth == 0:
                    return source[
                        start : section_start + match.end() + 8
                    ]  # +8 for </section>
            else:
                depth += 1
        return source[start : section_start + 500]
    return ""


# ---------------------------------------------------------------------------
# 1. Station Status panel contains navigation to System Admin
# ---------------------------------------------------------------------------


def test_station_status_panel_exists_with_correct_aria_label():
    """The Station Status panel must be findable by its aria-labelledby."""
    source = _read()
    assert 'id="station-title"' in source
    assert 'aria-labelledby="station-title"' in source
    assert "station-title" in source[: source.index("</main>")]


def test_station_status_panel_contains_link_to_station_assignment():
    """The Station Status panel must contain a navigation element targeting
    /systemAdmin#stations (the hash directs to the stations view, not just
    the System Admin homepage)."""
    source = _read()
    # Locate the panel by its aria-labelledby
    panel_start = source.index('aria-labelledby="station-title"')
    panel_section_start = source.rfind("<", 0, panel_start)
    # Find the closing </section> tag after this panel
    section_end = source.index("</section>", panel_start) + len("</section>")
    panel_html = source[panel_section_start:section_end]

    # Must contain the exact deep-link URL with hash
    assert "/systemAdmin#stations" in panel_html


def test_station_status_panel_navigation_is_clickable_target():
    """The navigation to System Admin must be a real clickable element:
    either an <a href> tag or a button with onclick. Not just text."""
    source = _read()
    panel_start = source.index('aria-labelledby="station-title"')
    panel_section_start = source.rfind("<", 0, panel_start)
    section_end = source.index("</section>", panel_start) + len("</section>")
    panel_html = source[panel_section_start:section_end]

    # Verify the link pattern is present as one of the two acceptable forms
    has_href_link = 'href="/systemAdmin#stations"' in panel_html
    has_button_click = (
        "onclick=" in panel_html
        and "/systemAdmin#stations" in panel_html
        and "window.location.href" in panel_html
    )

    assert (
        has_href_link or has_button_click
    ), "Station Status panel must have a clickable link or button targeting /systemAdmin#stations"

    # Extra validation: assert we have an actual opening tag, not just the string
    if has_href_link:
        assert "<a" in panel_html and "/systemAdmin#stations" in panel_html
    if has_button_click:
        assert (
            "<button" in panel_html
            and "onclick" in panel_html
            and "/systemAdmin#stations" in panel_html
        )


# ---------------------------------------------------------------------------
# 2. No station assignment controls on Game Admin
# ---------------------------------------------------------------------------


def test_no_station_number_input_on_game_admin():
    """Station number input fields are a System Admin concern only."""
    source = _read()
    # Should not have any ID or class that suggests station number input
    assert "station-number" not in source


def test_no_node_select_on_game_admin():
    """Node/device selection is a System Admin concern only."""
    source = _read()
    assert "node-select" not in source


def test_no_assign_function_on_game_admin():
    """assignStation() function belongs in System Admin, not Game Admin."""
    source = _read()
    assert "assignStation(" not in source


def test_no_unassign_function_on_game_admin():
    """unassignStation() function belongs in System Admin, not Game Admin."""
    source = _read()
    assert "unassignStation(" not in source


# ---------------------------------------------------------------------------
# 3. i18n: new keys exist in both inline dictionaries
# ---------------------------------------------------------------------------

NEW_I18N_KEYS = [
    "button.go_to_station_setup",  # Will be added to both dictionaries
]


def test_new_i18n_keys_present_in_both_dictionaries():
    """All new i18n keys must exist in both en-US and zh-TW dictionaries."""
    source = _read()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 8000]

    for key in NEW_I18N_KEYS:
        assert (
            f'"{key}":' in en_block
        ), f"{key} missing from en-US dictionary in gameAdmin.html"
        assert (
            f'"{key}":' in zh_block
        ), f"{key} missing from zh-TW dictionary in gameAdmin.html"


def test_i18n_keys_are_symmetric():
    """The en-US and zh-TW dictionaries must have the same set of keys."""
    source = _read()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 8000]

    # Extract all keys from each dictionary
    en_keys = re.findall(r'"([^"]+)":\s*"', en_block)
    zh_keys = re.findall(r'"([^"]+)":\s*"', zh_block)

    # Convert to set for comparison (order doesn't matter)
    en_key_set = set(en_keys)
    zh_key_set = set(zh_keys)

    missing_in_zh = en_key_set - zh_key_set
    missing_in_en = zh_key_set - en_key_set

    assert not missing_in_zh, f"Keys in en-US but missing in zh-TW: {missing_in_zh}"
    assert not missing_in_en, f"Keys in zh-TW but missing in en-US: {missing_in_en}"


# ---------------------------------------------------------------------------
# 4. The signpost text uses i18n (not hardcoded)
# ---------------------------------------------------------------------------


def test_station_status_panel_uses_i18n_not_hardcoded():
    """The navigation button/link must use i18n via data-i18n or t() call,
    not hardcoded English text."""
    source = _read()
    panel_start = source.index('aria-labelledby="station-title"')
    panel_section_start = source.rfind("<", 0, panel_start)
    section_end = source.index("</section>", panel_start) + len("</section>")
    panel_html = source[panel_section_start:section_end]

    # The button/link in the panel should reference a data-i18n attribute
    # or use t() function for the label
    if 'href="/systemAdmin#stations"' in panel_html:
        # For <a> tag, must have data-i18n or similar translation mechanism
        link_start = panel_html.index('href="/systemAdmin#stations"')
        tag_start = panel_html.rfind("<a", 0, link_start)
        tag_end = panel_html.index(">", link_start)
        tag_html = panel_html[tag_start : tag_end + 1]
        # Should have data-i18n or be used with t() somewhere
        assert "data-i18n" in tag_html or "button.go_to_station_setup" in panel_html
