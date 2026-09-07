"""Regression test for the dashboard's (hub_server/static/index.html) race
readiness notice.

Problem: a race refuses to start when equipment is offline, but the
projector dashboard just shows "Waiting for setup" with no explanation.
GET /api/race/readiness already exposes WHY (checks + station_health), but
nothing on the dashboard rendered it.

Fix: readinessBlockingReasons()/readinessNoticeHtml() in the page's inline
script build a translated, read-only notice from the STABLE check keys
(state/target/registrations/teams/stations) -- never from the readiness
payload's English blocking_issues sentences, which are untranslatable and
would put English text on a zh-TW projector.

This module extracts the real functions out of the page's inline <script>
(comments stripped) and executes them under node with stubbed t()/
escapeHtml(), per the technique established in
tests/unit/hub/test_system_admin_select_node_next_free_station.py. A test
that only pattern-matches the source text cannot catch a leaked English
sentence or a station number that slipped past the online/offline
filter -- it has to actually run the extracted code and inspect the
returned HTML string.

The harness intentionally does NOT wrap execution in try/except: if a stub
is missing and the extracted code throws before returning, node exits
non-zero and _run_node's assertion fails loudly, instead of silently
making every case look identical.
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"
LOCALES_DIR = (
    Path(__file__).resolve().parents[3] / "hub_server" / "infrastructure" / "locales"
)

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

NEW_KEYS = [
    "readiness.blocked_title",
    "readiness.reason_state",
    "readiness.reason_target",
    "readiness.reason_registrations",
    "readiness.reason_teams",
    "readiness.reason_stations",
]

LOCALE_FILES = ["de-CH", "en-US", "fr", "it", "sv", "zh-TW"]


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _stripped_script() -> str:
    source = _read()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _extract_readiness_functions() -> str:
    """readinessBlockingReasons() and readinessNoticeHtml(), from a
    comment-stripped script, up to (not including) setRaceStageOverride.
    Grabbing the real function bodies means a wrong filter, a dropped
    guard, or a leaked English string is exercised, not just
    pattern-matched."""
    script = _stripped_script()
    start = script.index("function readinessBlockingReasons")
    end = script.index("function setRaceStageOverride", start)
    return script[start:end]


def _extract_fetch_nodes() -> str:
    """The real fetchNodes(), from a comment-stripped script, up to (not
    including) fetchReadiness(). Extracting the actual body -- rather than
    grepping the source for the substring "fetchReadiness()" -- means a
    deleted call is exercised (the recording stub never fires), not just
    pattern-matched; a regex check would pass even on a commented-out
    call."""
    script = _stripped_script()
    start = script.index("async function fetchNodes")
    end = script.index("async function fetchReadiness", start)
    return script[start:end]


def _extract_render_race_stage_banner() -> str:
    """The real renderRaceStageBanner(), from a comment-stripped script, up
    to (not including) readinessBlockingReasons. Grabbing the actual body
    means a revert to the old unconditional
    `...innerText = details.sub` is exercised, not just pattern-matched."""
    script = _stripped_script()
    start = script.index("function renderRaceStageBanner")
    end = script.index("function readinessBlockingReasons", start)
    return script[start:end]


def _run_node(js_source: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(js_source)
        tmp_file.flush()
        tmp_path = tmp_file.name
    try:
        result = subprocess.run(
            ["node", tmp_path], capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0, f"node failed: {result.stderr}"
        return result.stdout
    finally:
        Path(tmp_path).unlink()


_STUB_PREFIX = """
const MESSAGES = {
  "readiness.blocked_title": "BLOCKED_TITLE",
  "readiness.reason_state": "REASON_STATE",
  "readiness.reason_target": "REASON_TARGET",
  "readiness.reason_registrations": "REASON_REGISTRATIONS",
  "readiness.reason_teams": "REASON_TEAMS",
  "readiness.reason_stations": "REASON_STATIONS:{stations}",
};
function t(key, params = {}) {
  let value = MESSAGES[key] || key;
  Object.entries(params).forEach(([name, replacement]) => {
    value = value.replaceAll(`{${name}}`, String(replacement));
  });
  return value;
}
function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
"""


def _ok_check():
    return {"status": "ok", "message": "fine"}


def _block_check(message="blocked"):
    return {"status": "block", "message": message}


def _base_checks(**overrides):
    checks = {
        "state": _ok_check(),
        "target": _ok_check(),
        "registrations": _ok_check(),
        "teams": _ok_check(),
        "stations": _ok_check(),
        "sound": _ok_check(),
    }
    checks.update(overrides)
    return checks


def _render(readiness, race_state="READY"):
    body = _extract_readiness_functions()
    harness = f"""
{_STUB_PREFIX}
{body}

