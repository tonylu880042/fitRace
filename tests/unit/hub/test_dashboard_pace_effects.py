"""Fast running should look and feel exciting on the dashboard
(hub_server/static/index.html, display-only per CLAUDE.md). This applies
ONLY to rows whose equipment is running equipment (the existing
isRunningEquipment rule: treadmill, curved_treadmill) during a RUNNING
race -- bikes/rowers and finished rows are unaffected. Pace comes from the
existing formatPacePerKm logic (seconds per km = 3600 / instantaneous_
speed_kph; speeds at or below 0.5 kph have no pace).

Three effects, driven by two pure helpers:
  - paceBand(speedKph) -- "none" | "steady" | "fast" | "blazing" from
    fixed seconds-per-km thresholds: >=360s none, 300<=s<360 steady,
    240<=s<300 fast, <240 blazing.
  - fastestPaceNodeId(nodes) -- the node_id of the single fastest eligible
    running row among rows that are not finished (ties broken by lowest
    station number), or null when the best pace is slower than steady, or
    when exactly one running row qualifies but it is not blazing.

Both are duplicated as nested declarations inside every render/patch
function that needs them (formatSprintSignal, renderSprintBoardLeaderboard,
renderRaceTrackLeaderboard's individualRows.map callback, renderLeaderboard's
nodes.forEach callback and its own top scope, updateLeaderboardCardValues)
rather than declared once as a top-level sibling -- mirroring the existing
isRunningEquipment/formatPacePerKm convention documented in
test_dashboard_treadmill_pace.py. This file extracts the real functions
from index.html the same way that file (and test_dashboard_race_board_
density.py, test_dashboard_anonymous_everywhere.py) do, and runs them
under node. No assertion here is satisfied by a comment: every check reads
a raw rendered HTML string or a value patched onto a fake DOM element.
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
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 1. paceBand -- pure, no DOM. Extracted from formatSprintSignal, the first
#    of paceBand's nested copies to appear in index.html. Every copy is
#    byte-for-byte identical, so this is a faithful test of all of them
#    (the render-level tests below independently exercise the other
#    copies through their enclosing renderers).
# ---------------------------------------------------------------------------


def _extract_pace_band() -> str:
    source = _read_index()
    fn = _extract_function(source, "formatSprintSignal")
    fn = _strip_js_comments(fn)
    return _extract_function(fn, "paceBand")


def _run_pace_band(speed_kph):
    fn = _extract_pace_band()
    script = fn + f"\nconsole.log(JSON.stringify(paceBand({json.dumps(speed_kph)})));"
    return json.loads(_run_node(script))


def test_pace_band_zero_is_none():
    assert _run_pace_band(0) == "none"


def test_pace_band_not_finite_is_none():
    assert _run_pace_band(None) == "none"


def test_pace_band_at_the_0_5_kph_floor_is_none():
    assert _run_pace_band(0.5) == "none"


def test_pace_band_just_above_six_minutes_is_none():
    # 3600 / 9.99 = 360.36s/km -- at/above the 360s ceiling, still none.
    assert _run_pace_band(9.99) == "none"


def test_pace_band_exactly_six_minutes_is_none():
    # 3600 / 10.0 = 360s/km exactly -- the >=360 boundary belongs to none.
    assert _run_pace_band(10.0) == "none"


def test_pace_band_just_under_six_minutes_is_steady():
    # 3600 / 10.01 = 359.6s/km, just inside the steady band.
    assert _run_pace_band(10.01) == "steady"


def test_pace_band_exactly_five_minutes_is_steady_not_fast():
    # 3600 / 12 = 300s/km exactly. The table's fast range is
    # 240 <= s < 300, so the 300s boundary itself is steady, not fast.
    assert _run_pace_band(12) == "steady"


def test_pace_band_just_under_five_minutes_is_fast():
    # 3600 / 12.01 = 299.75s/km, just inside the fast band.
    assert _run_pace_band(12.01) == "fast"


def test_pace_band_exactly_four_minutes_is_fast():
    # 3600 / 15 = 240s/km exactly -- the fast band includes its own 240s
    # floor.
    assert _run_pace_band(15) == "fast"


def test_pace_band_just_under_four_minutes_is_blazing():
    assert _run_pace_band(15.01) == "blazing"


def test_pace_band_very_fast_is_blazing():
    assert _run_pace_band(20) == "blazing"


def test_bike_rows_never_get_a_pace_band_regardless_of_speed():
    # paceBand itself is speed-only (it does not know about equipment) --
    # every call site gates it behind isRunningEquipment first, so a bike
    # row's band is always "none" no matter how fast it is spinning. This
    # exercises that exact gate, extracted alongside paceBand from the
    # same formatSprintSignal copy.
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "formatSprintSignal"))
    is_running = _extract_function(fn, "isRunningEquipment")
    band = _extract_function(fn, "paceBand")
    script = (
        is_running
        + "\n"
        + band
        + "\n"
        + "function effectiveBand(equipmentType, speedKph) {\n"
        + "  return isRunningEquipment(equipmentType) ? paceBand(speedKph) : 'none';\n"
        + "}\n"
        + "console.log(JSON.stringify(effectiveBand('spin_bike', 30)));"
    )
    assert json.loads(_run_node(script)) == "none"


# ---------------------------------------------------------------------------
# 2. fastestPaceNodeId -- pure, no DOM. Extracted from
#    renderSprintBoardLeaderboard, the first of its nested copies to
#    appear in index.html.
# ---------------------------------------------------------------------------


def _extract_fastest_pace_node_id() -> str:
    source = _read_index()
    fn = _extract_function(source, "renderSprintBoardLeaderboard")
    fn = _strip_js_comments(fn)
    return _extract_function(fn, "fastestPaceNodeId")


def _run_fastest(nodes):
    fn = _extract_fastest_pace_node_id()
    script = (
        fn + f"\nconsole.log(JSON.stringify(fastestPaceNodeId({json.dumps(nodes)})));"
    )
    return json.loads(_run_node(script))


def _fp_node(node_id, speed, station=1, equipment_type="treadmill", finished=False):
    return {
        "node_id": node_id,
        "station_number": station,
        "instantaneous_speed_kph": speed,
        "equipment_type": equipment_type,
        "finished_time_ms": 12345 if finished else None,
    }


def test_fastest_pace_node_id_empty_list_returns_null():
    assert _run_fastest([]) is None


def test_fastest_pace_node_id_picks_the_fastest_of_several_running_rows():
    nodes = [
        _fp_node("n1", 15, station=1),  # 240s -> fast
        _fp_node("n2", 20, station=2),  # 180s -> blazing (fastest)
        _fp_node("n3", 10.909, station=3),  # ~330s -> steady
    ]
    assert _run_fastest(nodes) == "n2"


def test_fastest_pace_node_id_ignores_bikes():
    nodes = [
        _fp_node("n1", 30, station=1, equipment_type="spin_bike"),
        _fp_node("n2", 20, station=2, equipment_type="treadmill"),
    ]
    assert _run_fastest(nodes) == "n2"


def test_fastest_pace_node_id_ignores_finished_rows():
    # n2 is the only non-finished candidate, so it must be blazing on its
    # own to win (the single-running-row rule below) -- 20 kph clears
    # that regardless of n1's finished, faster speed.
    nodes = [
        _fp_node("n1", 25, station=1, finished=True),
        _fp_node("n2", 20, station=2),
    ]
    assert _run_fastest(nodes) == "n2"


def test_fastest_pace_node_id_returns_null_when_best_is_slower_than_six_minutes():
    nodes = [_fp_node("n1", 9, station=1), _fp_node("n2", 8, station=2)]
    assert _run_fastest(nodes) is None


def test_fastest_pace_node_id_single_running_row_needs_blazing():
    steady_row = [_fp_node("n1", 10.909, station=1)]  # ~330s -> steady
    assert _run_fastest(steady_row) is None

    fast_row = [_fp_node("n1", 15, station=1)]  # 240s -> fast
    assert _run_fastest(fast_row) is None

    blazing_row = [_fp_node("n1", 20, station=1)]  # 180s -> blazing
    assert _run_fastest(blazing_row) == "n1"


def test_fastest_pace_node_id_tie_goes_to_lowest_station():
    nodes = [
        _fp_node("n1", 20, station=5),
        _fp_node("n2", 20, station=2),
    ]
    assert _run_fastest(nodes) == "n2"


# ---------------------------------------------------------------------------
# 3. Classic leaderboard -- full render and the O(1) fast path
#    (updateLeaderboardCardValues), driven against a fake DOM extending
#    the one in test_dashboard_treadmill_pace.py / test_dashboard_
#    leaderboard_card_cache.py with className support on the row, the
#    metric-val cells and the fastest-pace badge (all plain mutable
#    strings, not classList, matching production's className-token
#    toggling so this exercises the exact same code path).
# ---------------------------------------------------------------------------

_CLASSIC_FN_NAMES = [
    "resetLeaderboardCardCache",
    "buildLeaderboardCardSignature",
    "captureLeaderboardCardRefs",
    "setSmoothedCardText",
    "updateLeaderboardCardValues",
    "raceBoardDensityTier",
    "renderLeaderboard",
    "sortLeaderboardNodes",
    "isLeaderboardFinal",
    "relayLegLine",
]


def _extract_classic_fns() -> str:
    source = _read_index()
    return "\n".join(
        _strip_js_comments(_extract_function(source, name))
        for name in _CLASSIC_FN_NAMES
    )


def _classic_stubs(current_state: str) -> str:
    return (
        "const t = (key) => `T[${key}]`;\n"
        "const metricNumber = (value, fallback = 0) => { const n = Number(value); return Number.isFinite(n) ? n : fallback; };\n"
        "const escapeHtml = (value) => String(value == null ? '' : value);\n"
        "const nodeDisplayName = (node) => (node && (node.node_display_name || node.display_name || node.node_id)) || '--';\n"
        "let leaderboardSmoothValues = new Map();\n"
        "function smoothMetricNumber(key, targetValue) { const target = metricNumber(targetValue); leaderboardSmoothValues.set(key, target); return target; }\n"
        "function animateLeaderboardReorder() {}\n"
        "function detectAthleteFinishes() {}\n"
        "function detectRelayHandoffs() {}\n"
        "function renderRegistrationEmptyState() { return ''; }\n"
        "function renderPodium() { return ''; }\n"
        "function triggerFinishCelebration() {}\n"
        "let leaderboardNodes = [];\n"
        "let teamLeaderboardRows = [];\n"
        "let leaderboardRankByNode = new Map();\n"
        "let leaderboardDisplayMode = 'classic';\n"
        "let leaderboardCardRefs = new Map();\n"
        "let leaderboardCardSignature = null;\n"
        "let currentSessionMode = 'race';\n"
        f"let currentState = {json.dumps(current_state)};\n"
        "let currentConfig = { race_type: 'distance' };\n"
    )


def _classic_fake_dom() -> str:
    """Extends the fake DOM in test_dashboard_treadmill_pace.py: rows and
    metric-val cells carry a mutable className (a plain string property,
    matching production's toggleClassToken -- not classList), and each
    row exposes a fastest-pace badge element the same way."""
    return r"""
let innerHTMLSetCount = 0;

function makeClassBox(initial) {
  const box = { _className: initial || "" };
  Object.defineProperty(box, "className", {
    get() { return box._className; },
    set(v) { box._className = v; },
  });
  return box;
}

function parseContainerRows(html) {
  const rowOpenRe = /<div class="([^"]*)" id="node-[^"]*" data-node-id="([^"]+)">/g;
  const opens = [];
  let m;
  while ((m = rowOpenRe.exec(html))) {
    opens.push({ className: m[1], nodeId: m[2], start: m.index + m[0].length });
  }
  return opens.map((o, i) => {
    const end = i + 1 < opens.length ? opens[i + 1].start : html.length;
    const segment = html.slice(o.start, end);
    const metricMatches = [...segment.matchAll(/<div class="(metric-val[^"]*)">([^<]*)<\/div>/g)];
    const metricVals = metricMatches.map((mm) => ({ className: mm[1], text: mm[2] }));
    const fillMatch = segment.match(/<div class="progress-fill" style="width: ([^%]+)%">/);
    const badgeMatch = segment.match(/<span class="(fastest-pace-badge[^"]*)" aria-hidden="true">/);
    return {
      nodeId: o.nodeId,
      className: o.className,
      metricVals,
      fillWidth: fillMatch ? fillMatch[1] : null,
      badgeClass: badgeMatch ? badgeMatch[1] : null,
    };
  });
}

