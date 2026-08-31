"""Tests for the class board's per-station DOM cache.

Root-cause context (amended target: a venue reaching twenty equipment
streams): renderClassBoardFromState used to call buildClassBoardHtml -- a
full string-concatenated rebuild of the whole board -- on every single
call, and it is called from TWO sources: the untyped-telemetry WS branch
(coalesced to at most one render per animation frame by an earlier commit,
but still a real render on essentially every burst) and a 250ms
setInterval clock tick that runs independently of telemetry. At twenty
stations that is an O(station count) rebuild roughly four times a second
minimum, even when nothing but the countdown changed.

The fix: renderClassBoardFromState now tries
applyClassBoardIncrementalUpdate first. That function patches each
station's power/speed/distance text and effort-bar width, plus the hero
countdown, total-remaining text, and overall progress-fill width, directly
from a cached element map -- no markup regeneration, no innerHTML write --
whenever the station set, the current segment, and each station's
target-met status (whose indicator element only exists in the DOM when
"on" or "under") all match the last full render. A full rebuild remains
the fallback for the cases that actually need new markup: a station
joining/leaving, a segment change, the plan finishing, or a station's
target status flipping.

These tests extract the real functions from index.html (same brace-depth
technique used throughout tests/unit/hub/) and drive them against a
minimal fake DOM built to support exactly what these functions touch:
innerHTML get/set, querySelectorAll("[data-node-id]"), and
querySelector(...) for the class-metric-*/class-effort-fill/class-hero-*/
class-progress-fill hooks. A real browser DOM library is not available
under plain node in this environment.
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


_FN_NAMES = [
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


def _extract_all_fns() -> str:
    source = _read_index()
    return "\n".join(
        _strip_js_comments(_extract_function(source, name)) for name in _FN_NAMES
    )


def _stubs() -> str:
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
        "let resetLeaderboardCardCache;\n"  # left undefined on purpose: exercises the typeof guard
    )


def _fake_dom() -> str:
    return r"""
let innerHTMLSetCount = 0;

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
    const effort = segment.match(/class="class-effort-fill" style="height:100%;width:([^%]+)%/);
    return {
      nodeId: o.nodeId,
      power: power ? power[1] : null,
      speed: speed ? speed[1] : null,
      distance: distance ? distance[1] : null,
      effortWidth: effort ? effort[1] : null,
    };
  });
}

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

