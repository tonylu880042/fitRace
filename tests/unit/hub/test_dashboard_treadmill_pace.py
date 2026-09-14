"""Runners prefer pace over speed. The dashboard (hub_server/static/
index.html, display-only per CLAUDE.md) now shows pace-per-kilometre
instead of km/h for any row whose equipment_type is "treadmill" or
"curved_treadmill" -- everywhere a per-row speed is displayed: the classic
leaderboard (full render and its O(1) patch fast path), the class board
(full render and its incremental patch fast path), and the sprint board.
Every other equipment type (bikes, rowers, unknown) keeps showing raw kph,
unchanged. Ranking/sorting stays speed-based -- this feature never touches
sortLeaderboardNodes.

Two pure helpers drive the whole feature:
  - isRunningEquipment(equipmentType) -- true for "treadmill" and
    "curved_treadmill" only.
  - formatPacePerKm(speedKph) -- "M'SS\"" pace text, or "--'--\"" for a
    speed that is not finite, at or below 0.5 kph, or so slow the pace
    would be an hour or more per kilometre.

Both are duplicated as *nested* declarations inside every render/patch
function that needs them (isRunningEquipment/formatPacePerKm inside
renderLeaderboard's nodes.forEach callback, updateLeaderboardCardValues,
formatSprintSignal, and buildClassBoardHtml; isRunningEquipmentForPatch/
formatPacePerKmForPatch inside applyClassBoardIncrementalUpdate) rather than
declared once as a top-level sibling. That mirrors the codebase's existing
equipmentIconSvg convention (see buildClassBoardHtml in index.html): many
tests across this suite extract a single render/patch function alone by its
function-name marker and execute it standalone under node -e, so a helper
those functions call has to live inside the extracted text itself, or every
one of those tests would fail with a ReferenceError.

This file extracts the real functions from index.html the same way
tests/unit/hub/test_dashboard_leaderboard_card_cache.py,
tests/unit/hub/test_dashboard_class_board_card_cache.py, and
tests/unit/hub/test_dashboard_i18n.py already do, and runs them under node.
No assertion here is satisfied by a comment: every check reads either a
raw rendered HTML string or a value patched onto a fake DOM element.
"""

import json
import re
import subprocess
from pathlib import Path

from hub_server.infrastructure.locales import load_locale

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
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 1. Pure helpers -- formatPacePerKm and isRunningEquipment, executed
#    directly under node with no DOM and no other page state. Extracted
#    from formatSprintSignal, the first of the five nested copies to
#    appear in index.html -- the marker-based extraction below picks up
#    whichever "function isRunningEquipment(" occurs first in the file, and
#    since every nested copy is byte-for-byte identical in behaviour, this
#    is a faithful test of all of them.
# ---------------------------------------------------------------------------


def _extract_pure_helper(name: str) -> str:
    source = _read_index()
    fn = _extract_function(source, "formatSprintSignal")
    fn = _strip_js_comments(fn)
    return _extract_function(fn, name)


def _run_format_pace_per_km(speed) -> str:
    fn = _extract_pure_helper("formatPacePerKm")
    script = (
        fn + f"\nconsole.log(JSON.stringify(formatPacePerKm({json.dumps(speed)})));"
    )
    return json.loads(_run_node(script))


def _run_is_running_equipment(equipment_type) -> bool:
    fn = _extract_pure_helper("isRunningEquipment")
    script = (
        fn
        + f"\nconsole.log(JSON.stringify(isRunningEquipment({json.dumps(equipment_type)})));"
    )
    return json.loads(_run_node(script))


def test_format_pace_per_km_examples():
    assert _run_format_pace_per_km(12) == "5'00\""
    assert _run_format_pace_per_km(10.909) == "5'30\""
    assert _run_format_pace_per_km(20) == "3'00\""
    assert _run_format_pace_per_km(0) == "--'--\""


def test_format_pace_per_km_at_the_0_5_kph_boundary_is_placeholder():
    assert _run_format_pace_per_km(0.5) == "--'--\""
    # 3600 / 0.51 rounds to 7059s, which is >= 3600s -- still a placeholder,
    # since the >= 3600 cap catches it even though 0.51 is above the 0.5
    # boundary check. A speed a little further above the floor genuinely
    # returns a real (if extreme) pace instead.
    assert _run_format_pace_per_km(0.51) == "--'--\""
    assert _run_format_pace_per_km(1.2) == "50'00\""