function makeFakeRow(parsed) {
  const rowBox = makeClassBox(parsed.className);
  const metricEls = parsed.metricVals.map((mv) => {
    const el = makeClassBox(mv.className);
    el._text = mv.text;
    Object.defineProperty(el, "textContent", {
      get() { return el._text; },
      set(v) { el._text = v; },
    });
    return el;
  });
  let fillEl = null;
  if (parsed.fillWidth !== null) {
    fillEl = { style: { _width: parsed.fillWidth + "%" } };
    Object.defineProperty(fillEl.style, "width", {
      get() { return fillEl.style._width; },
      set(v) { fillEl.style._width = v; },
    });
  }
  const badgeEl = parsed.badgeClass !== null ? makeClassBox(parsed.badgeClass) : null;
  rowBox.dataset = { nodeId: parsed.nodeId };
  rowBox.style = {};
  rowBox.getBoundingClientRect = () => ({ top: 0, left: 0 });
  rowBox.querySelectorAll = (sel) => (sel === ".metric-val" ? metricEls : []);
  rowBox.querySelector = (sel) => {
    if (sel === ".progress-fill") return fillEl;
    if (sel === ".fastest-pace-badge") return badgeEl;
    return null;
  };
  return rowBox;
}

function makeContainer() {
  let html = "";
  let rows = [];
  const container = {
    querySelectorAll(sel) { return sel === ".leaderboard-item" ? rows : []; },
    querySelector() { return null; },
  };
  Object.defineProperty(container, "innerHTML", {
    get() { return html; },
    set(value) {
      html = value;
      innerHTMLSetCount += 1;
      rows = parseContainerRows(value).map(makeFakeRow);
    },
  });
  return container;
}

