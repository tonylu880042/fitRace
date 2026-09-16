"""While the overall standings page (buildOverallStandingsView) is showing,
the idle record wall (hub_server/static/index.html) must not rotate or
show carousel dots -- it is ONE static page, not a slide in the carousel.
When GET /api/results/standings comes back empty (no eligible race), the
existing top-3 carousel (with its 10s rotation and dots) must still work
exactly as before.

This executes the REAL, unmodified `refreshRecordWallData`,
`renderRecordWallSlide`, `advanceRecordWall`, `updateRecordWallRotateTimer`
and `enterIdleRecordWall` functions pulled out of index.html's inline
<script> via brace-depth matching, under node with a stubbed `fetch` /
`document` / `window.setInterval` -- never a source-text grep -- so
reintroducing the old always-rotate behavior turns this red instead of
being satisfied by a nearby comment.
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


def _matching_bracket_end(
    source: str, open_idx: int, open_ch: str, close_ch: str
) -> int:
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
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching close bracket found")


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    async_start = source.rfind("async ", 0, start)
    if async_start != -1 and source[async_start:start] == "async ":
        start = async_start
    brace_open = source.index("{", start)
    brace_end = _matching_bracket_end(source, brace_open, "{", "}")
    return source[start : brace_end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


_FUNCTION_NAMES = (
    "recordWallTypeLabel",
    "recordWallDivisionLabel",
    "formatRecordEntryValue",
    "formatRecordProgressValue",
    "formatRecordWallEntryValue",
    "renderRecordWallRows",
    "buildLatestRaceEntry",
    "formatMixedGroupTargetLabel",
    "buildMixedGroupHeading",
    "buildRecordWallSlides",
    "renderStandingsRows",
    "buildOverallStandingsView",
    "renderRecordWallSlide",
    "advanceRecordWall",
    "updateRecordWallRotateTimer",
    "refreshRecordWallData",
    "enterIdleRecordWall",
)


def _t_stub() -> str:
    return (
        "const t = (key, params) => {\n"
        "  const messages = {\n"
        '    "record_wall.title": "Record Wall",\n'
        '    "record_wall.latest_race": "Latest Race",\n'
        '    "record_wall.overall_standings": "Overall Standings",\n'
        '    "record_wall.race_count": "{count} races",\n'
        '    "record_wall.type_distance": "Distance",\n'
        '    "record_wall.type_calories": "Calories",\n'
        '    "record_wall.division_men": "Men",\n'
        '    "record_wall.division_women": "Women",\n'
        '    "record_wall.relay_legs": "{legs}-Leg Relay",\n'
        '    "record_wall.dnf": "DNF",\n'
        '    "stations.station": "Station",\n'
        '    "stations.athlete": "Athlete",\n'
        '    "race_type.distance": "Distance",\n'
        '    "race_type.calories": "Calories",\n'
        '    "equipment_type.treadmill": "Treadmill",\n'
        '    "equipment_type.fan_bike": "Fan Bike"\n'
        "  };\n"
        "  let value = messages[key] || key;\n"
        "  Object.entries(params || {}).forEach(([name, replacement]) => {\n"
        "    value = value.replaceAll(`{${name}}`, String(replacement));\n"
        "  });\n"
        "  return value;\n"
        "};\n"
    )


def _metric_number_stub() -> str:
    return (
        "function metricNumber(value, fallback) {\n"
        "  const fb = fallback === undefined ? 0 : fallback;\n"
        "  const n = Number(value);\n"
        "  return Number.isFinite(n) ? n : fb;\n"
        "}\n"
    )


def _escape_html_stub() -> str:
    return (
        "function escapeHtml(value) {\n"
        "  return String(value === null || value === undefined ? '' : value)\n"
        "    .replace(/&/g, '&amp;')\n"
        "    .replace(/</g, '&lt;')\n"
        "    .replace(/>/g, '&gt;');\n"
        "}\n"
    )


_STATE_STUB = """
let recordWallActive = false;
let recordWallSlides = [];
let recordWallIndex = 0;
let recordWallRotateTimer = null;
let recordWallRefreshTimer = null;
let recordWallStandingsMode = false;

