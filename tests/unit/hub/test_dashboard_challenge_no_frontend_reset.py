"""R3: in challenge mode the backend owns the reset (it waits for the next
sign-up), so the dashboard must never start its own auto-reset countdown.
Runs the real syncAutoReset() from index.html under node."""

import json
import re
import subprocess
from pathlib import Path

INDEX = Path(__file__).resolve().parents[3] / "hub_server" / "static" / "index.html"
_LINE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


def _script():
    src = INDEX.read_text(encoding="utf-8")
    start = src.index("<script>") + len("<script>")
    return _LINE.sub("", _BLOCK.sub("", src[start : src.index("</script>", start)]))


def _function(source, name):
    start = source.index(f"function {name}(")
    i = source.index("{", start)
    depth, in_str = 0, None
    while True:
        ch = source[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'`":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1


def _run(state, data, interval="null"):
    js = f"""
let currentState = {json.dumps(state)};
let autoResetInterval = {interval};
let autoResetCancelled = false;
let celebrationShownForRaceKey = "k";
const calls = [];
function startAutoResetCountdown() {{ calls.push("start"); autoResetInterval = 1; }}
function cancelAutoReset(manual) {{ calls.push("cancel"); autoResetInterval = null; }}
{_function(_script(), "syncAutoReset")}
syncAutoReset({json.dumps(data)});
console.log(JSON.stringify({{ calls, celebration: celebrationShownForRaceKey }}));
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_stopped_starts_the_frontend_countdown_when_not_in_challenge_mode():
    assert _run("STOPPED", {"challenge_mode_enabled": False})["calls"] == ["start"]
    assert _run("STOPPED", {})["calls"] == ["start"]


def test_stopped_in_challenge_mode_never_starts_the_countdown():
    assert "start" not in _run("STOPPED", {"challenge_mode_enabled": True})["calls"]


def test_challenge_mode_cancels_a_countdown_already_running():
    assert _run("STOPPED", {"challenge_mode_enabled": True}, interval="7")["calls"] == [
        "cancel"
    ]


def test_leaving_stopped_cancels_and_clears_celebration_on_ready():
    result = _run("READY", {})
    assert result["calls"] == ["cancel"]
    assert result["celebration"] is None
