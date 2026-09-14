"""Race boards must scale up when only a handful of stations are racing.

Venues often race just 1-4 machines. With so few rows the classic
leaderboard, the race track view and the sprint board all sit as a thin
strip at the top of the projector, with small type. This mirrors the
class board density mechanism (test_dashboard_class_board_density.py):
a pure function picks a tier purely from the row count, CSS expresses
what each tier looks like, and no DOM measurement or extra state is
involved.

`raceBoardDensityTier(rowCount)` returns "xl" for 1-2 rows, "lg" for 3-4
rows, and "" (no tier -- render exactly as today) for 0 or 5+ rows. The
three individual-row renderers (renderLeaderboard's classic branch,
renderRaceTrackLeaderboard, renderSprintBoardLeaderboard) apply the tier
as an extra class on their rendered board root (.leaderboard-list,
.race-track-list, .sprint-board-grid respectively). Team Battle, the team
leaderboard, the class board and the podium/finish overlays are untouched.

Since the tier is purely a function of row count, and the classic
leaderboard already invalidates its card-signature fast path whenever the
node set (and therefore its length) changes, a tier change always lands
on a full rebuild -- and an ordinary numeric-only tick on an unchanged
row count still takes the fast path and keeps whatever tier class was
already on the root.
"""

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
    return source[start : _matching_brace_end(source, brace_open) + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout


# ---------------------------------------------------------------------------
# raceBoardDensityTier: pure, no DOM.
# ---------------------------------------------------------------------------


def _tier(row_count: int) -> str:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "raceBoardDensityTier"))
    out = _run_node(
        fn + f"\nconsole.log(JSON.stringify(raceBoardDensityTier({row_count})));"
    )
    import json

    return json.loads(out)


def test_zero_rows_has_no_tier():
    assert _tier(0) == ""


def test_one_row_is_xl():
    assert _tier(1) == "xl"


def test_two_rows_is_xl():
    assert _tier(2) == "xl"


def test_three_rows_is_lg():
    assert _tier(3) == "lg"


def test_four_rows_is_lg():
    assert _tier(4) == "lg"


def test_five_rows_has_no_tier():
    assert _tier(5) == ""


def test_twenty_four_rows_has_no_tier():
    assert _tier(24) == ""


# ---------------------------------------------------------------------------
# Classic renderLeaderboard: real card-cache fast path + real tier function,
# driven against the same minimal fake DOM used by
# test_dashboard_leaderboard_card_cache.py (innerHTML get/set,
# querySelectorAll(".leaderboard-item" | ".metric-val"),
# querySelector(".progress-fill")).
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
"""


def _extract_classic_fns() -> str:
    source = _read_index()
    return "\n".join(
        _strip_js_comments(_extract_function(source, name))
        for name in _CLASSIC_FN_NAMES
    )


def _classic_node(
    node_id, station_number, power=100, speed=20, distance=300, progress=10
):
    return {
        "node_id": node_id,
        "station_number": station_number,
        "athlete_name": f"Athlete {station_number}",
        "power_watts": power,
        "instantaneous_speed_kph": speed,
        "distance_m": distance,
        "progress_percent": progress,
    }


def _run_classic(script_body: str) -> dict:
    import json

    script = (
        _classic_stubs()
        + _classic_fake_dom()
        + _extract_classic_fns()
        + "\n"
        + script_body
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


def _leaderboard_list_tag(html: str) -> str:
    match = re.search(r'<div[^>]*class="[^"]*leaderboard-list[^"]*"[^>]*>', html)
    assert (
        match
    ), "classic leaderboard has no element carrying the leaderboard-list class"
    return match.group(0)


def test_classic_two_rows_carries_the_xl_class():
    import json

    nodes = {"n1": _classic_node("n1", 1), "n2": _classic_node("n2", 2)}
    script = f"""