const leaderboardContainer = makeContainer();
const document = {
  getElementById(id) { return id === "leaderboard-container" ? leaderboardContainer : null; },
  querySelectorAll() { return []; },
};

function readCard(nodeId) {
  const row = leaderboardContainer.querySelectorAll(".leaderboard-item").find((r) => r.dataset.nodeId === nodeId);
  if (!row) return null;
  const badge = row.querySelector(".fastest-pace-badge");
  return {
    className: row.className,
    metricVals: row.querySelectorAll(".metric-val").map((el) => ({ text: el.textContent, className: el.className })),
    badgeClassName: badge ? badge.className : null,
  };
}
"""


def _node(
    node_id,
    speed,
    station=1,
    equipment_type=None,
    finished=False,
    distance=500,
    progress=50,
):
    node = {
        "node_id": node_id,
        "athlete_name": f"Athlete {node_id}",
        "station_number": station,
        "power_watts": 100,
        "instantaneous_speed_kph": speed,
        "distance_m": distance,
        "progress_percent": progress,
    }
    if equipment_type is not None:
        node["equipment_type"] = equipment_type
    if finished:
        node["finished_time_ms"] = 12345
    return node


def _run_classic(script_body: str, current_state: str = "RUNNING") -> dict:
    script = (
        _classic_stubs(current_state)
        + _classic_fake_dom()
        + _extract_classic_fns()
        + "\n"
        + script_body
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def test_classic_full_render_blazing_treadmill_gets_blazing_class_and_badge():
    n1 = _node("n1", 20, station=1, equipment_type="treadmill")  # 180s -> blazing
    script = f"""
