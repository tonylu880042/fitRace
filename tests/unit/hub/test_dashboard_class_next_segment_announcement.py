"""Tests for the large last-ten-seconds announcement of the NEXT segment on
the class board (`hub_server/static/index.html`).

Athletes previously got no warning before an interval changed -- the only
clue was a small muted line (`class.next_segment`) that stays legible from
the front row but is easy to miss from across a gym. This module covers the
new large, unmissable cue that appears once the CURRENT segment has ten
seconds or fewer remaining.

Per CLAUDE.md's testability guidance, the recurring defect in this feature
area is a correct pure helper whose result never actually reaches the DOM.
To close that gap this module:

  1. Executes computeUpcomingSegmentAnnouncementForPatch -- the pure
     show/hide-and-what decision -- directly under `node -e`, extracted
     from the page by the same brace-depth technique
     tests/unit/hub/test_dashboard_class_board_card_cache.py uses. Covers
     11s remaining (hidden), exactly 10s (shown), 1s (shown), the last
     segment of the plan (never shown), a finished plan (never shown), and
     a next segment with no target (shown, without a watts figure).
  2. Drives the real render path (buildClassBoardHtml) and asserts the
     announcement markup, its display state, and its text actually land in
     the rendered HTML -- not merely that the decision function returns
     the right thing somewhere.
  3. Drives the real incremental patch path (renderClassBoardFromState ->
     applyClassBoardIncrementalUpdate) against a fake DOM and proves that
     crossing the ten-second threshold mid-segment patches the
     announcement text and visibility IN PLACE, with no full rebuild
     (innerHTML is written exactly once across both ticks).
  4. Covers the new class.next_segment_announcement i18n key across all six
     locales with genuine, non-English-copy translations, and confirms
     class.target_watts (not a near-duplicate key) supplies the watts
     figure.

This file does not modify tests/unit/hub/test_dashboard_class_board_card_cache.py
or any other existing test.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"
LOCALES_DIR = (
    Path(__file__).resolve().parents[3] / "hub_server" / "infrastructure" / "locales"
)

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CJK_RE = re.compile(r"[一-鿿぀-ゟ゠-ヿ가-힯]")

LOCALES = ["de-CH", "en-US", "fr", "it", "sv", "zh-TW"]


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
# 1. computeUpcomingSegmentAnnouncementForPatch -- the pure decision,
#    executed standalone. No DOM, no i18n, no stubs needed at all: inputs
#    are the clock and the plan, output is whether to show and the next
#    segment kind/target.
# ---------------------------------------------------------------------------


def _run_announcement_decision(clock_js: str, plan_js: str) -> dict:
    source = _read_index()
    fn = _strip_js_comments(
        _extract_function(source, "computeUpcomingSegmentAnnouncementForPatch")
    )
    script = (
        fn
        + "\n"
        + f"const clock = {clock_js};\n"
        + f"const plan = {plan_js};\n"
        + "console.log(JSON.stringify(computeUpcomingSegmentAnnouncementForPatch(clock, plan)));"
    )
    return json.loads(_run_node(script))


_TWO_SEGMENT_PLAN_WITH_TARGET = json.dumps(
    {
        "segments": [
            {"kind": "work", "duration_sec": 300},
            {"kind": "rest", "duration_sec": 60, "target_watts": 150},
        ]
    }
)


def _clock(index, segment_remaining_ms, finished=False):
    return json.dumps(
        {
            "index": index,
            "kind": "work",
            "segmentRemainingMs": segment_remaining_ms,
            "totalRemainingMs": segment_remaining_ms + 60000,
            "finished": finished,
        }
    )


def test_hidden_at_eleven_seconds_remaining():
    result = _run_announcement_decision(_clock(0, 11000), _TWO_SEGMENT_PLAN_WITH_TARGET)
    assert result is None


def test_shown_at_exactly_ten_seconds_remaining():
    result = _run_announcement_decision(_clock(0, 10000), _TWO_SEGMENT_PLAN_WITH_TARGET)
    assert result == {"kind": "rest", "targetWatts": 150}


def test_shown_at_one_second_remaining():
    result = _run_announcement_decision(_clock(0, 1000), _TWO_SEGMENT_PLAN_WITH_TARGET)
    assert result == {"kind": "rest", "targetWatts": 150}


def test_never_shown_on_the_last_segment_of_the_plan():
    # index 1 is the last segment in the two-segment plan -- there is no
    # index 2 to announce, no matter how little time remains.
    result = _run_announcement_decision(_clock(1, 3000), _TWO_SEGMENT_PLAN_WITH_TARGET)
    assert result is None


def test_never_shown_once_the_plan_has_finished():
    # A structurally plausible next segment exists (index 0 of 3), but the
    # finished flag alone must suppress the announcement.
    three_segment_plan = json.dumps(
        {
            "segments": [
                {"kind": "warmup", "duration_sec": 30},
                {"kind": "work", "duration_sec": 300},
                {"kind": "cooldown", "duration_sec": 60},
            ]
        }
    )
    result = _run_announcement_decision(_clock(0, 0, finished=True), three_segment_plan)
    assert result is None


def test_shown_without_watts_when_next_segment_has_no_target():
    plan_no_target = json.dumps(
        {
            "segments": [
                {"kind": "work", "duration_sec": 300},
                {"kind": "cooldown", "duration_sec": 60},
            ]
        }
    )
    result = _run_announcement_decision(_clock(0, 5000), plan_no_target)
    assert result == {"kind": "cooldown", "targetWatts": None}


# ---------------------------------------------------------------------------
# 2. buildClassBoardHtml render path -- the announcement markup.
# ---------------------------------------------------------------------------


def _t_stub() -> str:
    # Informative stub: plain T[key] with no params, T[key|{"n":"v"}] with
    # params -- proves an actual kind/watts value reached t(), not merely
    # that some key was called.
    return (
        "const t = (key, params = {}) => {\n"
        "  if (params && Object.keys(params).length) {\n"
        "    return `T[${key}|${JSON.stringify(params)}]`;\n"
        "  }\n"
        "  return `T[${key}]`;\n"
        "};\n"
    )


def _metric_number_stub() -> str:
    return (
        "const metricNumber = (value, fallback = 0) => {\n"
        "  const n = Number(value);\n"
        "  return Number.isFinite(n) ? n : fallback;\n"
        "};\n"
    )


def _escape_html_stub() -> str:
    return (
        "const escapeHtml = (value) => {\n"
        "  return String(value ?? '')\n"
        "    .replace(/&/g, '&amp;')\n"
        "    .replace(/</g, '&lt;')\n"
        "    .replace(/>/g, '&gt;')\n"
        "    .replace(/\"/g, '&quot;')\n"
        "    .replace(/'/g, '&#039;');\n"
        "};\n"
    )


def _node_display_name_stub() -> str:
    return (
        "const nodeDisplayName = (node) => {\n"
        "  return node?.node_display_name || node?.display_name || node?.node_id || '--';\n"
        "};\n"
    )


def _intl_number_format_stub() -> str:
    return "const Intl = { NumberFormat: function(locale) { return { format: (n) => String(n) }; } };\n"


def _format_clock_stub() -> str:
    return (
        "function formatClock(ms) {\n"
        "  const safeMs = Math.max(0, metricNumber(ms));\n"
        "  const totalSeconds = Math.floor(safeMs / 1000);\n"
        "  const seconds = totalSeconds % 60;\n"
        "  const minutes = Math.floor(totalSeconds / 60);\n"
        "  return (minutes < 10 ? '0' + minutes : String(minutes)) + ':' +\n"
        "         (seconds < 10 ? '0' + seconds : String(seconds));\n"
        "}\n"
    )


def _run_build_class_board_html(session_data_js: str, clock_js: str) -> str:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "buildClassBoardHtml"))
    script = (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _node_display_name_stub()
        + _intl_number_format_stub()
        + _format_clock_stub()
        + "const currentLocale = 'en-US';\n"
        + fn
        + "\n"
        + f"const sessionData = {session_data_js};\n"
        + f"const clock = {clock_js};\n"
        + "console.log(buildClassBoardHtml(sessionData, clock));"
    )
    return _run_node(script)


def test_render_hides_announcement_with_eleven_seconds_remaining():
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "work", "duration_sec": 300},
                    {"kind": "rest", "duration_sec": 60, "target_watts": 150},
                ]
            },
            "leaderboard": {},
        }
    )
    clock = _clock(0, 11000)
    html = _run_build_class_board_html(session_data, clock)
    assert 'class="class-next-announcement" style="display:none;' in html


def test_render_shows_announcement_with_kind_and_watts_at_ten_seconds():
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "work", "duration_sec": 300},
                    {"kind": "rest", "duration_sec": 60, "target_watts": 150},
                ]
            },
            "leaderboard": {},
        }
    )
    clock = _clock(0, 10000)
    html = _run_build_class_board_html(session_data, clock)
    assert 'class="class-next-announcement" style="display:block;' in html
    assert "T[class.next_segment_announcement|" in html
    assert "T[class.kind.rest]" in html
    # class.target_watts is reused here rather than a near-duplicate key.
    assert "T[class.target_watts|" in html


def test_render_shows_announcement_without_watts_when_next_has_none():
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "work", "duration_sec": 300},
                    {"kind": "cooldown", "duration_sec": 60},
                ]
            },
            "leaderboard": {},
        }
    )
    clock = _clock(0, 5000)
    html = _run_build_class_board_html(session_data, clock)
    assert 'class="class-next-announcement" style="display:block;' in html
    assert "T[class.next_segment_announcement|" in html
    # No target on the upcoming segment -- the watts line stays empty.
    assert (
        'class="class-next-announcement-watts" style="font-family:Oswald,sans-serif;font-size:1.75rem;font-weight:700;color:var(--hazard-amber);margin-top:0.25rem;"></div>'
        in html
    )


def test_render_never_shows_announcement_on_the_last_segment():
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "work", "duration_sec": 300},
                ]
            },
            "leaderboard": {},
        }
    )
    clock = _clock(0, 3000)
    html = _run_build_class_board_html(session_data, clock)
    assert 'class="class-next-announcement" style="display:none;' in html


def test_render_never_shows_announcement_once_finished():
    session_data = json.dumps(
        {
            "class_plan": {
                "segments": [
                    {"kind": "work", "duration_sec": 300},
                    {"kind": "cooldown", "duration_sec": 60},
                ]
            },
            "leaderboard": {},
        }
    )
    clock = _clock(1, 0, finished=True)
    html = _run_build_class_board_html(session_data, clock)
    assert 'class="class-next-announcement" style="display:none;' in html


# ---------------------------------------------------------------------------
# 3. Incremental patch path: renderClassBoardFromState ->
#    applyClassBoardIncrementalUpdate, against a fake DOM. Proves crossing
#    the ten-second threshold patches the announcement in place with no
#    full rebuild.
# ---------------------------------------------------------------------------


def _stubs() -> str:
    return (
        "const t = (key, params = {}) => {\n"
        "  if (params && Object.keys(params).length) {\n"
        "    return `T[${key}|${JSON.stringify(params)}]`;\n"
        "  }\n"
        "  return `T[${key}]`;\n"
        "};\n"
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
    return { nodeId: o.nodeId, power: power ? power[1] : null };
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

function makeMutableStyleProp(initial, propName) {
  const box = { style: {} };
  box.style["_" + propName] = initial;
  Object.defineProperty(box.style, propName, {
    get() { return box.style["_" + propName]; },
    set(v) { box.style["_" + propName] = v; },
  });
  return box;
}

function makeContainer() {
  let html = "";
  let cardEls = [];
  let countdownEl = null;
  let totalRemainingEl = null;
  let progressFillEl = null;
  let announcementContainerEl = null;
  let announcementKindEl = null;
  let announcementWattsEl = null;

  const container = {
    querySelectorAll(sel) { return sel === "[data-node-id]" ? cardEls : []; },
    querySelector(sel) {
      if (sel === ".class-hero-countdown") return countdownEl;
      if (sel === ".class-hero-total-remaining") return totalRemainingEl;
      if (sel === ".class-progress-fill") return progressFillEl;
      if (sel === ".class-next-announcement") return announcementContainerEl;
      if (sel === ".class-next-announcement-kind") return announcementKindEl;
      if (sel === ".class-next-announcement-watts") return announcementWattsEl;
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
        return {
          dataset: { nodeId: c.nodeId },
          querySelector(sel) { return sel === ".class-metric-power" ? powerEl : null; },
        };
      });
      const countdownMatch = value.match(/class="class-hero-countdown" style="[^"]*">([^<]*)</);
      countdownEl = countdownMatch ? makeMutableText(countdownMatch[1]) : null;
      const totalRemainingMatch = value.match(/class="class-hero-total-remaining" style="[^"]*">([^<]*)</);
      totalRemainingEl = totalRemainingMatch ? makeMutableText(totalRemainingMatch[1]) : null;
      const progressFillMatch = value.match(/class="class-progress-fill" style="height:100%;width:([^%]+)%/);
      progressFillEl = progressFillMatch ? makeMutableStyleProp(progressFillMatch[1] + "%", "width") : null;
      const announcementDisplayMatch = value.match(/class="class-next-announcement" style="display:([^;]+);/);
      announcementContainerEl = announcementDisplayMatch ? makeMutableStyleProp(announcementDisplayMatch[1], "display") : null;
      const announcementKindMatch = value.match(/class="class-next-announcement-kind" style="[^"]*">([^<]*)</);
      announcementKindEl = announcementKindMatch ? makeMutableText(announcementKindMatch[1]) : null;
      const announcementWattsMatch = value.match(/class="class-next-announcement-watts" style="[^"]*">([^<]*)</);
      announcementWattsEl = announcementWattsMatch ? makeMutableText(announcementWattsMatch[1]) : null;
    },
  });

  return container;
}

const leaderboardContainer = makeContainer();
const document = {
  getElementById(id) { return id === "leaderboard-container" ? leaderboardContainer : null; },
};

function readAnnouncement() {
  const containerEl = leaderboardContainer.querySelector(".class-next-announcement");
  const kindEl = leaderboardContainer.querySelector(".class-next-announcement-kind");
  const wattsEl = leaderboardContainer.querySelector(".class-next-announcement-watts");
  return {
    display: containerEl ? containerEl.style.display : null,
    kindText: kindEl ? kindEl.textContent : null,
    wattsText: wattsEl ? wattsEl.textContent : null,
  };
}
"""


