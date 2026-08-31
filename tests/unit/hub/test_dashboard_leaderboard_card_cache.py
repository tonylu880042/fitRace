"""Tests for the classic leaderboard's per-card DOM cache.

Root-cause context (amended target: a venue that reaches twenty equipment
streams, not six): renderLeaderboard used to rebuild the ENTIRE
#leaderboard-container innerHTML from scratch on every render, and captured
a getBoundingClientRect() for every existing row both before and after that
rebuild (for the rank-reorder FLIP animation). That cost is O(station
count) on every single telemetry tick -- fine at two machines, expensive at
six, and the dominant cost at twenty, since the vast majority of ticks
change only power/speed/distance/progress numbers, never who is racing or
in what order.

The fix: renderLeaderboard now builds a "card signature" from everything
that actually needs new markup (race type, podium/final state, and per-node
identity fields -- name, team, station, machine, avatar, and whether this
node has just finished). When a new render's signature exactly matches the
signature of the last full render, and the DOM element cache has one entry
per node, it patches the three metric-value elements and the progress-fill
width of each already-rendered row directly (see
updateLeaderboardCardValues) instead of rebuilding markup, touching
innerHTML, or reading any element's position. A full rebuild remains the
fallback whenever the signature differs -- which is comparatively rare
(join/leave, reorder, finish, or race becoming final).

These tests extract the real renderLeaderboard and its new cache helpers
from index.html (same brace-depth technique used throughout
tests/unit/hub/) and drive them against a minimal fake DOM built to support
exactly the operations these functions use: innerHTML get/set,
querySelectorAll(".leaderboard-item" | ".metric-val"),
querySelector(".progress-fill"), and a getBoundingClientRect() spy. Real
browser DOM libraries are not available under plain node in this
environment, so the fake purposefully mirrors only the shapes
renderLeaderboard's classic-individual branch actually emits.
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
    "resetLeaderboardCardCache",
    "buildLeaderboardCardSignature",
    "captureLeaderboardCardRefs",
    "setSmoothedCardText",
    "updateLeaderboardCardValues",
    "renderLeaderboard",
    "sortLeaderboardNodes",
    "isLeaderboardFinal",
]


def _extract_all_fns() -> str:
    source = _read_index()
    return "\n".join(
        _strip_js_comments(_extract_function(source, name)) for name in _FN_NAMES
    )


def _stubs() -> str:
    return (
        "const t = (key) => `T[${key}]`;\n"
        "const metricNumber = (value, fallback = 0) => { const n = Number(value); return Number.isFinite(n) ? n : fallback; };\n"
        "const escapeHtml = (value) => String(value == null ? '' : value);\n"
        "const nodeDisplayName = (node) => (node && (node.node_display_name || node.display_name || node.node_id)) || '--';\n"
        "let leaderboardSmoothValues = new Map();\n"
        "function smoothMetricNumber(key, targetValue) { const target = metricNumber(targetValue); leaderboardSmoothValues.set(key, target); return target; }\n"
        "let animateLeaderboardReorderCalls = 0;\n"
        "function animateLeaderboardReorder() { animateLeaderboardReorderCalls += 1; }\n"
        "function detectAthleteFinishes() {}\n"
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


def _fake_dom() -> str:
    """A minimal fake DOM supporting exactly the operations
    renderLeaderboard's classic branch and the card-cache helpers use:
    innerHTML get/set on the container, querySelectorAll(".leaderboard-item"),
    row.querySelectorAll(".metric-val"), row.querySelector(".progress-fill"),
    and a getBoundingClientRect() call counter."""
    return r"""
let innerHTMLSetCount = 0;
let getBoundingClientRectCallCount = 0;

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
    getBoundingClientRect() { getBoundingClientRectCallCount += 1; return { top: 0, left: 0 }; },
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
    fillWidth: (row.querySelector(".progress-fill") || {}).style && row.querySelector(".progress-fill").style.width,
  };
}
"""


def _node(node_id, power, speed, distance, progress):
    return {
        "node_id": node_id,
        "athlete_name": f"Athlete {node_id}",
        "power_watts": power,
        "instantaneous_speed_kph": speed,
        "distance_m": distance,
        "progress_percent": progress,
    }


def _run(script_body: str) -> dict:
    script = _stubs() + _fake_dom() + _extract_all_fns() + "\n" + script_body
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def test_unchanged_station_set_and_order_does_not_rewrite_innerhtml():
    """Two renders with the same nodes, same order, same identity fields --
    only numeric values differ -- must write innerHTML exactly once (the
    first render). This is the O(1)-per-tick property the fix exists for."""
    n1 = json.dumps(_node("n1", 100, 20, 500, 50))
    n2 = json.dumps(_node("n1", 150, 22, 520, 55))
    script = f"""