renderLeaderboard({{ n1: {json.dumps(n1)} }});
console.log(JSON.stringify(readCard("n1")));
"""
    result = _run_classic(script)
    assert "pace-blazing" in result["metricVals"][0]["className"]
    assert result["badgeClassName"] is not None
    assert "is-visible" in result["badgeClassName"]
    assert "fastest-pace-highlight" in result["className"]


def test_classic_full_render_bike_row_has_neither_band_nor_badge():
    n1 = _node("n1", 20, station=1, equipment_type="spin_bike")
    script = f"""
renderLeaderboard({{ n1: {json.dumps(n1)} }});
console.log(JSON.stringify(readCard("n1")));
"""
    result = _run_classic(script)
    assert "pace-" not in result["metricVals"][0]["className"]
    assert (
        result["badgeClassName"] is None or "is-visible" not in result["badgeClassName"]
    )
    assert "fastest-pace-highlight" not in result["className"]


def test_classic_full_render_no_effects_when_race_is_not_running():
    n1 = _node("n1", 20, station=1, equipment_type="treadmill")
    script = f"""
renderLeaderboard({{ n1: {json.dumps(n1)} }});
console.log(JSON.stringify(readCard("n1")));
"""
    result = _run_classic(script, current_state="STOPPED")
    assert "pace-" not in result["metricVals"][0]["className"]
    assert (
        result["badgeClassName"] is None or "is-visible" not in result["badgeClassName"]
    )
    assert "fastest-pace-highlight" not in result["className"]


def test_classic_full_render_finished_treadmill_row_has_no_effects():
    n1 = _node(
        "n1", 20, station=1, equipment_type="treadmill", finished=True, progress=100
    )
    script = f"""
