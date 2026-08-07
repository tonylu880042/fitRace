"""Regression tests: the Station Status panel on Game Admin
(hub_server/static/gameAdmin.html) had no explanatory subtitle and mixed
hardcoded English into renderStations() -- "Station ", "unbound",
"No telemetry stream", "No athlete", "Health: ", and "Unassigned telemetry
stream. Configure it in System Admin." -- while the rest of the page is
localized via the inline dictionaries/t() mechanism (CLAUDE.md's i18n rule:
"never hardcode zh/en strings in static pages").

This adds:
  - a one-line subtitle under the panel title (text.station_status_subtitle)
  - i18n routing for every literal above (reusing text.station where it
    already exists, adding text.unbound / text.no_telemetry_stream /
    text.no_athlete / label.health / text.unassigned_telemetry)
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

# Comment-stripping so a comment mentioning e.g. "No athlete" can't satisfy
# the "no longer hardcoded" assertion in place of a real fix (CLAUDE.md's
# named "comment satisfies the assertion" trap).
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _script() -> str:
    source = _read()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _render_stations_region() -> str:
    """The exact slice of renderStations(), from a comment-stripped script --
    so a comment can't hide the old hardcoded literals or fake the new t()
    calls."""
    script = _script()
    start = script.index("function renderStations(")
    end = script.index("async function configureRace", start)
    return script[start:end]


NEW_KEYS = {
    "text.unbound": ("unbound", "未綁定"),
    "text.no_telemetry_stream": ("No telemetry stream", "無遙測串流"),
    "text.no_athlete": ("No athlete", "尚無選手"),
    "label.health": ("Health:", "狀態:"),
    "text.unassigned_telemetry": (
        "Unassigned telemetry stream. Configure it in System Admin.",
        "未指派遙測串流,請至系統管理設定。",
    ),
    "text.station_status_subtitle": (
        "Pre-race checklist: confirm every station has a live machine and a "
        "registered athlete before you start.",
        "賽前檢查清單:確認每個站位都已連上機台並完成選手報名,再開始比賽。",
    ),
}


# ---------------------------------------------------------------------------
# 1. renderStations() literals are gone and routed through t()
# ---------------------------------------------------------------------------


def test_render_stations_no_longer_hardcodes_english():
    body = _render_stations_region()
    assert '"unbound"' not in body
    assert '"No telemetry stream"' not in body
    assert '"No athlete"' not in body
    assert ">Station ${" not in body
    assert "Health: ${" not in body
    assert "Unassigned telemetry stream. Configure it in System Admin." not in body


def test_render_stations_routes_literals_through_t():
    body = _render_stations_region()
    assert 't("text.station")' in body
    assert 't("text.unbound")' in body
    assert 't("text.no_telemetry_stream")' in body
    assert 't("text.no_athlete")' in body
    assert 't("label.health")' in body
    assert 't("text.unassigned_telemetry")' in body


# ---------------------------------------------------------------------------
# 2. Health status values (CSS class names) are untouched
# ---------------------------------------------------------------------------


def test_health_pill_css_classes_still_use_raw_health_value():
    """health.health (online/stale/missing) drives the CSS class name and
    must stay untranslated -- only the visible "Health: " label text
    changes."""
    body = _render_stations_region()
    assert 'class="health-pill ${escapeHtml(health.health || "missing")}"' in body


def test_health_label_value_from_server_is_not_wrapped_in_t():
    """health.label itself is server-supplied text; only the "Health: "
    prefix goes through t()."""
    body = _render_stations_region()
    assert "escapeHtml(health.label || health.health" in body


# ---------------------------------------------------------------------------
# 3. Device-name fallback chain is unchanged apart from the final fallback
# ---------------------------------------------------------------------------


def test_device_name_fallback_chain_order_preserved():
    body = _render_stations_region()
    assert (
        'station.node_display_name || station.equipment_id || station.node_id || t("text.no_telemetry_stream")'
        in body
    )


# ---------------------------------------------------------------------------
# 4. New keys present with expected copy in both dictionaries
# ---------------------------------------------------------------------------


def test_new_keys_present_in_both_dictionaries_with_expected_copy():
    source = _read()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    # Fixed-length slice like the sibling i18n tests (test_game_admin_
    # station_signpost.py, test_game_admin_readiness_check_i18n.py) --
    # NOT source[zh_start:].index("};"), because some zh-TW copy contains a
    # literal "};" inside a placeholder (e.g. "{targetLabel};..."), which
    # truncates the block early and produces false "missing key" failures.
    zh_block = source[zh_start : zh_start + 8000]

    for key, (en_text, zh_text) in NEW_KEYS.items():
        assert (
            f'"{key}": "{en_text}"' in en_block
        ), f"{key} missing/incorrect in en-US dictionary"
        assert (
            f'"{key}": "{zh_text}"' in zh_block
        ), f"{key} missing/incorrect in zh-TW dictionary"


def test_i18n_keys_stay_symmetric_between_dictionaries():
    source = _read()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 8000]

    en_keys = set(re.findall(r'"([a-zA-Z0-9_.]+)":\s*"', en_block))
    zh_keys = set(re.findall(r'"([a-zA-Z0-9_.]+)":\s*"', zh_block))

    missing_from_zh = en_keys - zh_keys
    assert not missing_from_zh, f"keys missing from zh-TW dict: {missing_from_zh}"


# ---------------------------------------------------------------------------
# 5. Panel subtitle: exists, is one line, uses t(), sits under the title
# ---------------------------------------------------------------------------


def _station_panel_html() -> str:
    source = _read()
    panel_start = source.index('aria-labelledby="station-title"')
    panel_section_start = source.rfind("<", 0, panel_start)
    section_end = source.index("</section>", panel_start) + len("</section>")
    return source[panel_section_start:section_end]


def test_panel_has_subtitle_with_data_i18n_hook():
    panel_html = _station_panel_html()
    assert 'data-i18n="text.station_status_subtitle"' in panel_html


def test_subtitle_is_a_single_element_between_header_and_list():
    panel_html = _station_panel_html()
    header_end = panel_html.index("</div>", panel_html.index("panel-header"))
    list_start = panel_html.index('id="station-list"')
    subtitle_idx = panel_html.index('data-i18n="text.station_status_subtitle"')
    assert header_end < subtitle_idx < list_start
    # exactly one subtitle node
    assert panel_html.count('data-i18n="text.station_status_subtitle"') == 1


def test_subtitle_has_no_embedded_newline_in_source_text():
    """Keep the subtitle to one line of copy (no literal newline inside the
    element's text content)."""
    source = _read()
    marker = 'data-i18n="text.station_status_subtitle"'
    idx = source.index(marker)
    tag_close = source.index(">", idx) + 1
    text_end = source.index("<", tag_close)
    text = source[tag_close:text_end]
    assert "\n" not in text


def test_subtitle_uses_existing_muted_secondary_text_convention():
    """The subtitle must not introduce a new colour or font -- it should
    reuse var(--muted) at the same font-size the page already uses for
    secondary/meta text (e.g. .readiness-meta, .brand p)."""
    source = _read()
    marker = 'data-i18n="text.station_status_subtitle"'
    idx = source.index(marker)
    tag_start = source.rfind("<", 0, idx)
    tag_end = source.index(">", idx)
    opening_tag = source[tag_start : tag_end + 1]

    class_match = re.search(r'class="([^"]+)"', opening_tag)
    assert class_match, f"subtitle element has no class to style it: {opening_tag}"
    css_class = class_match.group(1).split()[0]

    css_rule_match = re.search(r"\." + re.escape(css_class) + r"\s*\{([^}]*)\}", source)
    assert css_rule_match, f".{css_class} has no CSS rule defined"
    rule_body = css_rule_match.group(1)
    assert "var(--muted)" in rule_body
    assert "13px" in rule_body


def test_subtitle_key_not_reused_from_unrelated_copy():
    """Guard against pointing the subtitle at the wrong dictionary entry
    (e.g. reusing header.subtitle, which describes the whole page, not this
    panel)."""
    panel_html = _station_panel_html()
    assert 'data-i18n="header.subtitle"' not in panel_html
