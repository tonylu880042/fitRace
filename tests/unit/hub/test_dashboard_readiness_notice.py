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
