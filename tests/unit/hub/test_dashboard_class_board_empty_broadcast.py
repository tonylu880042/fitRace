"""An empty WS payload must not wipe the class board's stations.

The hub broadcasts a bare `{}` whenever telemetry arrives that produces no
progress entry -- `mqtt_subscriber.py` sends it purely as a "refresh your node
list" ping. Class mode used to store it as the new leaderboard, so a single
such ping blanked every station card on a projector mid-class and only a
manual page reload brought them back (observed on the venue hub: 1,422 empty
broadcasts in five minutes while the hub itself still had 24 stations).
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


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


_STATION = {
    "node_id": "n1",
    "station_number": 1,
    "athlete_name": "Athlete n1",
    "power_watts": 120,
    "instantaneous_speed_kph": 22,
    "distance_m": 300,
}


def _run(payload_js: str, starting_leaderboard: dict) -> dict:
    """Feed one WS payload to renderLeaderboard in class mode."""
    script = (
        "let currentSessionMode = 'class';\n"
        f"let currentClassLeaderboard = {json.dumps(starting_leaderboard)};\n"
        "let renderCalls = 0;\n"
        "function renderClassBoardFromState() { renderCalls += 1; }\n"
        + _extract_function(_read_index(), "renderLeaderboard")
        + f"\nrenderLeaderboard({payload_js});\n"
        + "console.log(JSON.stringify({"
        "leaderboard: currentClassLeaderboard, renderCalls}));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}")
    return json.loads(result.stdout)


def test_empty_broadcast_keeps_the_stations_already_on_the_board():
    result = _run("{}", {"n1": _STATION})
    assert list(result["leaderboard"]) == ["n1"], (
        "an empty refresh ping blanked the class board -- the projector shows "
        "a class with no stations until someone reloads the page"
    )


def test_empty_broadcast_still_re_renders_so_the_clock_keeps_ticking():
    result = _run("{}", {"n1": _STATION})
    assert result["renderCalls"] == 1


def test_a_real_progress_payload_still_replaces_the_leaderboard():
    """The guard must not freeze the board: a station leaving the class has
    to disappear when telemetry says so."""
    payload = json.dumps({"n2": dict(_STATION, node_id="n2", station_number=2)})
    result = _run(payload, {"n1": _STATION})
    assert list(result["leaderboard"]) == ["n2"]


def test_null_payload_is_treated_like_an_empty_one():
    result = _run("null", {"n1": _STATION})
    assert list(result["leaderboard"]) == ["n1"]


def test_the_hub_still_sends_the_empty_ping_this_guard_exists_for():
    """Pin the producer: if the hub ever stops broadcasting a bare {}, this
    guard (and its comment) are describing something that no longer happens."""
    adapter = (
        Path(__file__).resolve().parents[3]
        / "hub_server"
        / "adapters"
        / "mqtt_subscriber.py"
    ).read_text(encoding="utf-8")
    assert re.search(r"broadcast\(\{\}\)", adapter)