const readiness = {json.dumps(readiness)};
const result = readinessNoticeHtml(readiness, {json.dumps(race_state)});
console.log(JSON.stringify({{ result }}));
"""
    output = _run_node(harness)
    return json.loads(output.strip().splitlines()[-1])["result"]


def test_readiness_functions_are_actually_defined():
    body = _extract_readiness_functions()
    assert "function readinessBlockingReasons" in body
    assert "function readinessNoticeHtml" in body


def test_stations_blocking_lists_both_offline_station_numbers():
    readiness = {
        "ready": False,
        "checks": _base_checks(stations=_block_check()),
        "station_health": [
            {"station_number": 3, "health": "stale"},
            {"station_number": 7, "health": "missing"},
        ],
        "blocking_issues": ["Assign at least one station before starting."],
    }
    html = _render(readiness)
    assert html is not None
    assert "BLOCKED_TITLE" in html
    assert "REASON_STATIONS:" in html
    assert "3" in html
    assert "7" in html


def test_online_station_is_not_listed_as_offline():
    readiness = {
        "ready": False,
        "checks": _base_checks(stations=_block_check()),
        "station_health": [
            {"station_number": 1, "health": "online"},
            {"station_number": 9, "health": "stale"},
        ],
        "blocking_issues": ["Assign at least one station before starting."],
    }
    html = _render(readiness)
    # Extract the REASON_STATIONS segment and check its station list only.
    segment = html.split("REASON_STATIONS:", 1)[1]
    assert "9" in segment
    assert "1" not in segment.split(" · ")[0]


def test_two_blocking_checks_both_reasons_appear_joined():
    readiness = {
        "ready": False,
        "checks": _base_checks(target=_block_check(), teams=_block_check()),
        "station_health": [],
        "blocking_issues": ["Target value must be greater than 0."],
    }
    html = _render(readiness)
    assert "REASON_TARGET" in html
    assert "REASON_TEAMS" in html
    assert " · " in html


def test_ready_true_renders_no_notice():
    readiness = {
        "ready": True,
        "checks": _base_checks(),
        "station_health": [],
        "blocking_issues": [],
    }
    assert _render(readiness) is None


def test_running_state_renders_no_notice_even_if_not_ready():
    readiness = {
        "ready": False,
        "checks": _base_checks(stations=_block_check()),
        "station_health": [{"station_number": 2, "health": "missing"}],
        "blocking_issues": ["Assign at least one station before starting."],
    }
    assert _render(readiness, race_state="RUNNING") is None


def test_english_blocking_issue_sentence_never_leaks_into_output():
    leaked_sentence = "Assign at least one station before starting."
    readiness = {
        "ready": False,
        "checks": _base_checks(stations=_block_check()),
        "station_health": [{"station_number": 4, "health": "missing"}],
        "blocking_issues": [leaked_sentence],
    }
    html = _render(readiness)
    assert leaked_sentence not in html


# ---------------------------------------------------------------------------
# Wiring -- the pure functions above are well covered, but nothing pinned
# that fetchNodes() actually calls fetchReadiness(), or that
# renderRaceStageBanner() actually routes readinessNoticeHtml()'s result
# onto the page. Both edits leave the whole suite green while silently
# killing the feature on the real screen; these tests fail under either.
# ---------------------------------------------------------------------------

_FETCH_NODES_HARNESS_PREFIX = """
const consoleCalls = [];
const console = {
  log: (...args) => consoleCalls.push(["log", args]),
  error: (...args) => consoleCalls.push(["error", args]),
};
async function fetch(url) {
  return { json: async () => ({ nodes: [] }) };
}
let renderEdgeNodesCalls = 0;
function renderEdgeNodes(nodes) { renderEdgeNodesCalls += 1; }
let checkBuildFreshnessCalls = 0;
function checkBuildFreshness() { checkBuildFreshnessCalls += 1; }
let fetchReadinessCalls = 0;
async function fetchReadiness() { fetchReadinessCalls += 1; }
"""


def test_fetch_nodes_actually_invokes_fetch_readiness():
    """Regression for deleting `fetchReadiness();` from the end of
    fetchNodes(): without it, latestReadiness stays null forever and the
    notice never appears, but every existing test of the pure functions
    still passes. This runs the real fetchNodes() under node (awaited, so
    a missing call cannot be mistaken for a timing artefact) with a
    recording stub for fetchReadiness and asserts it actually fired."""
    body = _extract_fetch_nodes()
    harness = f"""
{_FETCH_NODES_HARNESS_PREFIX}
{body}

