"""Class board must fit a full studio (~24 machines) on one projector screen.

Two mechanisms, tested here:

1. Density tiers -- the station grid carries a density class chosen from the
   station count, so 24 cards shrink onto one screen instead of spilling below
   the fold. Purely count-driven and CSS-expressed: no measurement, no state.
2. Rotation fallback -- past what even the tightest tier fits, the grid scrolls
   itself one page at a time. `nextRotationScrollTop` is the pure decision at
   the heart of it: which offset comes next, and when to wrap back to the top.
   It must land on the true bottom (`scrollHeight - clientHeight`) before it
   wraps, or the last partial page is never shown.
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


def _leaderboard_js(count: int) -> str:
    board = {
        f"sim-{i:02d}": {
            "node_id": f"sim-{i:02d}",
            "station_number": i + 1,
            "athlete_name": f"Athlete {i + 1}",
            "power_watts": 180,
            "instantaneous_speed_kph": 25,
            "distance_m": 500,
        }
        for i in range(count)
    }
    return json.dumps(board)


def _build_class_board_html(station_count: int) -> str:
    source = _read_index()
    script = (
        _stubs()
        + _extract_function(source, "buildClassBoardHtml")
        + "\nconst sessionData = "
        + json.dumps(
            {
                "class_plan": {"segments": [{"kind": "work", "duration_sec": 600}]},
            }
        )[:-1]
        + ', "leaderboard": '
        + _leaderboard_js(station_count)
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


def _station_grid_tag(html: str) -> str:
    match = re.search(r'<div[^>]*class="[^"]*class-station-grid[^"]*"[^>]*>', html)
    assert match, "class board has no element carrying the class-station-grid class"
    return match.group(0)


def test_eight_stations_stay_on_the_roomy_default_tier():
    tag = _station_grid_tag(_build_class_board_html(8))
    assert "class-station-grid--dense" not in tag
    assert "class-station-grid--ultra" not in tag


def test_fourteen_stations_switch_to_the_dense_tier():
    tag = _station_grid_tag(_build_class_board_html(14))
    assert "class-station-grid--dense" in tag
    assert "class-station-grid--ultra" not in tag


def test_twenty_four_stations_switch_to_the_ultra_tier():
    """A full studio -- the customer's target size -- must reach the tightest tier."""
    tag = _station_grid_tag(_build_class_board_html(24))
    assert "class-station-grid--ultra" in tag


def _min_track_px(css: str, selector: str) -> int:
    block = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert block, f"no CSS rule for {selector}"
    track = re.search(r"minmax\((\d+)px", block.group(1))
    assert track, f"{selector} does not set a minmax() column track"
    return int(track.group(1))


def test_each_density_tier_packs_columns_tighter_than_the_one_above():
    css = _read_index()
    base = _min_track_px(css, ".class-station-grid")
    dense = _min_track_px(css, ".class-station-grid--dense")
    ultra = _min_track_px(css, ".class-station-grid--ultra")
    assert base > dense > ultra, (base, dense, ultra)


def _next_rotation_scroll_top(scroll_top: int, client_h: int, scroll_h: int) -> int:
    source = _read_index()
    script = (
        _extract_function(source, "nextRotationScrollTop")
        + f"\nconsole.log(nextRotationScrollTop({scroll_top}, {client_h}, {scroll_h}));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}")
    return int(result.stdout.strip())


def test_rotation_stays_put_when_everything_already_fits():
    assert _next_rotation_scroll_top(0, 500, 500) == 0


def test_rotation_advances_one_full_page():
    assert _next_rotation_scroll_top(0, 500, 1200) == 500


def test_rotation_lands_on_the_true_bottom_before_wrapping():
    """Without this clamp the last partial page of stations is never shown."""
    assert _next_rotation_scroll_top(500, 500, 1200) == 700


def test_rotation_wraps_to_the_top_once_the_bottom_has_been_shown():
    assert _next_rotation_scroll_top(700, 500, 1200) == 0


def test_ultra_tier_drops_the_metric_labels_it_can_infer_from_units():
    """At 24 stations the card has room for "229 W", not for "Power / 229 W".

    Two halves, both required: the renderer must mark the labels, and the
    ultra tier must actually hide the marked labels. Asserting only the CSS
    would pass with nothing on the page carrying the class.
    """
    html = _build_class_board_html(24)
    assert "class-metric-label" in html, "renderer stopped marking the metric labels"

    css = _read_index()
    rule = re.search(
        r"\.class-station-grid--ultra \.class-metric-label\s*\{([^}]*)\}", css
    )
    assert rule, "ultra tier does not hide the metric labels"
    assert "display: none" in rule.group(1)


def test_ultra_tier_compacts_the_card_body_the_renderer_marks():
    """The metrics block and effort bar need hooks for the same reason."""
    html = _build_class_board_html(24)
    for hook in ("class-card-metrics", "class-effort-track"):
        assert hook in html, f"renderer stopped marking {hook}"
    css = _read_index()
    for hook in ("class-card-metrics", "class-effort-track"):
        assert re.search(
            r"\.class-station-grid--ultra \." + hook + r"\s*\{", css
        ), f"ultra tier does not compact {hook}"


def test_ultra_tier_reclaims_the_race_stage_banner_height():
    """The banner repeats what the class hero already says ("class in
    progress" + remaining), and its ~100px is a whole row of stations. It is
    hidden from the ancestor side, so nothing about the shared banner markup
    has to change."""
    css = _read_index()
    assert re.search(
        r":has\(\.class-board--ultra\)[^{]*\.race-stage-banner\s*\{[^}]*display:\s*none",
        css,
    ), "ultra tier does not reclaim the race stage banner"
