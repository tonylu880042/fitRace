"""Regression test for the dashboard's (hub_server/static/index.html) race
clock when a distance/calories race is stopped with unfinished runners.

Problem (reproduced on real hardware): an individual 500m distance race with
4 runners was stopped (POST /api/race/stop) at 2:40.9 while 2 runners had
finished (finished_time_ms 134076 / 150790) and 2 had not (progress_percent
93.2% / 79.7%, no finished_time_ms). updateStopwatch() unconditionally
replaced the elapsed clock with Math.max(...finished_time_ms) = 02:30.7 for
any STOPPED distance/calories race, hiding the real stop time
(raceEndTime - raceStartTime = 2:40.9).

Fix: only substitute the last finisher's time when EVERY competitor has
finished (the existing allFinished check: every node has finished_time_ms
or progress_percent >= 100). If the race is STOPPED and ANY competitor has
NOT finished, the clock must show the actual stop time.

This extracts the real updateStopwatch() (plus its real helpers
formatElapsedTime/metricNumber) out of the page's inline <script> and runs
it under node with a stubbed DOM/renderRaceStageBanner, per the technique
established in tests/unit/hub/test_dashboard_readiness_notice.py. Asserting
on the actual computed clock string -- not a substring near a comment --
means reverting the allFinished guard is caught.
"""

import json
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


def _stripped_script() -> str:
    source = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _extract(name_start: str, name_end: str) -> str:
    script = _stripped_script()
    start = script.index(name_start)
    end = script.index(name_end, start)
    return script[start:end]


def _extract_update_stopwatch() -> str:
    """The real updateStopwatch(), from a comment-stripped script, up to
    (not including) configureRace(). Grabbing the actual body means a
    reverted allFinished guard is exercised, not just pattern-matched."""
    return _extract("function updateStopwatch", "async function configureRace")


def _extract_format_elapsed_time() -> str:
    return _extract("function formatElapsedTime", "function getClassStageDetails")


def _extract_metric_number() -> str:
    script = _stripped_script()
    start = script.index("function metricNumber(value, fallback = 0)")
    end = script.index("\n    }\n", start) + len("\n    }\n")
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


def _harness(
    *,
    current_state,
    race_start_time,
    race_end_time,
    nodes,
    race_type="distance",
):
    body = _extract_update_stopwatch()
    format_elapsed = _extract_format_elapsed_time()
    metric_number = _extract_metric_number()
    return f"""
{metric_number}
{format_elapsed}

let stopwatchInterval = null;
function clearInterval() {{}}
const labelEl = {{ innerText: null }};
const document = {{
  getElementById: (id) => (id === "stopwatch-lbl" ? labelEl : null),
}};
let renderRaceStageBannerCalls = 0;
function renderRaceStageBanner() {{ renderRaceStageBannerCalls += 1; }}

const currentState = {json.dumps(current_state)};
const raceStartTime = {json.dumps(race_start_time)};
const raceEndTime = {json.dumps(race_end_time)};
const leaderboardNodes = {json.dumps(nodes)};
const currentConfig = {{ race_type: {json.dumps(race_type)}, duration_sec: 0 }};

{body}

updateStopwatch();
console.log(JSON.stringify({{ label: labelEl.innerText }}));
"""


def _run(**kwargs):
    output = _run_node(_harness(**kwargs))
    return json.loads(output.strip().splitlines()[-1])["label"]


def test_updates_are_actually_defined():
    body = _extract_update_stopwatch()
    assert "function updateStopwatch" in body


def test_stopped_race_with_unfinished_runners_shows_stop_time_not_last_finisher():
    # 2 finished (134076ms, 150790ms), 2 unfinished (93.2%, 79.7%); stopped
    # at 2:40.9 = 160900ms after a 100ms start epoch.
    nodes = [
        {"finished_time_ms": 134076, "progress_percent": 100},
        {"finished_time_ms": 150790, "progress_percent": 100},
        {"finished_time_ms": None, "progress_percent": 93.2},
        {"finished_time_ms": None, "progress_percent": 79.7},
    ]
    label = _run(
        current_state="STOPPED",
        race_start_time=100,
        race_end_time=161000,
        nodes=nodes,
    )
    assert label == "02:40.9"
    assert label != "02:30.7"


def test_stopped_race_fully_finished_still_shows_last_finisher_time():
    nodes = [
        {"finished_time_ms": 134076, "progress_percent": 100},
        {"finished_time_ms": 150790, "progress_percent": 100},
    ]
    label = _run(
        current_state="STOPPED",
        race_start_time=100,
        race_end_time=200100,
        nodes=nodes,
    )
    assert label == "02:30.7"


def test_calories_race_type_also_uses_stop_time_when_unfinished():
    nodes = [
        {"finished_time_ms": 90000, "progress_percent": 100},
        {"finished_time_ms": None, "progress_percent": 50},
    ]
    label = _run(
        current_state="STOPPED",
        race_start_time=100,
        race_end_time=120100,
        nodes=nodes,
        race_type="calories",
    )
    assert label == "02:00.0"