renderLeaderboard({json.dumps(nodes)});
console.log(JSON.stringify({{ html: leaderboardContainer.innerHTML }}));
"""
    result = _run_classic(script)
    tag = _leaderboard_list_tag(result["html"])
    assert "race-board--xl" in tag
    assert "race-board--lg" not in tag


def test_classic_four_rows_carries_the_lg_class():
    import json

    nodes = {f"n{i}": _classic_node(f"n{i}", i) for i in range(1, 5)}
    script = f"""
renderLeaderboard({json.dumps(nodes)});
console.log(JSON.stringify({{ html: leaderboardContainer.innerHTML }}));
"""
    result = _run_classic(script)
    tag = _leaderboard_list_tag(result["html"])
    assert "race-board--lg" in tag
    assert "race-board--xl" not in tag


def test_classic_six_rows_carries_no_tier_class():
    import json

    nodes = {f"n{i}": _classic_node(f"n{i}", i) for i in range(1, 7)}
    script = f"""
renderLeaderboard({json.dumps(nodes)});
console.log(JSON.stringify({{ html: leaderboardContainer.innerHTML }}));
"""
    result = _run_classic(script)
    tag = _leaderboard_list_tag(result["html"])
    assert "race-board--xl" not in tag
    assert "race-board--lg" not in tag


def test_classic_numeric_only_tick_on_two_rows_keeps_the_xl_class_and_fast_path():
    """A tick that only changes live numbers on the same two stations must
    still take the card-cache fast path (no second innerHTML rebuild) and
    the root must still carry the xl class from the first render, since
    the fast path never touches the root markup at all."""
    n1 = _classic_node("n1", 1, power=100, speed=20, distance=300, progress=10)
    n2 = _classic_node("n2", 2, power=90, speed=18, distance=280, progress=8)
    n1_tick = dict(n1, power=150, speed=22, distance=320, progress=15)
    n2_tick = dict(n2, power=140, speed=19, distance=300, progress=12)
    import json

    script = f"""