(async () => {{
  await fetchNodes();
  process.stdout.write(JSON.stringify({{
    fetchReadinessCalls,
    renderEdgeNodesCalls,
    checkBuildFreshnessCalls,
  }}));
}})();
"""
    output = _run_node(harness)
    result = json.loads(output.strip().splitlines()[-1])
    assert result["fetchReadinessCalls"] == 1
    assert result["renderEdgeNodesCalls"] == 1
    assert result["checkBuildFreshnessCalls"] == 1


def _make_stage_banner_dom_harness(notice_html):
    return f"""
function makeEl() {{
  return {{
    _innerText: null,
    _innerHTML: null,
    _className: null,
    set innerText(v) {{ this._innerText = v; }},
    get innerText() {{ return this._innerText; }},
    set innerHTML(v) {{ this._innerHTML = v; }},
    get innerHTML() {{ return this._innerHTML; }},
    set className(v) {{ this._className = v; }},
    get className() {{ return this._className; }},
  }};
}}
const elements = {{
  "race-stage-banner": makeEl(),
  "race-stage-kicker": makeEl(),
  "race-stage-main": makeEl(),
  "race-stage-sub": makeEl(),
  "race-stage-timer": makeEl(),
}};
const document = {{ getElementById: (id) => elements[id] || null }};

let currentState = "READY";
let raceStageOverride = null;
let latestReadiness = null;

function getRaceStageDetails(stage) {{
  return {{ className: "ready", kicker: "KICKER", main: "MAIN", sub: "DETAILS_SUB", timer: "TIMER" }};
}}

function readinessNoticeHtml(readiness, raceState) {{
  return {json.dumps(notice_html)};
}}
"""


def _render_stage_banner(notice_html):
    body = _extract_render_race_stage_banner()
    harness = f"""
{_make_stage_banner_dom_harness(notice_html)}
{body}

renderRaceStageBanner();

console.log(JSON.stringify({{
  subInnerHTML: elements["race-stage-sub"]._innerHTML,
  subInnerText: elements["race-stage-sub"]._innerText,
}}));
"""
    output = _run_node(harness)
    return json.loads(output.strip().splitlines()[-1])


def test_render_race_stage_banner_routes_notice_html_onto_sub_element():
    """Regression for reverting the `#race-stage-sub` block back to the old
    unconditional `...innerText = details.sub`: readinessNoticeHtml() would
    still compute the right string, but it would never reach the page. This
    runs the real renderRaceStageBanner() under node with a stubbed DOM and
    a readinessNoticeHtml() stub returning a fixed string, and asserts that
    string actually lands on race-stage-sub's innerHTML (not innerText)."""
    result = _render_stage_banner("NOTICE_HTML")
    assert result["subInnerHTML"] == "NOTICE_HTML"
    assert result["subInnerText"] is None


def test_render_race_stage_banner_falls_back_to_details_sub_when_no_notice():
    """The other half of the same wiring: when readinessNoticeHtml()
    returns null (ready, or race RUNNING), the banner must fall back to
    details.sub via innerText, not leave stale notice HTML in place."""
    result = _render_stage_banner(None)
    assert result["subInnerText"] == "DETAILS_SUB"
    assert result["subInnerHTML"] is None


# ---------------------------------------------------------------------------
# Locale parity -- plain data test, no node needed.
# ---------------------------------------------------------------------------


def test_all_locales_contain_new_keys_with_identical_key_sets():
    key_sets = {}
    for name in LOCALE_FILES:
        data = json.loads((LOCALES_DIR / f"{name}.json").read_text(encoding="utf-8"))
        for key in NEW_KEYS:
            assert key in data, f"{name}.json missing key {key}"
            assert data[key].strip(), f"{name}.json has empty value for {key}"
        key_sets[name] = set(data.keys())

    reference = key_sets[LOCALE_FILES[0]]
    for name in LOCALE_FILES[1:]:
        assert (
            key_sets[name] == reference
        ), f"{name}.json key set differs from {LOCALE_FILES[0]}.json"