renderLeaderboard({{ n1: {json.dumps(n1)} }});
console.log(JSON.stringify(readCard("n1")));
"""
    result = _run_classic(script)
    assert "pace-" not in result["metricVals"][0]["className"]
    assert "fastest-pace-highlight" not in result["className"]


def test_classic_fast_path_band_class_changes_from_steady_to_blazing_without_rebuild():
    n1_steady = _node(
        "n1", 10.909, station=1, equipment_type="treadmill"
    )  # ~330s -> steady
    n1_blazing = dict(n1_steady, instantaneous_speed_kph=20)  # 180s -> blazing
    script = f"""
renderLeaderboard({{ n1: {json.dumps(n1_steady)} }});
const afterFirst = innerHTMLSetCount;
const before = readCard("n1");
renderLeaderboard({{ n1: {json.dumps(n1_blazing)} }});
const afterSecond = innerHTMLSetCount;
const after = readCard("n1");
console.log(JSON.stringify({{ afterFirst, afterSecond, before, after }}));
"""
    result = _run_classic(script)
    assert result["afterSecond"] == result["afterFirst"], (
        "a speed-only tick on an unchanged station must take the card-cache "
        "fast path, not rebuild"
    )
    assert "pace-steady" in result["before"]["metricVals"][0]["className"]
    assert "pace-blazing" in result["after"]["metricVals"][0]["className"]


def test_classic_fast_path_fastest_badge_moves_to_the_new_fastest_row_without_rebuild():
    # Different progress/distance so sortLeaderboardNodes never reorders
    # these two stations on a speed-only change -- the fast path is only
    # eligible when the card signature (which bakes in node order) stays
    # identical between ticks.
    n1a = _node(
        "n1", 20, station=1, equipment_type="treadmill", distance=800, progress=80
    )
    n2a = _node(
        "n2", 15, station=2, equipment_type="treadmill", distance=500, progress=50
    )
    n1b = dict(n1a)
    n2b = dict(n2a, instantaneous_speed_kph=25)
    script = f"""
