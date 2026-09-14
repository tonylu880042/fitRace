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
    # Top-level (feat/pace-effects) -- renderLeaderboard and
    # updateLeaderboardCardValues both call these shared definitions
    # rather than a nested copy of their own.
    "isRunningEquipment",
    "formatPacePerKm",
    "paceBand",
    "fastestPaceNodeId",
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
    # Top-level (feat/pace-effects) -- renderRaceTrackLeaderboard and
    # renderSprintBoardLeaderboard both call these shared definitions
    # rather than a nested copy of their own.
    "isRunningEquipment",
    "formatPacePerKm",
    "paceBand",
    "fastestPaceNodeId",
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
        "function formatSprintSignal(row, raceType) { return { value: `${metricNumber(row.raw.power_watts).toFixed(0)}W`, label: t('metric.current_power'), band: 'none' }; }\n"
        "function teamPolicyLabel() { return ''; }\n"
        "let leaderboardNodes = [];\n"
        "let teamLeaderboardRows = [];\n"
        "let leaderboardRankByNode = new Map();\n"
        # renderRaceTrackLeaderboard / renderSprintBoardLeaderboard now also
        # gate the pace-effects trail/badge (feat/pace-effects) on the
        # page-global currentState -- not exercised by this file's tier
        # assertions, so a plain non-RUNNING default is enough.
        "let currentState = 'IDLE';\n"
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


def test_css_xl_tier_grows_rows_with_a_viewport_based_min_height():
    """xl rows must grow using a size that does not depend on a parent's
    own height. The leaderboard panel is content-sized, not stretched to
    the viewport by an ancestor, so a flex-grow row inside a flex-grow
    list has nothing to grow into and stays at its natural content
    height -- a real defect a screenshot caught that no DOM-less test
    here could. A min-height expressed in vh resolves against the
    viewport regardless of any ancestor's height, so it grows the rows
    even though nothing upstream constrains the panel."""
    css = _read_index()
    match = re.search(
        r"\.race-board--xl \.leaderboard-item,\s*"
        r"\.race-board--xl \.race-track-item,\s*"
        r"\.race-board--xl \.sprint-board-card\s*\{([^}]*)\}",
        css,
    )
    assert (
        match
    ), "xl tier does not size the classic/race-track/sprint-board rows together"
    block = match.group(1)
    assert "min-height" in block, "xl tier rows have no min-height rule"
    assert "vh" in block, "xl tier row min-height is not viewport-based"
    # The old flex-grow mechanism this replaces must actually be gone, not
    # just supplemented alongside it -- an ineffective rule left in place
    # is still a defect even if a working one now sits next to it. Checked
    # on the PROPERTY, not the selector text: .leaderboard-list.race-
    # board--xl is a legitimate compound selector reused later in this
    # file for an unrelated, working rule (row-gap compaction at short
    # viewports), so a bare substring-not-in-css check on the selector
    # would false-fail on that reuse.
    for selector in (
        ".leaderboard-list.race-board--xl",
        ".race-track-list.race-board--xl",
    ):
        block_match = re.search(re.escape(selector) + r"[^{]*\{([^}]*)\}", css)
        if block_match:
            rule_body = block_match.group(1)
            assert (
                "flex" not in rule_body
            ), f"the old flex-grow rule on {selector} is still present: {rule_body}"


def test_css_row_padding_and_avatar_shrink_at_short_viewports():
    """The min-height fix above only bounds classic/race-track/sprint-board
    from BELOW -- at a short-but-wide viewport (1280x720) the type is
    already at its width-based floor and the row's own chrome (padding,
    the 44px avatar) is what is left standing between that and fitting
    3-4 rows inside 720px tall, the review-caught regression (rows
    pushed below the fold at lg). Both must shrink via a viewport-height
    term, not stay flat."""
    css = _read_index()
    padding_rule = re.search(
        r"\.race-board--xl \.leaderboard-item,\s*"
        r"\.race-board--lg \.leaderboard-item\s*\{([^}]*)\}",
        css,
    )
    assert padding_rule, "xl/lg classic rows have no shared padding override"
    assert "vh" in padding_rule.group(1), (
        "xl/lg classic row padding is not viewport-height responsive: "
        + padding_rule.group(1)
    )

    avatar_rule = re.search(
        r"\.race-board--xl \.leaderboard-item \.athlete-avatar,\s*"
        r"\.race-board--lg \.leaderboard-item \.athlete-avatar\s*\{([^}]*)\}",
        css,
    )
    assert avatar_rule, "xl/lg classic rows do not override the avatar size"
    body = avatar_rule.group(1)
    assert "vh" in body, "xl/lg avatar size is not viewport-height responsive: " + body
    # The renderer sets the avatar's size as an inline style (higher
    # specificity than any class selector) -- without !important this
    # override does nothing at all.
    assert (
        "!important" in body
    ), "xl/lg avatar override has no !important and cannot win over the inline style"