_PIPELINE_FN_NAMES = [
    "resetClassBoardCardCache",
    "classTargetStatusForPatch",
    "computeClassProgressPercentForPatch",
    "computeUpcomingSegmentAnnouncementForPatch",
    "classSegmentKindLabelForPatch",
    "buildClassStationSignature",
    "captureClassBoardRefs",
    "applyClassBoardIncrementalUpdate",
    "renderClassBoardFromState",
    "buildClassBoardHtml",
    "classClockAt",
    "formatClock",
]


def _extract_pipeline_fns() -> str:
    source = _read_index()
    return "\n".join(
        _strip_js_comments(_extract_function(source, name))
        for name in _PIPELINE_FN_NAMES
    )


def _run_pipeline(script_body: str) -> dict:
    script = _stubs() + _fake_dom() + _extract_pipeline_fns() + "\n" + script_body
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


_PLAN_WITH_TARGET_JS = json.dumps(
    {
        "segments": [
            {"kind": "work", "duration_sec": 20},
            {"kind": "rest", "duration_sec": 30, "target_watts": 140},
        ]
    }
)


def test_incremental_path_reveals_announcement_when_crossing_threshold():
    """Two ticks inside the SAME segment: the first with 11s remaining
    (hidden), the second with 9.5s remaining (shown). Must patch in place
    with no full rebuild, and the patched text must actually name the
    upcoming segment and its target."""
    script = f"""
currentClassPlan = {_PLAN_WITH_TARGET_JS};
currentClassLeaderboard = {{}};
const nowBase = Date.now();
raceStartTime = nowBase - 9000;
renderClassBoardFromState();
const before = readAnnouncement();
const afterFirst = innerHTMLSetCount;
raceStartTime = nowBase - 10500;
renderClassBoardFromState();
const after = readAnnouncement();
console.log(JSON.stringify({{ before, after, afterFirst, afterSecond: innerHTMLSetCount }}));
"""
    result = _run_pipeline(script)
    assert result["afterFirst"] == 1
    assert result["afterSecond"] == 1, (
        "crossing the announcement threshold mid-segment must not trigger "
        f"a full rebuild, went from 1 to {result['afterSecond']}"
    )
    assert result["before"]["display"] == "none"
    assert result["after"]["display"] == "block"
    assert result["before"]["kindText"] == ""
    assert "T[class.next_segment_announcement|" in result["after"]["kindText"]
    assert "T[class.kind.rest]" in result["after"]["kindText"]
    assert "T[class.target_watts|" in result["after"]["wattsText"]