function makeContainer() {
  let html = "";
  let cardEls = [];
  let countdownEl = null;
  let totalRemainingEl = null;
  let progressFillEl = null;

  const container = {
    querySelectorAll(sel) { return sel === "[data-node-id]" ? cardEls : []; },
    querySelector(sel) {
      if (sel === ".class-hero-countdown") return countdownEl;
      if (sel === ".class-hero-total-remaining") return totalRemainingEl;
      if (sel === ".class-progress-fill") return progressFillEl;
      return null;
    },
  };

  Object.defineProperty(container, "innerHTML", {
    get() { return html; },
    set(value) {
      html = value;
      innerHTMLSetCount += 1;
      const parsedCards = parseCards(value);
      cardEls = parsedCards.map((c) => {
        const powerEl = c.power === null ? null : makeMutableText(c.power);
        const speedEl = c.speed === null ? null : makeMutableText(c.speed);
        const distanceEl = c.distance === null ? null : makeMutableText(c.distance);
        const effortEl = c.effortWidth === null ? null : makeMutableWidth(c.effortWidth);
        return {
          dataset: { nodeId: c.nodeId },
          querySelector(sel) {
            if (sel === ".class-metric-power") return powerEl;
            if (sel === ".class-metric-speed") return speedEl;
            if (sel === ".class-metric-distance") return distanceEl;
            if (sel === ".class-effort-fill") return effortEl;
            return null;
          },
        };
      });
      const countdownMatch = value.match(/class="class-hero-countdown" style="[^"]*">([^<]*)</);
      countdownEl = countdownMatch ? makeMutableText(countdownMatch[1]) : null;
      const totalRemainingMatch = value.match(/class="class-hero-total-remaining" style="[^"]*">([^<]*)</);
      totalRemainingEl = totalRemainingMatch ? makeMutableText(totalRemainingMatch[1]) : null;
      const progressFillMatch = value.match(/class="class-progress-fill" style="height:100%;width:([^%]+)%/);
      progressFillEl = progressFillMatch ? makeMutableWidth(progressFillMatch[1]) : null;
    },
  });

  return container;
}

const leaderboardContainer = makeContainer();
const document = {
  getElementById(id) { return id === "leaderboard-container" ? leaderboardContainer : null; },
};

function readBoard() {
  return {
    cards: leaderboardContainer.querySelectorAll("[data-node-id]").map((card) => ({
      nodeId: card.dataset.nodeId,
      power: card.querySelector(".class-metric-power") && card.querySelector(".class-metric-power").textContent,
      speed: card.querySelector(".class-metric-speed") && card.querySelector(".class-metric-speed").textContent,
      distance: card.querySelector(".class-metric-distance") && card.querySelector(".class-metric-distance").textContent,
      effortWidth: card.querySelector(".class-effort-fill") && card.querySelector(".class-effort-fill").style.width,
    })),
    countdown: leaderboardContainer.querySelector(".class-hero-countdown") && leaderboardContainer.querySelector(".class-hero-countdown").textContent,
    totalRemaining: leaderboardContainer.querySelector(".class-hero-total-remaining") && leaderboardContainer.querySelector(".class-hero-total-remaining").textContent,
    progressWidth: leaderboardContainer.querySelector(".class-progress-fill") && leaderboardContainer.querySelector(".class-progress-fill").style.width,
  };
}
"""


_PLAN = {"segments": [{"kind": "work", "duration_sec": 600}]}


def _station(node_id, station_number, power, speed, distance):
    return {
        "node_id": node_id,
        "station_number": station_number,
        "athlete_name": f"Athlete {node_id}",
        "power_watts": power,
        "instantaneous_speed_kph": speed,
        "distance_m": distance,
    }


def _run(script_body: str) -> dict:
    script = _stubs() + _fake_dom() + _extract_all_fns() + "\n" + script_body
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def test_unchanged_station_set_does_not_rewrite_innerhtml():
    """Two ticks with the same station, same segment -- only power/speed/
    distance differ -- must write innerHTML exactly once."""
    script = f"""
currentClassPlan = {json.dumps(_PLAN)};
raceStartTime = 0;
currentClassLeaderboard = {{ n1: {json.dumps(_station("n1", 1, 100, 20, 500))} }};
renderClassBoardFromState();
const afterFirst = innerHTMLSetCount;
currentClassLeaderboard = {{ n1: {json.dumps(_station("n1", 1, 150, 24, 540))} }};
renderClassBoardFromState();
console.log(JSON.stringify({{ afterFirst, afterSecond: innerHTMLSetCount }}));
"""
    result = _run(script)
    assert result["afterFirst"] == 1
    assert result["afterSecond"] == 1, (
        "an ordinary telemetry tick with the same station set must not "
        f"rebuild the board again, went from 1 to {result['afterSecond']}"
    )


def test_unchanged_station_set_patches_numbers_in_place():
    script = f"""
currentClassPlan = {json.dumps(_PLAN)};
raceStartTime = 0;
currentClassLeaderboard = {{ n1: {json.dumps(_station("n1", 1, 100, 20, 500))} }};
renderClassBoardFromState();
const before = readBoard();
currentClassLeaderboard = {{ n1: {json.dumps(_station("n1", 1, 150, 24, 540))} }};
renderClassBoardFromState();
const after = readBoard();
console.log(JSON.stringify({{ before, after }}));
"""
    result = _run(script)
    assert result["before"]["cards"][0]["power"] == "100 W"
    assert result["after"]["cards"][0]["power"] == "150 W"
    assert result["before"]["cards"][0]["speed"] == "20 kph"
    assert result["after"]["cards"][0]["speed"] == "24 kph"
    assert result["before"]["cards"][0]["distance"] == "500 m"
    assert result["after"]["cards"][0]["distance"] == "540 m"
    assert result["before"]["cards"][0]["effortWidth"] == "20%"
    assert result["after"]["cards"][0]["effortWidth"] == "30%"


def test_countdown_and_total_remaining_tick_without_a_rebuild():
    """The 250ms class clock tick must still visibly count down even
    though it takes the fast path -- proving the countdown patch actually
    runs, not merely that no rebuild happened."""
    station = json.dumps(_station("n1", 1, 100, 20, 500))
    script = f"""
currentClassPlan = {json.dumps(_PLAN)};
currentClassLeaderboard = {{ n1: {station} }};
raceStartTime = Date.now();
renderClassBoardFromState();
const before = readBoard();
const afterFirst = innerHTMLSetCount;
raceStartTime = Date.now() - 5000;
renderClassBoardFromState();
const after = readBoard();
console.log(JSON.stringify({{ before, after, afterFirst, afterSecond: innerHTMLSetCount }}));
"""
    result = _run(script)
    assert result["afterSecond"] == result["afterFirst"]
    assert (
        result["before"]["countdown"] != result["after"]["countdown"]
    ), "the countdown must have ticked between the two calls"
    assert result["before"]["totalRemaining"] != result["after"]["totalRemaining"]


def test_new_station_joining_triggers_a_full_rebuild():
    station1 = json.dumps(_station("n1", 1, 100, 20, 500))
    two = json.dumps(
        {"n1": _station("n1", 1, 100, 20, 500), "n2": _station("n2", 2, 90, 18, 400)}
    )
    script = f"""
currentClassPlan = {json.dumps(_PLAN)};
raceStartTime = 0;
currentClassLeaderboard = {{ n1: {station1} }};
renderClassBoardFromState();
const afterFirst = innerHTMLSetCount;
currentClassLeaderboard = {two};
renderClassBoardFromState();
console.log(JSON.stringify({{ afterFirst, afterSecond: innerHTMLSetCount }}));
"""
    result = _run(script)
    assert result["afterSecond"] == result["afterFirst"] + 1


def test_segment_change_triggers_a_full_rebuild():
    """A tick that has crossed into the next segment must rebuild, even
    with the same station set -- the hero band, timeline, and target
    indicator all depend on the segment."""
    two_segment_plan = {
        "segments": [
            {"kind": "warmup", "duration_sec": 5},
            {"kind": "work", "duration_sec": 600},
        ]
    }
    station = json.dumps(_station("n1", 1, 100, 20, 500))
    script = f"""
currentClassPlan = {json.dumps(two_segment_plan)};
currentClassLeaderboard = {{ n1: {station} }};
raceStartTime = Date.now();
renderClassBoardFromState();
const afterFirst = innerHTMLSetCount;
raceStartTime = Date.now() - 6000;
renderClassBoardFromState();
console.log(JSON.stringify({{ afterFirst, afterSecond: innerHTMLSetCount }}));
"""
    result = _run(script)
    assert result["afterSecond"] == result["afterFirst"] + 1


def test_target_status_change_triggers_a_full_rebuild():
    """The on/under-target indicator only exists in the DOM when a status
    applies -- a station crossing the target threshold must invalidate
    the fast path, not silently keep a stale (or missing) indicator."""
    plan_with_target = {
        "segments": [{"kind": "work", "duration_sec": 600, "target_watts": 120}]
    }
    below = json.dumps(_station("n1", 1, 100, 20, 500))
    above = json.dumps(_station("n1", 1, 150, 20, 500))
    script = f"""
currentClassPlan = {json.dumps(plan_with_target)};
raceStartTime = 0;
currentClassLeaderboard = {{ n1: {below} }};
renderClassBoardFromState();
const afterFirst = innerHTMLSetCount;
currentClassLeaderboard = {{ n1: {above} }};
renderClassBoardFromState();
console.log(JSON.stringify({{ afterFirst, afterSecond: innerHTMLSetCount }}));
"""
    result = _run(script)
    assert result["afterSecond"] == result["afterFirst"] + 1


def test_repeated_unchanged_ticks_keep_a_single_rebuild():
    stations = [
        json.dumps({"n1": _station("n1", 1, 100 + i, 20 + i, 500 + i)})
        for i in range(10)
    ]
    calls = "\n".join(
        f"currentClassLeaderboard = {s}; renderClassBoardFromState();" for s in stations
    )
    script = f"""
currentClassPlan = {json.dumps(_PLAN)};
raceStartTime = 0;
{calls}
console.log(JSON.stringify({{ innerHTMLSetCount }}));
"""
    result = _run(script)
    assert result["innerHTMLSetCount"] == 1