def test_css_sprint_board_grid_locks_column_count_to_avoid_wrapping():
    """auto-fit's default minmax(220px, 1fr) wraps a 4th sprint-board card
    onto a second grid row once four cards no longer fit one 220px-
    minimum column each -- review caught that second row pushed below
    the fold at 1280x720. xl (1-2 cards) and lg (3-4 cards) must each
    force a fixed column count instead, so every card the tier ever
    renders shares a single row."""
    css = _read_index()
    xl_columns = _grid_columns_value(css, ".sprint-board-grid.race-board--xl")
    lg_columns = _grid_columns_value(css, ".sprint-board-grid.race-board--lg")
    assert "auto-fit" not in xl_columns, (
        "xl sprint board grid still uses auto-fit, which can wrap: " + xl_columns
    )
    assert "auto-fit" not in lg_columns, (
        "lg sprint board grid still uses auto-fit, which can wrap: " + lg_columns
    )
    assert re.search(r"repeat\(\s*2\s*,", xl_columns), (
        "xl sprint board grid does not force exactly 2 columns: " + xl_columns
    )
    assert re.search(r"repeat\(\s*4\s*,", lg_columns), (
        "lg sprint board grid does not force exactly 4 columns: " + lg_columns
    )


# ---------------------------------------------------------------------------
# Column-width regression coverage. Review of the first cut of this feature
# (real 1920x1080 / 1280x720 screenshots via Playwright, not visible to the
# node-only tests above) caught two defects no earlier test in this file
# exercised:
#   1. The classic card's metric columns kept today's widths while xl/lg
#      grew the type inside them 1.5x-2x, so a distance value and the
#      progress percent next to it collided into one unreadable run
#      ("32064.0%"), clipped past the card's right edge. Same story for
#      the race track score column, narrower still (0.18fr).
#   2. Fixing (1) with flat px column minimums sized for a 1920px
#      projector created a SECOND defect at 1280x720: those minimums do
#      not shrink the way the font's vw-based clamp does, so the metric
#      columns kept demanding their full 1920px minimum width and the
#      name column -- the only track with no floor of its own -- was
#      squeezed down to nothing, wrapping a three-character name one
#      character per line.
# These are structural CSS checks (grep, not a real layout engine --
# actual pixel collision is out of reach for the node-only harness this
# suite runs under), verifying the fix's shape: real column-width tracks
# now exist, sized with vw so they shrink together with the font clamps
# above, plus the min-width: 0 a long name needs to wrap instead of
# forcing the row wider than its card.
# ---------------------------------------------------------------------------


def _grid_columns_value(css: str, selector: str) -> str:
    # A selector can appear in more than one rule block (e.g. one block
    # sets gap, a later one sets grid-template-columns) -- scan every
    # block that selector text opens and return the first that actually
    # declares grid-template-columns, rather than assuming the first
    # occurrence is the relevant one.
    pattern = re.escape(selector) + r"\s*\{([^}]*)\}"
    blocks = re.findall(pattern, css)
    assert blocks, f"no CSS rule for {selector}"
    for block in blocks:
        gtc = re.search(r"grid-template-columns:\s*([^;]+);", block)
        if gtc:
            return gtc.group(1)
    raise AssertionError(
        f"{selector} does not set grid-template-columns in any of its rule blocks"
    )


def test_xl_and_lg_classic_grid_uses_auto_sized_metric_columns():
    """review's second-round fix (a vw-scaled minmax() floor on every
    metric column) turned out to fight its own font-size clamp: a floor
    picked generously enough for a 1920px projector does not shrink at
    1280px the way the font does, so the metric columns kept demanding
    their full-size minimum width and the name column -- the only track
    with no floor of its own -- was squeezed toward zero, wrapping a
    three-character name one character per line. auto sidesteps the
    mismatch entirely: a track sized "auto" always matches its own
    content's actual rendered width at whatever font-size currently
    applies, so it can never drift out of sync with the font clamp the
    way a separately-maintained vw formula did. The name column is the
    one non-auto (flexible) track, so it -- not the fixed auto tracks --
    absorbs whatever width auto's tracks did not claim."""
    css = _read_index()
    for selector in (
        ".race-board--xl .leaderboard-item",
        ".race-board--lg .leaderboard-item",
    ):
        columns = _grid_columns_value(css, selector)
        assert (
            columns == "auto minmax(0, 1fr) auto auto auto"
        ), f"{selector} grid-template-columns is not the auto-track design: {columns}"


def test_xl_and_lg_metric_value_font_size_is_viewport_height_responsive():
    """Column width alone cannot fix a value colliding with its neighbour
    if the type itself is not also capped by the viewport's HEIGHT: a
    short-but-wide 1280x720 window still received the full-size (width-
    clamped) type, and that type needed more vertical room than 720px
    tall has for even a single row -- sprint board's one-row case
    overflowed the fold on exactly this axis, independent of column
    width. Every metric-val/rank font-size must include a vh term so it
    also shrinks when height, not width, is the scarce dimension."""
    css = _read_index()
    for tier in ("xl", "lg"):
        rule = re.search(
            rf"\.race-board--{tier} \.metric-val,\s*"
            rf"\.race-board--{tier} \.rank,\s*"
            rf"\.race-board--{tier} \.sprint-board-rank\s*\{{([^}}]*font-size[^}}]*)\}}",
            css,
        )
        assert (
            rule
        ), f"{tier} metric-val/rank/sprint-board-rank has no shared font-size rule"
        assert "vh" in rule.group(
            1
        ), f"{tier} metric-val font-size has no viewport-height term: " + rule.group(1)