renderLeaderboard({{ n1: {json.dumps(n1a)}, n2: {json.dumps(n2a)} }});
const afterFirst = innerHTMLSetCount;
const firstN1 = readCard("n1");
const firstN2 = readCard("n2");
renderLeaderboard({{ n1: {json.dumps(n1b)}, n2: {json.dumps(n2b)} }});
const afterSecond = innerHTMLSetCount;
const secondN1 = readCard("n1");
const secondN2 = readCard("n2");
console.log(JSON.stringify({{ afterFirst, afterSecond, firstN1, firstN2, secondN1, secondN2 }}));
"""
    result = _run_classic(script)
    assert (
        result["afterSecond"] == result["afterFirst"]
    ), "the fastest-pace hand-off must patch cached elements, not rebuild"
    assert "is-visible" in (result["firstN1"]["badgeClassName"] or "")
    assert "is-visible" not in (result["firstN2"]["badgeClassName"] or "")
    assert "is-visible" not in (result["secondN1"]["badgeClassName"] or "")
    assert "is-visible" in (result["secondN2"]["badgeClassName"] or "")
    assert "fastest-pace-highlight" in result["firstN1"]["className"]
    assert "fastest-pace-highlight" not in result["firstN2"]["className"]
    assert "fastest-pace-highlight" not in result["secondN1"]["className"]
    assert "fastest-pace-highlight" in result["secondN2"]["className"]


def test_classic_signature_does_not_change_on_a_pace_band_or_fastest_change():
    """The card signature must never invalidate the fast path over a band
    or fastest change -- those are exactly what the fast path exists to
    patch without a rebuild."""
    n1_slow = _node("n1", 10.909, station=1, equipment_type="treadmill")
    n1_fast = dict(n1_slow, instantaneous_speed_kph=20)
    script = f"""
const sigSlow = buildLeaderboardCardSignature([{json.dumps(n1_slow)}], "distance", false);
const sigFast = buildLeaderboardCardSignature([{json.dumps(n1_fast)}], "distance", false);
console.log(JSON.stringify({{ sigSlow, sigFast }}));
"""
    result = _run_classic(script)
    assert result["sigSlow"] == result["sigFast"]


# ---------------------------------------------------------------------------
# 4. Sprint Board and Race Track -- real getRankedIndividualRows,
#    sortLeaderboardNodes, stationLabel, relayLegLine, formatResultScore,
#    raceBoardDensityTier and the renderer itself, driven against a
#    minimal fake container (innerHTML set/get only), matching the
#    accepted pattern in test_dashboard_race_board_density.py.
# ---------------------------------------------------------------------------

_BOARD_FN_NAMES = [
    "raceBoardDensityTier",
    "metricNumber",
    "nodeDisplayName",
    "stationLabel",
    "relayLegLine",
    "formatResultScore",
    "sortLeaderboardNodes",
    "getRankedIndividualRows",
]


def _board_stubs(current_state: str) -> str:
    return (
        "const t = (key, params = {}) => { let value = `T[${key}]`; "
        "Object.entries(params).forEach(([name, replacement]) => { "
        "value = value.replaceAll(`{${name}}`, String(replacement)); }); "
        "return value; };\n"
        "const escapeHtml = (value) => String(value == null ? '' : value);\n"
        "let leaderboardSmoothValues = new Map();\n"
        "function smoothMetricNumber(key, targetValue) { const target = metricNumber(targetValue); leaderboardSmoothValues.set(key, target); return target; }\n"
        "function resetLeaderboardCardCache() {}\n"
        "function animateLeaderboardReorder() {}\n"
        "function triggerFinishCelebration() {}\n"
        "function renderLeaderboardEmptyState() { return ''; }\n"
        "function renderPodium() { return ''; }\n"
        "function renderTeamPodium() { return ''; }\n"
        "function isLeaderboardFinal() { return false; }\n"
        "function isTeamLeaderboardFinal() { return false; }\n"
        "function renderMemberProgressChips() { return ''; }\n"
        "function getRankedTeamRows() { return []; }\n"
        "function formatTeamScore() { return { value: '', label: '' }; }\n"
        "function teamCompletionLabel() { return ''; }\n"
        "function teamPolicyLabel() { return ''; }\n"
        "let leaderboardNodes = [];\n"
        "let teamLeaderboardRows = [];\n"
        "let leaderboardRankByNode = new Map();\n"
        f"let currentState = {json.dumps(current_state)};\n"
        "let currentConfig = { race_type: 'distance', competition_mode: 'individual' };\n"
    )


def _board_fake_dom() -> str:
    return r"""