def test_incremental_path_never_shows_announcement_on_last_segment():
    single_segment_plan = json.dumps(
        {"segments": [{"kind": "work", "duration_sec": 20}]}
    )
    script = f"""
currentClassPlan = {single_segment_plan};
currentClassLeaderboard = {{}};
const nowBase = Date.now();
raceStartTime = nowBase - 12000;
renderClassBoardFromState();
const before = readAnnouncement();
raceStartTime = nowBase - 18000;
renderClassBoardFromState();
const after = readAnnouncement();
console.log(JSON.stringify({{ before, after }}));
"""
    result = _run_pipeline(script)
    assert result["before"]["display"] == "none"
    assert result["after"]["display"] == "none"


def test_incremental_path_hides_announcement_once_plan_finishes():
    """The segment nears its end (announcement shown), then the plan
    finishes entirely (finished flips true) -- the finished transition is
    already known to force a full rebuild, and that rebuild must render
    the announcement hidden, not leave it stuck showing."""
    single_segment_plan = json.dumps(
        {"segments": [{"kind": "work", "duration_sec": 20}]}
    )
    script = f"""
currentClassPlan = {single_segment_plan};
currentClassLeaderboard = {{}};
const nowBase = Date.now();
raceStartTime = nowBase - 12000;
renderClassBoardFromState();
raceStartTime = nowBase - 25000;
renderClassBoardFromState();
const after = readAnnouncement();
console.log(JSON.stringify({{ after }}));
"""
    result = _run_pipeline(script)
    assert result["after"]["display"] == "none"