renderLeaderboard({json.dumps({"n1": n1, "n2": n2})});
const afterFirst = innerHTMLSetCount;
renderLeaderboard({json.dumps({"n1": n1_tick, "n2": n2_tick})});
console.log(JSON.stringify({{ afterFirst, afterSecond: innerHTMLSetCount, html: leaderboardContainer.innerHTML }}));
"""
    result = _run_classic(script)
    assert result["afterSecond"] == result["afterFirst"], (
        "an ordinary value-only tick on the same two stations must not "
        f"trigger another rebuild, went from {result['afterFirst']} to {result['afterSecond']}"
    )
    tag = _leaderboard_list_tag(result["html"])
    assert "race-board--xl" in tag


# ---------------------------------------------------------------------------
# Race Track and Sprint Board renderers: real getRankedIndividualRows,
# sortLeaderboardNodes, stationLabel, relayLegLine, formatResultScore,
# formatSprintSignal, raceBoardDensityTier and the renderer itself, driven
# against a minimal fake container (innerHTML set/get only -- neither
# renderer reads element geometry for the individual-mode path this test
# exercises). Peripheral helpers unrelated to the tier decision (podium,
# finish celebration, member chips, reorder animation, finality) are
# stubbed, matching the accepted pattern in test_dashboard_relay_leg_cue.py
# and test_dashboard_leaderboard_card_cache.py of extracting the real
# function under test while stubbing DOM-heavy or unrelated collaborators.
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


def _board_stubs() -> str:
    return (
        "const t = (key, params = {}) => { let value = `T[${key}]`; "
        "Object.entries(params).forEach(([name, replacement]) => { "
        "value = value.replaceAll(`{${name}}`, String(replacement)); }); "
        "return value; };\n"
        # Stubbed rather than extracted -- escapeHtml's real body contains a
        # `/'/g` regex literal, whose lone quote character defeats the
        # brace-depth extractor used throughout this test suite (same
        # reason every other hub test file stubs escapeHtml instead of
        # extracting it).
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
        "function formatSprintSignal(row, raceType) { return { value: `${metricNumber(row.raw.power_watts).toFixed(0)}W`, label: t('metric.current_power') }; }\n"
        "function teamPolicyLabel() { return ''; }\n"
        "let leaderboardNodes = [];\n"
        "let teamLeaderboardRows = [];\n"
        "let leaderboardRankByNode = new Map();\n"
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


def _board_node(node_id, station_number, progress=10):
    return {
        "node_id": node_id,
        "station_number": station_number,
        "athlete_name": f"Athlete {station_number}",
        "power_watts": 150,
        "instantaneous_speed_kph": 20,
        "distance_m": 300,
        "progress_percent": progress,
    }


def _run_board(renderer_name: str, row_count: int, root_class: str) -> str:
    import json

    progress_data = {f"n{i}": _board_node(f"n{i}", i) for i in range(1, row_count + 1)}
    script = (
        _board_stubs()
        + _board_fake_dom()
        + _extract_board_fns([renderer_name])
        + f"\n{renderer_name}({json.dumps(progress_data)});"
        + "\nconsole.log(leaderboardContainer.innerHTML);"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    html = result.stdout
    match = re.search(rf'<div[^>]*class="[^"]*{root_class}[^"]*"[^>]*>', html)
    assert match, f"{renderer_name} has no element carrying the {root_class} class"
    return match.group(0)


def test_race_track_two_rows_carries_the_xl_class():
    tag = _run_board("renderRaceTrackLeaderboard", 2, "race-track-list")
    assert "race-board--xl" in tag


def test_race_track_six_rows_carries_no_tier_class():
    tag = _run_board("renderRaceTrackLeaderboard", 6, "race-track-list")
    assert "race-board--xl" not in tag
    assert "race-board--lg" not in tag


def test_sprint_board_two_rows_carries_the_xl_class():
    tag = _run_board("renderSprintBoardLeaderboard", 2, "sprint-board-grid")
    assert "race-board--xl" in tag


def test_sprint_board_six_rows_carries_no_tier_class():
    tag = _run_board("renderSprintBoardLeaderboard", 6, "sprint-board-grid")
    assert "race-board--xl" not in tag
    assert "race-board--lg" not in tag


# ---------------------------------------------------------------------------
# CSS structural checks: the xl/lg rules exist and target the classic, race
# track and sprint board selectors.
# ---------------------------------------------------------------------------


def test_css_xl_tier_targets_all_three_board_types():
    css = _read_index()
    assert re.search(
        r"\.race-board--xl[^{]*\.athlete-name[^{]*\{", css
    ), "xl tier does not size the classic leaderboard athlete name"
    assert re.search(
        r"\.race-board--xl[^{]*\.race-track-name[^{]*\{", css
    ), "xl tier does not size the race track name"
    assert re.search(
        r"\.race-board--xl[^{]*\.sprint-board-name[^{]*\{", css
    ), "xl tier does not size the sprint board name"


def test_css_lg_tier_targets_all_three_board_types():
    css = _read_index()
    assert re.search(
        r"\.race-board--lg[^{]*\.athlete-name[^{]*\{", css
    ), "lg tier does not size the classic leaderboard athlete name"
    assert re.search(
        r"\.race-board--lg[^{]*\.race-track-name[^{]*\{", css
    ), "lg tier does not size the race track name"
    assert re.search(
        r"\.race-board--lg[^{]*\.sprint-board-name[^{]*\{", css
    ), "lg tier does not size the sprint board name"


def test_css_xl_tier_stretches_rows_to_fill_the_panel():
    css = _read_index()
    assert re.search(
        r"\.leaderboard-list\.race-board--xl[^{]*\{[^}]*flex", css
    ), "xl tier does not make the classic leaderboard list a flex container"
    assert re.search(
        r"\.leaderboard-list\.race-board--xl \.leaderboard-item[^{]*\{[^}]*flex",
        css,
    ), "xl tier does not grow the classic leaderboard rows"
