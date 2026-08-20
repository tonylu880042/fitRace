"""The station is named once per card, never twice.

The hub labels an unregistered class station "Station 3 - Vmax26_35B"
(race_manager._default_participant_name). The card also carries a station
badge, so printing the label as-is says "station 3" twice -- once in the
localized badge, once in English inside the name.

So the card strips the station half off the name while the badge is there,
and at ultra density -- where the badge is dropped to buy back 26px per card
across a 24-station board -- puts the station back on the name in the page
locale. Either way the coach reads the station exactly once.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


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
    return source[start : _matching_brace_end(source, brace_open) + 1]


def _stubs() -> str:
    return (
        "const t = (key, params = {}) => { let value = `T[${key}]`; "
        "Object.entries(params).forEach(([name, replacement]) => { "
        "value = value.replaceAll(`{${name}}`, String(replacement)); }); "
        "return value; };\n"
        "const metricNumber = (value, fallback = 0) => { const n = Number(value); "
        "return Number.isFinite(n) ? n : fallback; };\n"
        "const escapeHtml = (value) => String(value ?? '')"
        ".replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')"
        ".replace(/\"/g, '&quot;').replace(/'/g, '&#039;');\n"
        "const nodeDisplayName = (node) => "
        "node?.node_display_name || node?.display_name || node?.node_id || '--';\n"
        "const Intl = { NumberFormat: function() { return { format: (n) => String(n) }; } };\n"
        "const formatClock = (ms) => {\n"
        "  const total = Math.max(0, Math.floor(ms / 1000));\n"
        "  const m = String(Math.floor(total / 60)).padStart(2, '0');\n"
        "  const s = String(total % 60).padStart(2, '0');\n"
        "  return `${m}:${s}`;\n"
        "};\n"
        "const currentLocale = 'en-US';\n"
    )


def _station(index, athlete_name):
    return {
        "node_id": f"n{index}",
        "station_number": index,
        "athlete_name": athlete_name,
        "node_display_name": f"Node130+Machine{index}",
        "power_watts": 180,
        "instantaneous_speed_kph": 25,
        "distance_m": 500,
    }


def _board_html(stations):
    """stations: list of (station_number, athlete_name)."""
    leaderboard = {f"n{number}": _station(number, name) for number, name in stations}
    script = (
        _stubs()
        + _extract_function(_read_index(), "buildClassBoardHtml")
        + "\nconst sessionData = "
        + json.dumps(
            {"class_plan": {"segments": [{"kind": "work", "duration_sec": 600}]}}
        )[:-1]
        + ', "leaderboard": '
        + json.dumps(leaderboard)
        + "};\n"
        + "const clock = {index: 0, kind: 'work', segmentRemainingMs: 600000, "
        "totalRemainingMs: 600000, finished: false};\n"
        + "console.log(buildClassBoardHtml(sessionData, clock));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}")
    return result.stdout


def _names(html):
    return re.findall(r'class="class-station-name"[^>]*>([^<]*)<', html)


def _roomy(count, name_of):
    return [(i, name_of(i)) for i in range(1, count + 1)]


def test_badge_density_card_drops_the_station_half_of_the_hub_label():
    """Badge present -- the name must not repeat the station in English."""
    html = _board_html([(1, "Station 1 - Vmax26_35B")])
    assert "class-station-badge" in html
    assert _names(html) == ["Vmax26_35B"]


def test_ultra_density_card_carries_the_station_on_the_name_instead():
    stations = _roomy(24, lambda i: f"Station {i} - Machine{i}")
    html = _board_html(stations)
    assert "class-station-badge" not in html, "ultra keeps the redundant badge"
    assert _names(html)[0] == "T[stations.station] 1 - Machine1"


def test_a_registered_athlete_keeps_their_name_and_still_shows_a_station():
    """A registered class has real names, which carry no station half to
    strip -- the station still has to reach the card at ultra density."""
    stations = _roomy(24, lambda i: f"Coach {i}")
    html = _board_html(stations)
    assert _names(html)[0] == "T[stations.station] 1 - Coach 1"


def test_a_registered_athlete_keeps_the_badge_at_roomy_density():
    html = _board_html([(1, "Wang")])
    assert "class-station-badge" in html
    assert _names(html) == ["Wang"]


def test_only_an_exact_station_prefix_is_stripped():
    """A machine (or a person) whose name merely starts with "Station" keeps
    every character of it."""
    html = _board_html([(1, "Stationary Bike 7")])
    assert _names(html) == ["Stationary Bike 7"]


def _strip_js_comments(code: str) -> str:
    without_blocks = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    return re.sub(r"^[ \t]*//.*$\n?", "", without_blocks, flags=re.MULTILINE)


def test_a_viewport_change_re_measures_the_grid():
    """The rotation clamp sizes the grid from window.innerHeight at render
    time. A kiosk that renders before the display settles on its final
    resolution (HDMI renegotiation, a window that opens small) keeps the
    stale height forever otherwise -- the board then scroll-rotates through
    stations that would have fitted on one screen.

    Comments are stripped first: this must match the wiring, not a comment
    that merely names it.
    """
    source = _strip_js_comments(_read_index())
    listener = re.search(
        r"addEventListener\(\s*\"resize\"[\s\S]{0,600}?updateClassBoardRotation",
        source,
    )
    assert listener, "no resize handler re-measures the class board grid"
