"""Owner request: every idle station card shows an equipment icon, reusing
the existing class-mode equipmentIconSvg (hub_server/static/index.html) --
hoisted to a shared top-level scope rather than duplicated -- with an
accessible label (aria-label/title, i18n equipment name). Also covers
requirement 4: no redundant "#N" badge in the card header any more, only
the station label plus the icon.
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
    async_marker = f"async function {name}("
    marker = f"function {name}("
    if async_marker in source:
        start = source.index(async_marker)
    else:
        start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _extract_const(source: str, name: str) -> str:
    marker = f"const {name} = "
    start = source.index(marker)
    end = source.index(";\n", start)
    return source[start : end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 1. equipmentIconSvg is genuinely shared, not duplicated: exactly one
#    definition in the whole file.
# ---------------------------------------------------------------------------


def test_equipment_icon_svg_has_exactly_one_definition():
    source = _read_index()
    assert source.count("function equipmentIconSvg(") == 1


# ---------------------------------------------------------------------------
# 2. equipmentTypeLabelKey -- pure mapping, accessible label i18n key.
# ---------------------------------------------------------------------------


def _run_label_key(equipment_type):
    source = _read_index()
    pieces = [
        _strip_js_comments(_extract_const(source, "KNOWN_EQUIPMENT_TYPES_FOR_LABEL")),
        _strip_js_comments(_extract_function(source, "equipmentTypeLabelKey")),
    ]
    script = (
        "\n".join(pieces)
        + "\n"
        + f"console.log(JSON.stringify(equipmentTypeLabelKey({json.dumps(equipment_type)})));"
    )
    return json.loads(_run_node(script))


def test_label_key_for_known_type():
    assert _run_label_key("treadmill") == "equipment_type.treadmill"
    assert _run_label_key("rower") == "equipment_type.rower"
    assert _run_label_key("fan_bike") == "equipment_type.fan_bike"


def test_label_key_is_case_insensitive():
    assert _run_label_key("TREADMILL") == "equipment_type.treadmill"


def test_label_key_falls_back_to_generic_for_unknown_type():
    assert _run_label_key("moon_walker_9000") == "equipment_type.generic"


def test_label_key_falls_back_to_generic_for_missing_type():
    assert _run_label_key(None) == "equipment_type.generic"


# ---------------------------------------------------------------------------
# 3. renderIdleStationCard -- icon present with accessible label, no "#N".
# ---------------------------------------------------------------------------


def _run_render_station_card(station_js: str) -> str:
    source = _read_index()
    pieces = [
        _strip_js_comments(_extract_function(source, "isRunningEquipment")),
        _strip_js_comments(_extract_function(source, "formatTreadmillPace")),
        _strip_js_comments(_extract_function(source, "formatIdleMetricValue")),
        _strip_js_comments(_extract_function(source, "idleParticipantLabel")),
        _strip_js_comments(_extract_const(source, "KNOWN_EQUIPMENT_TYPES_FOR_LABEL")),
        _strip_js_comments(_extract_function(source, "equipmentTypeLabelKey")),
        _strip_js_comments(_extract_function(source, "equipmentIconSvg")),
        _strip_js_comments(_extract_function(source, "idleStationIconHtml")),
        _strip_js_comments(_extract_function(source, "renderIdleStationCard")),
    ]
    script = (
        # t() returns a real, distinguishable string per key so the test
        # can assert the equipment label actually reached the markup, not
        # merely that *some* string did.
        "function t(key) { return `T[${key}]`; }\n"
        "function escapeHtml(value) { return String(value); }\n"
        + "\n".join(pieces)
        + "\n"
        + f"console.log(JSON.stringify(renderIdleStationCard({station_js})));"
    )
    return json.loads(_run_node(script))


def test_card_includes_an_equipment_icon_with_accessible_label():
    html = _run_render_station_card(
        '{"station_number": 1, "equipment_type": "rower", '
        '"instantaneous_speed_kph": 12.0, "power_watts": 180, '
        '"cadence_rpm": 28, "heart_rate_bpm": 150, "is_stale": false}'
    )
    assert 'data-equipment-icon="rower"' in html
    assert 'role="img"' in html
    assert 'aria-label="T[equipment_type.rower]"' in html
    assert 'title="T[equipment_type.rower]"' in html


def test_card_shows_a_generic_icon_for_unknown_equipment_never_a_broken_element():
    html = _run_render_station_card(
        '{"station_number": 4, "equipment_type": "mystery_machine", '
        '"instantaneous_speed_kph": 5.0, "power_watts": 50, '
        '"cadence_rpm": 40, "heart_rate_bpm": 100, "is_stale": false}'
    )
    assert 'data-equipment-icon="generic"' in html
    assert 'aria-label="T[equipment_type.generic]"' in html


def test_curved_treadmill_card_gets_the_treadmill_icon():
    # Regression: curved_treadmill was missing from equipmentIconSvg's
    # FAMILIES entirely, so curved treadmills -- the main equipment at the
    # upcoming running event -- fell back to the generic icon even though
    # their label correctly says "Curved Treadmill" / 曲面跑步機.
    html = _run_render_station_card(
        '{"station_number": 5, "equipment_type": "curved_treadmill", '
        '"instantaneous_speed_kph": 12.0, "cadence_rpm": 160, '
        '"heart_rate_bpm": 140, "is_stale": false}'
    )
    assert 'data-equipment-icon="treadmill"' in html


def test_upright_and_recumbent_bike_cards_get_the_bike_icon():
    for equipment_type in ("upright_bike", "recumbent_bike"):
        html = _run_render_station_card(
            f'{{"station_number": 6, "equipment_type": "{equipment_type}", '
            '"instantaneous_speed_kph": 20.0, "power_watts": 150, '
            '"cadence_rpm": 90, "heart_rate_bpm": 130, "is_stale": false}'
        )
        assert 'data-equipment-icon="bike"' in html
    assert "<svg" in html and "</svg>" in html


def test_stale_card_still_shows_the_icon():
    html = _run_render_station_card(
        '{"station_number": 2, "equipment_type": "treadmill", "is_stale": true}'
    )
    assert 'data-equipment-icon="treadmill"' in html
    assert "idle-station-waiting-label" in html


def test_card_no_longer_shows_a_redundant_number_badge():
    for equipment_type in ("treadmill", "rower", "fan_bike"):
        html = _run_render_station_card(
            f'{{"station_number": 1, "equipment_type": "{equipment_type}", '
            '"instantaneous_speed_kph": 10.0, "power_watts": 100, '
            '"cadence_rpm": 80, "heart_rate_bpm": 120, "is_stale": false}'
        )
        assert "idle-station-number" not in html
        assert ">#1<" not in html