const wallEl = {
  innerHTML: "",
  classList: {
    _set: new Set(),
    add(c) { this._set.add(c); },
    remove(c) { this._set.delete(c); },
    contains(c) { return this._set.has(c); },
  },
};
const containerEl = { style: {} };
global.document = {
  getElementById(id) {
    if (id === "record-wall") return wallEl;
    if (id === "leaderboard-container") return containerEl;
    return null;
  },
};

const intervalCalls = [];
let nextTimerId = 1;
global.window = {
  setInterval(fn, ms) {
    const id = nextTimerId++;
    intervalCalls.push({ id, ms });
    return id;
  },
  clearInterval(id) {
    for (let i = intervalCalls.length - 1; i >= 0; i--) {
      if (intervalCalls[i].id === id) intervalCalls.splice(i, 1);
    }
  },
};
"""


def _harness(fetch_stub: str, body: str) -> str:
    source = _strip_js_comments(_read_index())
    fns = "\n".join(_extract_function(source, name) for name in _FUNCTION_NAMES)
    return (
        _t_stub()
        + _metric_number_stub()
        + _escape_html_stub()
        + _STATE_STUB
        + fetch_stub
        + "\n"
        + fns
        + "\n"
        + body
    )


_EMPTY_STANDINGS = "{ race_type: null, sections: [], race_count: 0 }"

_SIX_ROW_STANDINGS = """
{
  race_type: "distance",
  label: "1000 m",
  relay_legs: 2,
  race_count: 3,
  sections: [
    {
      division: null,
      rows: [
        {rank: 1, athlete_name: "Echo", team_name: null, division: null, finished: true, value: 40000, station_number: 1, relay_members: null, race_start_epoch_ms: 1000},
        {rank: 2, athlete_name: "Charlie", team_name: null, division: null, finished: true, value: 45000, station_number: 1, relay_members: null, race_start_epoch_ms: 1000}
      ]
    }
  ]
}
"""


def _fetch_stub(standings_js: str, records_js: str = "[]") -> str:
    return f"""
global.fetch = async (url) => {{
  if (url.includes("/api/results/standings")) {{
    return {{ ok: true, json: async () => ({standings_js}) }};
  }}
  if (url.includes("/api/results/records")) {{
    return {{ ok: true, json: async () => ({{ records: {records_js} }}) }};
  }}
  if (url.includes("/api/results/races")) {{
    return {{ ok: true, json: async () => ({{ races: [] }}) }};
  }}
  return {{ ok: false }};
}};
"""


def _fetch_stub_with_latest_race(standings_js: str, latest_race_js: str) -> str:
    """Like `_fetch_stub`, but /api/results/races?limit=1 finds a race and
    /api/results/races/<id> resolves it to `latest_race_js` -- used to
    exercise the fallback path where the newest race is "mixed"."""
    return f"""
global.fetch = async (url) => {{
  if (url.includes("/api/results/standings")) {{
    return {{ ok: true, json: async () => ({standings_js}) }};
  }}
  if (url.includes("/api/results/records")) {{
    return {{ ok: true, json: async () => ({{ records: [] }}) }};
  }}
  if (url.includes("/api/results/races/")) {{
    return {{ ok: true, json: async () => ({latest_race_js}) }};
  }}
  if (url.includes("/api/results/races")) {{
    return {{ ok: true, json: async () => ({{ races: [{{ result_id: "latest-mixed" }}] }}) }};
  }}
  return {{ ok: false }};
}};
"""


# Mirrors tests/unit/hub/test_dashboard_mixed_record_wall.py's fixture --
# the /api/results/races/{id} response shape for a mixed race (groups +
# results ordered group 0 then group 1, see RaceResultsQuery._rank_and_tag_mixed).
_MIXED_LATEST_RACE = """{
  race_type: "mixed",
  groups: [
    {group_index: 0, race_type: "distance", target_value: 800, duration_sec: 0, equipment_types: ["treadmill"]},
    {group_index: 1, race_type: "calories", target_value: 30, duration_sec: 0, equipment_types: ["fan_bike"]}
  ],
  results: [
    {athlete_name: "Alex", station_number: 1, group_index: 0, rank: 1, finished_time_ms: 60000, distance_m: 800, calories: 5},
    {athlete_name: "Jamie", station_number: 3, group_index: 1, rank: 1, finished_time_ms: null, distance_m: 100, calories: 30}
  ]
}"""


def test_standings_mode_suppresses_dots_and_rotation():
    harness = _harness(
        _fetch_stub(_SIX_ROW_STANDINGS),
        """
