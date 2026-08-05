"""Regression tests for the actual readiness->color STATE MAPPING in Game
Admin (hub_server/static/gameAdmin.html), not merely the presence of a CSS
class name in the stylesheet.

Gap this closes: tests/unit/hub/test_game_admin_race_control_restructure.py
asserts that `.readiness-card.block` exists in <style> and that
`.readiness-check.block` no longer does. Neither of those assertions
executes the JS that decides WHEN the "block" class is actually applied.
A mutation of

    const statusClass = readiness.ready ? "ok" : "block";
    ->  const statusClass = "ok";

(readiness card can never render red -- the single most important signal
in the whole redesign) or an inverted mapping

    ->  const statusClass = readiness.ready ? "block" : "ok";

(a ready race renders as blocked, and vice versa) both leave every
existing CSS-presence assertion green. This is exactly the "assertion
satisfied by adjacent evidence" trap CLAUDE.md warns about.

This module follows the same technique as tests/unit/hub/
test_system_admin_orphaned_stations.py's "real behavioral proof" section:
extract the actual function source out of the page's inline <script> (with
JS comments stripped) and execute it for real under node, rather than
pattern-matching the surrounding markup. It covers two mapping points:

1. The readiness CARD: renderReadinessPanel()'s
   `readiness.ready ? "ok" : "block"` ternary -- executed end-to-end so a
   removed or inverted branch is caught by the actual rendered HTML.
2. The per-CHECK dot: readinessStatusClass(status), both as a pure
   function (ok/warn/block/unknown -> the right CSS suffix) and as it is
   actually wired into renderReadinessPanel's checksHtml (so a mutation
   that stops calling it, e.g. hardcoding `const klass = "ok"`, is also
   caught).
"""

import re
import subprocess
import tempfile
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _stripped_script() -> str:
    source = _read()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _extract_readiness_functions() -> str:
    """readinessStatusClass() and renderReadinessPanel() together, from a
    comment-stripped script, up to (not including) the next function
    (stationReasonText). Grabbing both in one slice means the harness
    below runs the REAL readinessStatusClass, not a hand-written stub --
    so a mutation to either function is exercised."""
    script = _stripped_script()
    start = script.index("function readinessStatusClass")
    end = script.index("function stationReasonText", start)
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


def test_readiness_functions_are_actually_defined():
    body = _extract_readiness_functions()
    assert "function readinessStatusClass(status)" in body
    assert "function renderReadinessPanel()" in body


# ---------------------------------------------------------------------------
# 1. readinessStatusClass() as a pure function: exact ok/warn/block/unknown
#    mapping, executed for real (not pattern-matched).
# ---------------------------------------------------------------------------


def test_readiness_status_class_maps_each_status_to_the_matching_color():
    body = _extract_readiness_functions()
    harness = f"""
{body}

console.log("ok=" + readinessStatusClass("ok"));
console.log("warn=" + readinessStatusClass("warn"));
console.log("block=" + readinessStatusClass("block"));
console.log("unknown=" + readinessStatusClass("something-else"));
"""
    output = _run_node(harness)
    assert "ok=ok" in output
    assert "warn=warn" in output
    assert "block=block" in output
    assert "unknown=info" in output


# ---------------------------------------------------------------------------
# 2. renderReadinessPanel(), executed end to end with a mock $()/t()/
#    escapeHtml(), proving the CARD actually flips to "block" when the
#    race is not ready, and to "ok" when it is.
# ---------------------------------------------------------------------------

_DOM_HARNESS_PREFIX = """
const mockElements = {};
function $(id) {
  if (!mockElements[id]) mockElements[id] = {};
  return mockElements[id];
}
function t(key) { return key; }
function escapeHtml(value) { return String(value ?? ""); }
const state = {};
"""


def _render_with_readiness(ready: bool) -> str:
    body = _extract_readiness_functions()
    harness = f"""
{_DOM_HARNESS_PREFIX}
{body}

state.readiness = {{
  ready: {"true" if ready else "false"},
  checks: {{}},
  blocking_issues: [],
  warnings: [],
  station_health: [],
}};
renderReadinessPanel();
console.log(mockElements["race-readiness-panel"].innerHTML);
"""
    return _run_node(harness)


def test_ready_race_renders_the_ok_card_not_block():
    html = _render_with_readiness(ready=True)
    assert "readiness-card ok" in html
    assert "readiness-card block" not in html


def test_not_ready_race_renders_the_block_card_not_ok():
    html = _render_with_readiness(ready=False)
    assert "readiness-card block" in html
    assert "readiness-card ok" not in html


# ---------------------------------------------------------------------------
# 3. The per-check dot: proves renderReadinessPanel actually threads each
#    check's own status through readinessStatusClass into its own dot,
#    rather than e.g. hardcoding one class for every check.
# ---------------------------------------------------------------------------


def test_each_readiness_check_gets_its_own_dot_color_from_its_own_status():
    body = _extract_readiness_functions()
    harness = f"""
{_DOM_HARNESS_PREFIX}
{body}

state.readiness = {{
  ready: true,
  checks: {{
    state: {{status: "block", message: "m1"}},
    target: {{status: "warn", message: "m2"}},
    registrations: {{status: "ok", message: "m3"}},
    teams: {{status: "ok", message: "m4"}},
    stations: {{status: "block", message: "m5"}},
    sound: {{status: "warn", message: "m6"}},
  }},
  blocking_issues: [],
  warnings: [],
  station_health: [],
}};
renderReadinessPanel();
const html = mockElements["race-readiness-panel"].innerHTML;

function dotFor(labelKey) {{
  const pattern = new RegExp(
    'readiness-check-dot ([a-z]+)"></span>\\\\s*<div class="readiness-check-body">\\\\s*<div class="readiness-check-title">' +
    labelKey.replace(".", "\\\\.")
  );
  const match = html.match(pattern);
  return match ? match[1] : "NOT_FOUND";
}}

console.log("state=" + dotFor("check.state"));
console.log("target=" + dotFor("check.target"));
console.log("registrations=" + dotFor("check.registrations"));
console.log("teams=" + dotFor("check.teams"));
console.log("stations=" + dotFor("check.stations"));
console.log("sound=" + dotFor("check.sound"));
"""
    output = _run_node(harness)
    assert "state=block" in output
    assert "target=warn" in output
    assert "registrations=ok" in output
    assert "teams=ok" in output
    assert "stations=block" in output
    assert "sound=warn" in output