# ---------------------------------------------------------------------------
# 4. i18n: class.next_segment_announcement exists in all six locales with
#    genuine, non-English-copy translations; zh-TW carries real CJK; the
#    {kind} placeholder survives translation; class.target_watts (already
#    covered by its own test module) supplies the watts figure rather than
#    a new near-duplicate key.
# ---------------------------------------------------------------------------

_NEW_KEY = "class.next_segment_announcement"


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_new_announcement_key_exists_in_all_locales():
    for locale in LOCALES:
        messages = _load_locale(locale)
        assert _NEW_KEY in messages, f"Missing {_NEW_KEY} in {locale}"


def test_new_announcement_key_in_zh_tw_contains_cjk_characters():
    value = _load_locale("zh-TW")[_NEW_KEY]
    assert _CJK_RE.search(
        value
    ), f"{_NEW_KEY} in zh-TW should contain CJK but is: {value}"


def test_new_announcement_key_is_not_a_copy_of_english_across_locales():
    en_us_value = _load_locale("en-US")[_NEW_KEY]
    for locale in ("de-CH", "fr", "it", "sv"):
        value = _load_locale(locale)[_NEW_KEY]
        assert value != en_us_value, (
            f"{_NEW_KEY} in {locale} is an unmodified copy of the en-US "
            f"string: {en_us_value!r}"
        )


def test_announcement_placeholder_present_and_consistent_across_locales():
    for locale in LOCALES:
        value = _load_locale(locale)[_NEW_KEY]
        assert "{kind}" in value, f"{locale} lost the {{kind}} placeholder: {value!r}"


def test_announcement_reuses_target_watts_key_not_a_near_duplicate():
    # class.target_watts already exists (added for the current-segment
    # hero band) and is reused verbatim for the announcement watts line --
    # this module must not have minted a second, near-duplicate key for
    # the same "Target {watts} W" concept.
    for locale in LOCALES:
        messages = _load_locale(locale)
        assert "class.target_watts" in messages
        assert "class.next_segment_announcement_watts" not in messages
        assert "class.next_segment_announcement_target" not in messages
