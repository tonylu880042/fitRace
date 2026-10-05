"""C5: Game Admin controls for challenge mode (enable + duration), POSTing to
/api/race/challenge. Runs the real saveChallengeMode()/renderChallengeMode()
from gameAdmin.html under node. Dashboard and System Admin must not carry
these controls."""

import json
import re
import subprocess
from pathlib import Path

STATIC = Path(__file__).resolve().parents[3] / "hub_server" / "static"
_LINE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


def _source(name="gameAdmin.html"):
    return _LINE.sub("", _BLOCK.sub("", (STATIC / name).read_text(encoding="utf-8")))


def _function(source, name):
    marker = f"function {name}("
    start = source.index(marker)
    a = source.rfind("async ", 0, start)
    if a != -1 and source[a:start] == "async ":
        start = a
    i = source.index("{", source.index(")", start))
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


def _run(body, fail=False):
    src = _source()
    fns = "\n".join(
        _function(src, n)
        for n in ("fetchJson", "saveChallengeMode", "renderChallengeMode")
    )
    js = f"""
const elements = {{}};
function $(id) {{ return elements[id] || (elements[id] = {{ value: "", checked: false, disabled: false, textContent: "" }}); }}
function t(k) {{ return k; }}
function setMessage(id, text, kind) {{ $(id).textContent = text; $(id).kind = kind; }}
function adminHeaders(extra = {{}}) {{ return {{ ...extra, "X-FitRace-Admin-Token": "secret" }}; }}
let state = {{ race: {{ state: "IDLE" }} }};
function renderRace() {{}}
const calls = [];
global.fetch = async (url, options) => {{
  calls.push({{ url, options }});
  return {{ ok: {str(not fail).lower()}, status: {500 if fail else 200}, statusText: "x",
    text: async () => JSON.stringify({{ state: "READY", challenge_mode_enabled: true, challenge_duration_sec: 90, detail: "boom" }}) }};
}};
{fns}
(async () => {{ {body} }})();
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_save_posts_enabled_and_duration_with_admin_headers():
    result = _run("""
$("challenge-mode-enabled").value = "true";
$("challenge-duration").value = "90";
await saveChallengeMode();
console.log(JSON.stringify({ url: calls[0].url, method: calls[0].options.method,
  body: JSON.parse(calls[0].options.body),
  token: calls[0].options.headers["X-FitRace-Admin-Token"], race: state.race }));
""")
    assert result["url"] == "/api/race/challenge"
    assert result["method"] == "POST"
    assert result["body"]["enabled"] is True
    assert result["body"]["duration_sec"] == 90
    assert result["token"] == "secret"
    assert result["race"]["challenge_duration_sec"] == 90


def test_save_sends_disabled_when_switched_off():
    result = _run("""
$("challenge-mode-enabled").value = "false";
$("challenge-duration").value = "180";
await saveChallengeMode();
console.log(JSON.stringify({ body: JSON.parse(calls[0].options.body) }));
""")
    assert result["body"]["enabled"] is False


def test_save_failure_shows_error_and_keeps_state():
    result = _run(
        """
$("challenge-mode-enabled").value = "true";
$("challenge-duration").value = "90";
await saveChallengeMode();
console.log(JSON.stringify({ kind: $("race-message").kind, race: state.race }));
""",
        fail=True,
    )
    assert result["kind"] == "error"
    assert result["race"] == {"state": "IDLE"}


def test_render_fills_controls_from_state_and_locks_them_while_running():
    result = _run("""
state.race = { state: "RUNNING", challenge_mode_enabled: true, challenge_duration_sec: 120 };
renderChallengeMode();
const running = { enabled: $("challenge-mode-enabled").value, duration: $("challenge-duration").value,
  locked: $("btn-save-challenge").disabled };
state.race = { state: "READY", challenge_mode_enabled: false, challenge_duration_sec: 180 };
renderChallengeMode();
console.log(JSON.stringify({ running, ready: { enabled: $("challenge-mode-enabled").value,
  locked: $("btn-save-challenge").disabled } }));
""")
    assert result["running"] == {"enabled": "true", "duration": "120", "locked": True}
    assert result["ready"] == {"enabled": "false", "locked": False}


def test_render_race_calls_render_challenge_mode():
    assert "renderChallengeMode()" in _function(_source(), "renderRace")


def test_dashboard_and_system_admin_have_no_challenge_controls():
    for page in ("index.html", "systemAdmin.html", "classAdmin.html"):
        assert "/api/race/challenge" not in _source(page), page
        assert "challenge-mode-enabled" not in _source(page), page