renderLeaderboard({{ n1: {n1} }});
const afterFirst = innerHTMLSetCount;
renderLeaderboard({{ n1: {n2} }});
console.log(JSON.stringify({{ afterFirst, afterSecond: innerHTMLSetCount }}));
"""
    result = _run(script)
    assert result["afterFirst"] == 1
    assert result["afterSecond"] == 1, (
        "a second render with the same station set/order must not touch "
        f"innerHTML again, but the count went from 1 to {result['afterSecond']}"
    )


def test_unchanged_station_set_and_order_calls_no_getBoundingClientRect():
    n1 = json.dumps(_node("n1", 100, 20, 500, 50))
    n2 = json.dumps(_node("n1", 150, 22, 520, 55))
    script = f"""
renderLeaderboard({{ n1: {n1} }});
const afterFirst = getBoundingClientRectCallCount;
renderLeaderboard({{ n1: {n2} }});
console.log(JSON.stringify({{ afterFirst, afterSecond: getBoundingClientRectCallCount }}));
"""
    result = _run(script)
    assert result["afterSecond"] == result["afterFirst"], (
        "an ordinary value-only tick must call getBoundingClientRect zero "
        f"additional times, went from {result['afterFirst']} to {result['afterSecond']}"
    )


def test_unchanged_station_set_patches_numeric_text_in_place():
    """The fast path must actually update the displayed numbers, not just
    skip work -- prove the cached metric-val/progress-fill elements reflect
    the SECOND call's values, not the first."""
    n1 = json.dumps(_node("n1", 100, 20.0, 500, 50.0))
    n2 = json.dumps(_node("n1", 150, 22.0, 520, 55.0))
    script = f"""
renderLeaderboard({{ n1: {n1} }});
const before = readCard("n1");
renderLeaderboard({{ n1: {n2} }});
const after = readCard("n1");
console.log(JSON.stringify({{ before, after }}));
"""
    result = _run(script)
    # metricA=speed, metricB=distance, metricC=progress for the default
    # (distance) race type -- see updateLeaderboardCardValues.
    assert result["before"]["metricVals"] == ["20.0", "500", "50.0%"]
    assert result["after"]["metricVals"] == ["22.0", "520", "55.0%"]
    assert result["before"]["fillWidth"] == "50%"
    assert result["after"]["fillWidth"] == "55%"


def test_new_station_joining_triggers_a_full_rebuild():
    """A station set change (someone joins) must invalidate the fast path
    -- the new station needs real markup, not a patch onto a nonexistent
    row."""
    n1 = json.dumps(_node("n1", 100, 20, 500, 50))
    two_nodes = json.dumps(
        {"n1": _node("n1", 100, 20, 500, 50), "n2": _node("n2", 90, 18, 400, 40)}
    )
    script = f"""
renderLeaderboard({{ n1: {n1} }});
const afterFirst = innerHTMLSetCount;
renderLeaderboard({two_nodes});
console.log(JSON.stringify({{ afterFirst, afterSecond: innerHTMLSetCount }}));
"""
    result = _run(script)
    assert result["afterSecond"] == result["afterFirst"] + 1, (
        "a station joining must trigger exactly one more full rebuild, "
        f"went from {result['afterFirst']} to {result['afterSecond']}"
    )


def test_athlete_name_change_triggers_a_full_rebuild():
    """Identity fields baked into markup (here: athlete_name) must also
    invalidate the fast path, not just the station set/order -- a stale
    cached row would otherwise keep showing the old name forever."""
    n1 = _node("n1", 100, 20, 500, 50)
    n1_renamed = dict(n1)
    n1_renamed["athlete_name"] = "Renamed Athlete"
    script = f"""
renderLeaderboard({{ n1: {json.dumps(n1)} }});
const afterFirst = innerHTMLSetCount;
renderLeaderboard({{ n1: {json.dumps(n1_renamed)} }});
console.log(JSON.stringify({{ afterFirst, afterSecond: innerHTMLSetCount }}));
"""
    result = _run(script)
    assert result["afterSecond"] == result["afterFirst"] + 1


def test_finishing_triggers_a_full_rebuild():
    """A node transitioning to finished swaps its progress percent for a
    finish-time display -- markup, not just a number -- so it must
    invalidate the fast path."""
    n1 = _node("n1", 100, 20, 500, 90)
    n1_finished = dict(n1)
    n1_finished["finished_time_ms"] = 12345
    script = f"""
renderLeaderboard({{ n1: {json.dumps(n1)} }});
const afterFirst = innerHTMLSetCount;
renderLeaderboard({{ n1: {json.dumps(n1_finished)} }});
console.log(JSON.stringify({{ afterFirst, afterSecond: innerHTMLSetCount }}));
"""
    result = _run(script)
    assert result["afterSecond"] == result["afterFirst"] + 1


def test_repeated_unchanged_renders_keep_a_single_rebuild():
    """A longer burst -- ten consecutive value-only ticks -- must still
    only ever have rebuilt once. Guards against an off-by-one that only
    happens to pass for exactly two calls."""
    payloads = [
        json.dumps({"n1": _node("n1", 100 + i, 20 + i, 500 + i, min(90, 50 + i))})
        for i in range(10)
    ]
    calls = "\n".join(f"renderLeaderboard({p});" for p in payloads)
    script = f"""
{calls}
console.log(JSON.stringify({{ innerHTMLSetCount }}));
"""
    result = _run(script)
    assert result["innerHTMLSetCount"] == 1