def test_format_pace_per_km_not_finite_is_placeholder():
    assert _run_format_pace_per_km(None) == "--'--\""


def test_is_running_equipment_treadmill_and_curved_treadmill_true():
    assert _run_is_running_equipment("treadmill") is True
    assert _run_is_running_equipment("curved_treadmill") is True


def test_is_running_equipment_other_types_false():
    assert _run_is_running_equipment("spin_bike") is False
    assert _run_is_running_equipment("rower") is False
    assert _run_is_running_equipment(None) is False


# ---------------------------------------------------------------------------
# 2. Classic leaderboard -- full render (metric label + value), the O(1)
#    patch fast path (updateLeaderboardCardValues), and the card signature
#    (buildLeaderboardCardSignature) that must invalidate the fast path
#    when a row's equipment_type changes. Extraction/stub/fake-DOM harness
#    mirrors tests/unit/hub/test_dashboard_leaderboard_card_cache.py.
# ---------------------------------------------------------------------------

_CLASSIC_FN_NAMES = [
    "resetLeaderboardCardCache",
    "buildLeaderboardCardSignature",
    "captureLeaderboardCardRefs",
    "setSmoothedCardText",
    "updateLeaderboardCardValues",
    # renderLeaderboard's classic branch applies a row-count density tier
    # (see test_dashboard_race_board_density.py) via raceBoardDensityTier --
    # extracted here too so the real renderLeaderboard resolves it.
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


def _classic_stubs() -> str:
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
        "let currentState = 'IDLE';\n"
        "let currentConfig = { race_type: 'distance' };\n"
    )


def _classic_fake_dom() -> str:
    """Same fake DOM as test_dashboard_leaderboard_card_cache.py: supports
    innerHTML get/set on the container, querySelectorAll(".leaderboard-item"),
    row.querySelectorAll(".metric-val"), and row.querySelector(".progress-fill")."""
    return r"""
let innerHTMLSetCount = 0;

function parseContainerRows(html) {
  const rowOpenRe = /<div class="leaderboard-item[^"]*" id="[^"]*" data-node-id="([^"]+)">/g;
  const opens = [];
  let m;
  while ((m = rowOpenRe.exec(html))) {
    opens.push({ nodeId: m[1], start: m.index + m[0].length });
  }
  return opens.map((o, i) => {
    const end = i + 1 < opens.length ? opens[i + 1].start : html.length;
    const segment = html.slice(o.start, end);
    const metricVals = [...segment.matchAll(/<div class="metric-val[^"]*">([^<]*)<\/div>/g)].map((mm) => mm[1]);
    const fillMatch = segment.match(/<div class="progress-fill" style="width: ([^%]+)%">/);
    return { nodeId: o.nodeId, metricVals, fillWidth: fillMatch ? fillMatch[1] : null };
  });
}

function makeFakeRow(parsed) {
  const metricEls = parsed.metricVals.map((v) => ({ _text: v }));
  metricEls.forEach((el) => {
    Object.defineProperty(el, "textContent", {
      get() { return el._text; },
      set(v) { el._text = v; },
    });
  });
  let fillEl = null;
  if (parsed.fillWidth !== null) {
    fillEl = { style: { _width: parsed.fillWidth + "%" } };
    Object.defineProperty(fillEl.style, "width", {
      get() { return fillEl.style._width; },
      set(v) { fillEl.style._width = v; },
    });
  }
  return {
    dataset: { nodeId: parsed.nodeId },
    style: {},
    getBoundingClientRect() { return { top: 0, left: 0 }; },
    querySelectorAll(sel) { return sel === ".metric-val" ? metricEls : []; },
    querySelector(sel) { return sel === ".progress-fill" ? fillEl : null; },
  };
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
  return {
    metricVals: row.querySelectorAll(".metric-val").map((el) => el.textContent),
  };
}
"""


def _node(node_id, speed, distance, progress, equipment_type=None):
    node = {
        "node_id": node_id,
        "athlete_name": f"Athlete {node_id}",
        "power_watts": 100,
        "instantaneous_speed_kph": speed,
        "distance_m": distance,
        "progress_percent": progress,
    }
    if equipment_type is not None:
        node["equipment_type"] = equipment_type
    return node