def test_xl_and_lg_name_is_not_forced_to_wrap_via_overflow_wrap_anywhere():
    """overflow-wrap: anywhere on the athlete name was review-flagged: it
    let a three-character name that only needed a slightly wider column
    break mid-character instead, on top of the actual column-width fix
    doing its job. Normal wrapping (CJK already breaks between
    characters when it must; Latin text breaks at spaces/hyphens) is
    enough once the column and font-size fixes above give a short name
    the room it needs -- this rule must not reintroduce the aggressive
    mid-token behaviour for .athlete-name/.sprint-board-name.
    .race-track-name is excluded: its own overflow-wrap: anywhere is
    pre-existing on main, unrelated to this feature, and out of scope
    here."""
    css = _read_index()
    for tier in ("xl", "lg"):
        rule = re.search(
            rf"\.race-board--{tier} \.athlete-name,\s*"
            rf"\.race-board--{tier} \.race-track-name,\s*"
            rf"\.race-board--{tier} \.sprint-board-name\s*\{{([^}}]*)\}}",
            css,
        )
        assert (
            rule
        ), f"{tier} athlete-name/race-track-name/sprint-board-name rule not found"
        assert "overflow-wrap" not in rule.group(
            1
        ), f"{tier} name rule still forces overflow-wrap: anywhere: " + rule.group(1)


def test_xl_and_lg_equipment_tag_and_relay_leg_are_single_line_with_ellipsis():
    """The equipment/station tag and every relay-leg-line variant hold
    arbitrary, unbounded text (a machine name, a runner's name) -- at
    the xl/lg scale that text must stay one line and ellipsize rather
    than break mid-token the way "FITRACE-EDGE-02-01" did (a browser
    default: it wraps after a hyphen when a word does not fit)."""
    css = _read_index()
    for tier in ("xl", "lg"):
        # The selector group can legitimately appear in more than one
        # rule block (e.g. font-size in one, the ellipsis properties in
        # another) -- concatenate every block it opens rather than
        # assuming the first one holds everything.
        blocks = re.findall(
            rf"\.race-board--{tier} \.equipment-tag,\s*"
            rf"\.race-board--{tier} \.relay-leg-line,\s*"
            rf"\.race-board--{tier} \.race-track-relay-leg,\s*"
            rf"\.race-board--{tier} \.sprint-board-relay-leg\s*\{{([^}}]*)\}}",
            css,
        )
        assert blocks, f"{tier} equipment-tag/relay-leg-line rule not found"
        body = "\n".join(blocks)
        assert (
            "white-space: nowrap" in body
        ), f"{tier} tag/relay-leg is not forced to one line: {body}"
        assert (
            "overflow: hidden" in body
        ), f"{tier} tag/relay-leg does not clip overflow: {body}"
        assert (
            "text-overflow: ellipsis" in body
        ), f"{tier} tag/relay-leg does not ellipsize: {body}"


def test_xl_classic_name_cell_resets_min_width_for_wrapping():
    css = _read_index()
    assert re.search(
        r"\.race-board--xl \.leaderboard-item > \.athlete-info\s*\{[^}]*min-width:\s*0",
        css,
    ), "xl tier does not reset athlete-info's min-width, so a long name forces the row wider than its card"
    assert re.search(
        r"\.race-board--xl \.leaderboard-item > \.athlete-info > div\s*\{[^}]*min-width:\s*0",
        css,
    ), "xl tier does not reset the name/tag column's min-width"


def test_xl_race_track_score_column_is_wider_than_the_original_defect_width():
    css = _read_index()
    columns = _grid_columns_value(css, ".race-board--xl .race-track-item")
    # The original (pre-review) score track was minmax(90px, 0.18fr) --
    # far too narrow once its type grew to the xl scale. Every minmax()
    # minimum in the overridden track list must now be at least 150px
    # (or a vw equivalent that resolves >= 150px at 1920), never the old
    # 90px floor.
    assert "90px" not in columns, (
        "xl race track columns still carry the original too-narrow 90px "
        "score column floor: " + columns
    )
    tracks = re.findall(r"minmax\(([^,]+),", columns)
    assert tracks, "xl race track row has no minmax() tracks: " + columns


def test_five_rows_classic_and_race_track_columns_are_untouched():
    """No tier class means no override at all -- the base grid definitions
    (used by every 5+ row render) must be exactly what they were before
    this feature existed."""
    css = _read_index()
    assert (
        "grid-template-columns: 50px 1.5fr 1fr 1fr 120px;" in css
    ), "the base (no-tier) classic leaderboard-item column widths changed"
    assert (
        "grid-template-columns: 54px minmax(170px, 0.55fr) minmax(260px, 1fr) minmax(90px, 0.18fr);"
        in css
    ), "the base (no-tier) race-track-item column widths changed"