function makeContainer() {
  let html = "";
  const container = {
    querySelectorAll() { return []; },
    querySelector() { return null; },
  };
  Object.defineProperty(container, "innerHTML", {
    get() { return html; },
    set(value) { html = value; },
  });
  return container;
}

const leaderboardContainer = makeContainer();
const document = {
  getElementById(id) { return id === "leaderboard-container" ? leaderboardContainer : null; },
  querySelectorAll() { return []; },
};
"""


def _extract_board_fns(extra_names) -> str:
    source = _read_index()
    names = _BOARD_FN_NAMES + list(extra_names)
    return "\n".join(
        _strip_js_comments(_extract_function(source, name)) for name in names
    )


def _board_node(node_id, speed, station, equipment_type="treadmill"):
    return {
        "node_id": node_id,
        "station_number": station,
        "athlete_name": f"Athlete {node_id}",
        "power_watts": 150,
        "instantaneous_speed_kph": speed,
        "distance_m": 300,
        "progress_percent": 10,
        "equipment_type": equipment_type,
    }


def _run_board(
    renderer_name: str, progress_data: dict, current_state: str = "RUNNING"
) -> str:
    script = (
        _board_stubs(current_state)
        + _board_fake_dom()
        + _extract_board_fns([renderer_name, "formatSprintSignal"])
        + f"\n{renderer_name}({json.dumps(progress_data)});"
        + "\nconsole.log(leaderboardContainer.innerHTML);"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout


def _row_segments(html: str, marker: str):
    opens = [m.start() for m in re.finditer(re.escape(marker), html)]
    return [
        html[start : opens[i + 1] if i + 1 < len(opens) else len(html)]
        for i, start in enumerate(opens)
    ]


def _segment_for_node(segments, node_id):
    for segment in segments:
        if f'data-node-id="{node_id}"' in segment:
            return segment
    raise AssertionError(f"no segment found for {node_id}")


def test_sprint_board_shows_band_class_and_badge_for_the_fastest_row():
    nodes = {
        "n1": _board_node("n1", 20, 1),  # 180s -> blazing, fastest
        "n2": _board_node("n2", 10.909, 2),  # ~330s -> steady
    }
    html = _run_board("renderSprintBoardLeaderboard", nodes)
    segments = _row_segments(html, '<div class="sprint-board-card')
    n1 = _segment_for_node(segments, "n1")
    n2 = _segment_for_node(segments, "n2")
    assert "pace-blazing" in n1
    assert "fastest-pace-badge is-visible" in n1
    assert "fastest-pace-highlight" in n1
    assert "pace-steady" in n2
    assert "fastest-pace-badge is-visible" not in n2
    assert "fastest-pace-highlight" not in n2


def test_sprint_board_bike_row_never_gets_a_band_or_badge():
    nodes = {"n1": _board_node("n1", 20, 1, equipment_type="spin_bike")}
    html = _run_board("renderSprintBoardLeaderboard", nodes)
    segments = _row_segments(html, '<div class="sprint-board-card')
    n1 = _segment_for_node(segments, "n1")
    assert "pace-" not in n1
    assert "fastest-pace-badge is-visible" not in n1


def test_race_track_marker_carries_a_trail_class_per_band():
    nodes = {
        "n1": _board_node("n1", 20, 1),  # 180s -> blazing
        "n2": _board_node("n2", 12.5, 2),  # 288s -> fast
        "n3": _board_node("n3", 10.5, 3),  # ~342.9s -> steady
        "n4": _board_node("n4", 9, 4),  # 400s -> none
    }
    html = _run_board("renderRaceTrackLeaderboard", nodes)
    segments = _row_segments(html, '<div class="race-track-item')

    def marker_class(node_id):
        segment = _segment_for_node(segments, node_id)
        match = re.search(r'<div class="race-track-marker([^"]*)"', segment)
        assert match, f"no race-track-marker element for {node_id}"
        return match.group(1)

    assert "pace-blazing" in marker_class("n1")
    assert "pace-fast" in marker_class("n2")
    assert "pace-steady" in marker_class("n3")
    assert marker_class("n4").strip() == ""


def test_race_track_bike_marker_never_gets_a_trail_class():
    nodes = {"n1": _board_node("n1", 20, 1, equipment_type="spin_bike")}
    html = _run_board("renderRaceTrackLeaderboard", nodes)
    segments = _row_segments(html, '<div class="race-track-item')
    segment = _segment_for_node(segments, "n1")
    match = re.search(r'<div class="race-track-marker([^"]*)"', segment)
    assert match
    assert match.group(1).strip() == ""


def test_race_track_no_trail_when_race_is_not_running():
    nodes = {"n1": _board_node("n1", 20, 1)}
    html = _run_board("renderRaceTrackLeaderboard", nodes, current_state="STOPPED")
    segments = _row_segments(html, '<div class="race-track-item')
    segment = _segment_for_node(segments, "n1")
    match = re.search(r'<div class="race-track-marker([^"]*)"', segment)
    assert match
    assert match.group(1).strip() == ""


# ---------------------------------------------------------------------------
# 5. CSS -- the band/badge/highlight/trail rules exist, and the reduced-
#    motion media query disables every pace-effect animation while
#    leaving colours and badge visibility alone. Structural check on top
#    of (not instead of) the behaviour tests above.
# ---------------------------------------------------------------------------


def test_css_pace_band_color_rules_exist_for_all_three_bands():
    css = _read_index()
    for band in ("steady", "fast", "blazing"):
        assert re.search(
            r"\.metric-val\.pace-" + band, css
        ), f"no .metric-val.pace-{band} rule"
        assert re.search(
            r"\.sprint-board-signal\.pace-" + band, css
        ), f"no .sprint-board-signal.pace-{band} rule"


def test_css_race_track_marker_trail_rules_exist_for_all_three_bands():
    css = _read_index()
    for band in ("steady", "fast", "blazing"):
        assert re.search(
            r"\.race-track-marker\.pace-" + band + r"::before", css
        ), f"no .race-track-marker.pace-{band}::before rule"


def test_css_fastest_badge_and_highlight_rules_exist():
    css = _read_index()
    assert ".fastest-pace-badge" in css
    assert ".fastest-pace-badge.is-visible" in css
    assert re.search(r"\.leaderboard-item\.fastest-pace-highlight", css)
    assert re.search(r"\.sprint-board-card\.fastest-pace-highlight", css)


def test_css_reduced_motion_disables_pace_effect_animations():
    css = _read_index()
    match = re.search(
        r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n    \}\n\n    @media \(max-width: 760px\)",
        css,
        re.DOTALL,
    )
    assert match, "could not find the first reduced-motion block"
    block = match.group(1)
    for selector in (
        ".metric-val",
        ".sprint-board-signal",
        ".race-track-marker::before",
        ".fastest-pace-badge",
    ):
        assert selector in block, f"{selector} is not disabled under reduced motion"
    assert "animation: none !important" in block