def _run_classic(script_body: str) -> dict:
    script = (
        _classic_stubs()
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


def test_classic_full_render_curved_treadmill_shows_pace_not_speed():
    n1 = json.dumps(_node("n1", 10.909, 91, 60, equipment_type="curved_treadmill"))
    script = f"""
renderLeaderboard({{ n1: {n1} }});
console.log(JSON.stringify({{ html: leaderboardContainer.innerHTML }}));
"""
    result = _run_classic(script)
    html = result["html"]
    assert "5'30\"" in html, (
        "curved_treadmill row at 10.909 km/h must render the 5'30\" pace "
        f"text somewhere in the card, got: {html}"
    )
    assert "T[metric.pace]" in html, "the pace label (metric.pace) must be shown"
    assert (
        " kph" not in html
    ), "a running-equipment row must not show a kph-style speed value"


def test_classic_full_render_spin_bike_still_shows_speed():
    n1 = json.dumps(_node("n1", 10.909, 91, 60, equipment_type="spin_bike"))
    script = f"""
renderLeaderboard({{ n1: {n1} }});
console.log(JSON.stringify({{ html: leaderboardContainer.innerHTML }}));
"""
    result = _run_classic(script)
    html = result["html"]
    assert (
        "10.9" in html
    ), "a non-running-equipment row must still show the raw kph number"
    assert "T[metric.speed]" in html, "the speed label (metric.speed) must be shown"
    assert "5'30" not in html, "a bike row must never show pace text"


def test_classic_full_render_time_race_type_treadmill_shows_pace():
    """Same rule on the 'time' race-type branch (~line 5416), not just the
    default 'distance' branch (~line 5435)."""
    n1 = json.dumps(_node("n1", 10.909, 91, 60, equipment_type="treadmill"))
    script = f"""
currentConfig = {{ race_type: 'time' }};
renderLeaderboard({{ n1: {n1} }});
console.log(JSON.stringify({{ html: leaderboardContainer.innerHTML }}));
"""
    result = _run_classic(script)
    html = result["html"]
    assert "5'30\"" in html
    assert "T[metric.pace]" in html
    assert " kph" not in html


def test_classic_fast_path_treadmill_metric_a_is_pace_after_speed_change():
    """Fast path: after a speed change on a treadmill row, metricA text is
    the pace string; the smoothing key stays the same (`${node_id}:speed`)
    so values do not jump when the display format switches."""
    n1 = json.dumps(_node("n1", 12, 500, 50, equipment_type="treadmill"))
    n2 = json.dumps(_node("n1", 10.909, 520, 55, equipment_type="treadmill"))
    script = f"""
renderLeaderboard({{ n1: {n1} }});
const before = readCard("n1");
renderLeaderboard({{ n1: {n2} }});
const after = readCard("n1");
console.log(JSON.stringify({{ before, after }}));
"""
    result = _run_classic(script)
    assert result["before"]["metricVals"][0] == "5'00\""
    assert result["after"]["metricVals"][0] == "5'30\"", (
        "after a speed change on a treadmill row the fast path must patch "
        f"metricA to the new pace, got {result['after']['metricVals']}"
    )


def test_classic_fast_path_bike_metric_a_is_still_the_speed_number():
    n1 = json.dumps(_node("n1", 20, 500, 50, equipment_type="spin_bike"))
    n2 = json.dumps(_node("n1", 22, 520, 55, equipment_type="spin_bike"))
    script = f"""
renderLeaderboard({{ n1: {n1} }});
const before = readCard("n1");
renderLeaderboard({{ n1: {n2} }});
const after = readCard("n1");
console.log(JSON.stringify({{ before, after }}));
"""
    result = _run_classic(script)
    assert result["before"]["metricVals"][0] == "20.0"
    assert result["after"]["metricVals"][0] == "22.0"


def test_classic_signature_changes_when_equipment_type_changes():
    """A row's equipment_type flipping (e.g. a bike swapped for a
    treadmill) must invalidate the card signature so the fast path cannot
    leave the wrong metric label on screen."""
    n1_bike = _node("n1", 20, 500, 50, equipment_type="spin_bike")
    n1_treadmill = dict(n1_bike, equipment_type="treadmill")
    script = f"""
const sigBefore = buildLeaderboardCardSignature([{json.dumps(n1_bike)}], "distance", false);
const sigAfter = buildLeaderboardCardSignature([{json.dumps(n1_treadmill)}], "distance", false);
console.log(JSON.stringify({{ sigBefore, sigAfter }}));
"""
    result = _run_classic(script)
    assert result["sigBefore"] != result["sigAfter"]


def test_classic_equipment_type_change_triggers_a_full_rebuild():
    n1_bike = json.dumps(_node("n1", 20, 500, 50, equipment_type="spin_bike"))
    n1_treadmill = json.dumps(_node("n1", 20, 500, 50, equipment_type="treadmill"))
    script = f"""
renderLeaderboard({{ n1: {n1_bike} }});
const afterFirst = innerHTMLSetCount;
renderLeaderboard({{ n1: {n1_treadmill} }});
console.log(JSON.stringify({{ afterFirst, afterSecond: innerHTMLSetCount }}));
"""
    result = _run_classic(script)
    assert result["afterSecond"] == result["afterFirst"] + 1, (
        "an equipment_type change must trigger exactly one more full "
        f"rebuild, went from {result['afterFirst']} to {result['afterSecond']}"
    )


# ---------------------------------------------------------------------------
# 3. Class board -- full render (buildClassBoardHtml) and the incremental
#    patch fast path (applyClassBoardIncrementalUpdate). Harness mirrors
#    tests/unit/hub/test_dashboard_class_board_card_cache.py.
# ---------------------------------------------------------------------------

_CLASS_FN_NAMES = [
    "resetClassBoardCardCache",
    "classTargetStatusForPatch",
    "computeClassProgressPercentForPatch",
    "buildClassStationSignature",
    "captureClassBoardRefs",
    "applyClassBoardIncrementalUpdate",
    "renderClassBoardFromState",
    "buildClassBoardHtml",
    "classClockAt",
    "formatClock",
]


def _extract_class_fns() -> str:
    source = _read_index()
    return "\n".join(
        _strip_js_comments(_extract_function(source, name)) for name in _CLASS_FN_NAMES
    )


def _class_stubs() -> str:
    return (
        "const t = (key, params = {}) => { let value = `T[${key}]`; Object.entries(params).forEach(([n, r]) => { value = value.replaceAll(`{${n}}`, String(r)); }); return value; };\n"
        "const metricNumber = (value, fallback = 0) => { const n = Number(value); return Number.isFinite(n) ? n : fallback; };\n"
        "const escapeHtml = (value) => String(value == null ? '' : value);\n"
        "const nodeDisplayName = (node) => (node && (node.node_display_name || node.display_name || node.node_id)) || '--';\n"
        "const Intl = { NumberFormat: function(locale) { return { format: (n) => String(n) }; } };\n"
        "let classBoardCardRefs = null;\n"
        "let currentLocale = 'en-US';\n"
        "let currentClassPlan = null;\n"
        "let currentClassLeaderboard = {};\n"
        "let raceStartTime = null;\n"
        "let resetLeaderboardCardCache;\n"
    )


def _class_fake_dom() -> str:
    return r"""
let innerHTMLSetCount = 0;

function makeMutableText(initial) {
  const box = { _text: initial };
  Object.defineProperty(box, "textContent", {
    get() { return box._text; },
    set(v) { box._text = v; },
  });
  return box;
}

function makeMutableWidth(initialPercent) {
  const box = { style: { _width: initialPercent === null ? null : initialPercent + "%" } };
  Object.defineProperty(box.style, "width", {
    get() { return box.style._width; },
    set(v) { box.style._width = v; },
  });
  return box;
}

function parseCards(html) {
  const openRe = /<div data-node-id="([^"]*)" style="/g;
  const opens = [];
  let m;
  while ((m = openRe.exec(html))) {
    opens.push({ nodeId: m[1], start: m.index });
  }
  return opens.map((o, i) => {
    const end = i + 1 < opens.length ? opens[i + 1].start : html.length;
    const segment = html.slice(o.start, end);
    const power = segment.match(/class="class-metric-power">([^<]*)</);
    const speed = segment.match(/class="class-metric-speed" style="[^"]*">([^<]*)</);
    const distance = segment.match(/class="class-metric-distance" style="[^"]*">([^<]*)</);
    const label = [...segment.matchAll(/class="class-metric-label" style="[^"]*">([^<]*)</g)].map((mm) => mm[1]);
    return {
      nodeId: o.nodeId,
      power: power ? power[1] : null,
      speed: speed ? speed[1] : null,
      distance: distance ? distance[1] : null,
      labels: label,
    };
  });
}

function makeContainer() {
  let html = "";
  let cardEls = [];

  const container = {
    querySelectorAll(sel) { return sel === "[data-node-id]" ? cardEls : []; },
    querySelector() { return null; },
  };

  Object.defineProperty(container, "innerHTML", {
    get() { return html; },
    set(value) {
      html = value;
      innerHTMLSetCount += 1;
      const parsedCards = parseCards(value);
      // One persistent textContent box per field, created once and reused
      // for every querySelector call on this card -- captureClassBoardRefs
      // and a later read must see the SAME mutable object, or a patch
      // applied through refs.speed.textContent would be invisible to a
      // test reading the card afterwards (querySelector re-parsing the
      // static HTML string on every call, and so never observing the
      // patch, was exactly this harness's first bug).
      cardEls = parsedCards.map((c) => {
        const boxes = {
          ".class-metric-power": makeMutableText(c.power),
          ".class-metric-speed": makeMutableText(c.speed),
          ".class-metric-distance": makeMutableText(c.distance),
          ".class-effort-fill": makeMutableWidth(null),
        };
        return {
          dataset: { nodeId: c.nodeId },
          _labels: c.labels,
          querySelector(sel) { return boxes[sel] || null; },
        };
      });
    },
  });

  return container;
}

const leaderboardContainer = makeContainer();
const document = {
  getElementById(id) { return id === "leaderboard-container" ? leaderboardContainer : null; },
};

function readBoardCards() {
  return leaderboardContainer.querySelectorAll("[data-node-id]").map((card) => ({
    nodeId: card.dataset.nodeId,
    power: card.querySelector(".class-metric-power").textContent,
    speed: card.querySelector(".class-metric-speed").textContent,
    distance: card.querySelector(".class-metric-distance").textContent,
    labels: card._labels,
  }));
}
"""


def _class_station(node_id, station_number, speed, equipment_type=None):
    station = {
        "node_id": node_id,
        "station_number": station_number,
        "athlete_name": f"Athlete {node_id}",
        "power_watts": 100,
        "instantaneous_speed_kph": speed,
        "distance_m": 500,
    }
    if equipment_type is not None:
        station["equipment_type"] = equipment_type
    return station


_CLASS_PLAN = {"segments": [{"kind": "work", "duration_sec": 600}]}


def _run_class(script_body: str) -> dict:
    script = (
        _class_stubs() + _class_fake_dom() + _extract_class_fns() + "\n" + script_body
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def test_class_board_treadmill_card_shows_pace():
    station = json.dumps(_class_station("n1", 1, 10.909, equipment_type="treadmill"))
    script = f"""
currentClassPlan = {json.dumps(_CLASS_PLAN)};
raceStartTime = 0;
currentClassLeaderboard = {{ n1: {station} }};
renderClassBoardFromState();
const cards = readBoardCards();
console.log(JSON.stringify({{ cards }}));
"""
    result = _run_class(script)
    card = result["cards"][0]
    assert card["speed"] == "5'30\"", f"expected pace text, got {card['speed']}"
    assert "T[metric.pace]" in card["labels"]
    assert "T[metric.speed]" not in card["labels"]


def test_class_board_bike_card_shows_kph():
    station = json.dumps(_class_station("n1", 1, 10.909, equipment_type="fan_bike"))
    script = f"""
currentClassPlan = {json.dumps(_CLASS_PLAN)};
raceStartTime = 0;
currentClassLeaderboard = {{ n1: {station} }};
renderClassBoardFromState();
const cards = readBoardCards();
console.log(JSON.stringify({{ cards }}));
"""
    result = _run_class(script)
    card = result["cards"][0]
    assert card["speed"] == "10.909 kph", f"expected kph text, got {card['speed']}"
    assert "T[metric.speed]" in card["labels"]
    assert "T[metric.pace]" not in card["labels"]


def test_class_board_fast_path_treadmill_speed_cell_patches_to_pace():
    """The incremental patch path (applyClassBoardIncrementalUpdate) must
    also write pace text into an unchanged treadmill card's speed cell on
    an ordinary tick, not just the initial full render."""
    station_1 = json.dumps(_class_station("n1", 1, 12, equipment_type="treadmill"))
    station_2 = json.dumps(_class_station("n1", 1, 10.909, equipment_type="treadmill"))
    script = f"""
currentClassPlan = {json.dumps(_CLASS_PLAN)};
raceStartTime = 0;
currentClassLeaderboard = {{ n1: {station_1} }};
renderClassBoardFromState();
const beforeRebuilds = innerHTMLSetCount;
const before = readBoardCards()[0].speed;
currentClassLeaderboard = {{ n1: {station_2} }};
renderClassBoardFromState();
const afterRebuilds = innerHTMLSetCount;
const after = readBoardCards()[0].speed;
console.log(JSON.stringify({{ before, after, beforeRebuilds, afterRebuilds }}));
"""
    result = _run_class(script)
    assert result["afterRebuilds"] == result["beforeRebuilds"], (
        "an ordinary speed-only tick on an unchanged station set must take "
        "the fast path, not rebuild"
    )
    assert result["before"] == "5'00\""
    assert result["after"] == "5'30\""


# ---------------------------------------------------------------------------
# 4. Sprint board -- formatSprintSignal. Harness mirrors
#    tests/unit/hub/test_dashboard_i18n.py's _run_format_sprint_signal.
# ---------------------------------------------------------------------------


def _run_format_sprint_signal(row_js: str, race_type: str) -> dict:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "formatSprintSignal"))
    script = (
        "const t = (key) => `T[${key}]`;\n"
        "const metricNumber = (value, fallback = 0) => { const n = Number(value); return Number.isFinite(n) ? n : fallback; };\n"
        # formatSprintSignal now also gates the pace-band field (feat/
        # pace-effects) on the page-global currentState -- not exercised by
        # this file's assertions (value/label only), so a plain non-RUNNING
        # default is enough to satisfy the reference.
        "let currentState = 'IDLE';\n"
        + fn
        + "\n"
        + f"console.log(JSON.stringify(formatSprintSignal({row_js}, {json.dumps(race_type)})));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout)


def test_sprint_board_treadmill_row_shows_pace():
    result = _run_format_sprint_signal(
        '{kind: "individual", raw: {instantaneous_speed_kph: 10.909, equipment_type: "curved_treadmill"}}',
        "distance",
    )
    assert result["value"] == "5'30\""
    assert result["label"] == "T[metric.pace]"


def test_sprint_board_bike_row_still_shows_speed():
    result = _run_format_sprint_signal(
        '{kind: "individual", raw: {instantaneous_speed_kph: 10.909, equipment_type: "spin_bike"}}',
        "distance",
    )
    assert result["value"] == "10.9"
    assert result["label"] == "T[metric.speed]"


def test_sprint_board_team_avg_speed_label_unaffected():
    """Team aggregate labels (team.avg_speed_label) are explicitly out of
    scope -- even a team whose members are all treadmills keeps the plain
    average-kph display, since a team score has no single equipment_type."""
    result = _run_format_sprint_signal(
        '{kind: "team", raw: {members: [{elapsed_time_ms: 60000, distance_m: 200}], member_count: 1}}',
        "distance",
    )
    assert result["label"] == "T[team.avg_speed_label]"


# ---------------------------------------------------------------------------
# 5. Locale coverage -- metric.pace exists (with the exact requested
#    copy) in every one of the six locale files, verified through
#    hub_server.infrastructure.locales the same way
#    tests/unit/hub/test_i18n_locales.py checks other dashboard keys.
# ---------------------------------------------------------------------------


def test_metric_pace_key_present_and_distinct_in_every_locale():
    for locale in ("zh-TW", "en-US", "de-CH", "fr", "it", "sv"):
        messages = load_locale(locale)["messages"]
        assert "metric.pace" in messages, f"{locale} is missing metric.pace"
        assert (
            messages["metric.pace"] != messages["metric.speed"]
        ), f"{locale} metric.pace must not be a copy of metric.speed"


def test_metric_pace_zh_tw_and_en_us_translations():
    assert load_locale("zh-TW")["messages"]["metric.pace"] == "配速 (/km)"
    assert load_locale("en-US")["messages"]["metric.pace"] == "Pace (/km)"