(async () => {
  await enterIdleRecordWall();
  console.log(JSON.stringify({
    innerHtmlHasDots: wallEl.innerHTML.includes("record-wall-dots"),
    innerHtmlHasEcho: wallEl.innerHTML.includes("Echo"),
    rotateTimerScheduled: intervalCalls.some((c) => c.ms === 10000),
    refreshTimerScheduled: intervalCalls.some((c) => c.ms === 300000),
    standingsMode: recordWallStandingsMode,
  }));
})();
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    assert result["innerHtmlHasEcho"] is True
    assert result["innerHtmlHasDots"] is False
    assert result["rotateTimerScheduled"] is False
    assert result["refreshTimerScheduled"] is True
    assert result["standingsMode"] is True


def test_empty_standings_falls_back_to_rotating_carousel_with_dots():
    records_js = (
        '[{race_type: "distance", label: "500 m", division: null, relay_legs: null, entries: []},'
        '{race_type: "distance", label: "300 m", division: null, relay_legs: null, entries: []}]'
    )
    harness = _harness(
        _fetch_stub(_EMPTY_STANDINGS, records_js),
        """
(async () => {
  await enterIdleRecordWall();
  console.log(JSON.stringify({
    innerHtmlHasDots: wallEl.innerHTML.includes("record-wall-dots"),
    rotateTimerScheduled: intervalCalls.some((c) => c.ms === 10000),
    refreshTimerScheduled: intervalCalls.some((c) => c.ms === 300000),
    standingsMode: recordWallStandingsMode,
    slideCount: recordWallSlides.length,
  }));
})();
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    assert result["slideCount"] == 2
    assert result["innerHtmlHasDots"] is True
    assert result["rotateTimerScheduled"] is True
    assert result["refreshTimerScheduled"] is True
    assert result["standingsMode"] is False


def test_advance_record_wall_is_a_no_op_in_standings_mode():
    harness = _harness(
        _fetch_stub(_SIX_ROW_STANDINGS),
        """
(async () => {
  await enterIdleRecordWall();
  const before = wallEl.innerHTML;
  advanceRecordWall();
  console.log(JSON.stringify({
    indexUnchanged: recordWallIndex === 0,
    htmlUnchanged: wallEl.innerHTML === before,
  }));
})();
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    assert result["indexUnchanged"] is True
    assert result["htmlUnchanged"] is True


def test_empty_standings_with_mixed_latest_race_renders_mixed_group_slides():
    # get_standings() returns the empty shape whenever the newest race is
    # "mixed" (see RaceResultsQuery._latest_standings_scope) -- the wall
    # must then fall back to buildRecordWallSlides' existing mixed
    # per-group slides, NOT the (empty) standings view.
    harness = _harness(
        _fetch_stub_with_latest_race(_EMPTY_STANDINGS, _MIXED_LATEST_RACE),
        """
(async () => {
  await enterIdleRecordWall();
  const allSlidesHtml = recordWallSlides.join("");
  console.log(JSON.stringify({
    standingsMode: recordWallStandingsMode,
    slideCount: recordWallSlides.length,
    innerHtmlHasStandingsView: wallEl.innerHTML.includes("standings-view"),
    slidesHaveStandingsView: allSlidesHtml.includes("standings-view"),
    slidesHaveTreadmillGroup: allSlidesHtml.includes("Treadmill"),
    slidesHaveFanBikeGroup: allSlidesHtml.includes("Fan Bike"),
    slidesHaveAlex: allSlidesHtml.includes("Alex"),
  }));
})();
""",
    )
    output = _run_node(harness)
    result = json.loads(output)
    assert result["standingsMode"] is False
    assert result["slideCount"] == 2  # one slide per mixed group
    assert result["innerHtmlHasStandingsView"] is False
    assert result["slidesHaveStandingsView"] is False
    assert result["slidesHaveTreadmillGroup"] is True
    assert result["slidesHaveFanBikeGroup"] is True
    assert result["slidesHaveAlex"] is True
