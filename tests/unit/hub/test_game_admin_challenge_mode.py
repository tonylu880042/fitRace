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


_CONTROLS = ("challenge-mode-enabled", "challenge-duration", "challenge-min-result")


def _onchange_handler(control_id):
    """The inline onchange of the real control, straight from the page."""
    html = _source()
    match = re.search(r'<(?:select|input)[^>]*id="%s"[^>]*>' % control_id, html)
    assert match, control_id
    handler = re.search(r'onchange="([^"]*)"', match.group(0))
    return handler.group(1) if handler else ""


def _run(body, fail=False, state_race=None):
    src = _source()
    fns = "\n".join(
        _function(src, n)
        for n in ("fetchJson", "saveChallengeMode", "renderChallengeMode")
    )
    initial = json.dumps(
        state_race
        or {
            "state": "IDLE",
            "challenge_mode_enabled": False,
            "challenge_duration_sec": 180,
            "challenge_min_result_sec": 10,
        }
    )
    js = f"""
const elements = {{}};
function $(id) {{ return elements[id] || (elements[id] = {{ value: "", checked: false, disabled: false, textContent: "" }}); }}
function t(k) {{ return k; }}
function setMessage(id, text, kind) {{ $(id).textContent = text; $(id).kind = kind; }}
function adminHeaders(extra = {{}}) {{ return {{ ...extra, "X-FitRace-Admin-Token": "secret" }}; }}
let state = {{ race: {initial} }};
function renderRace() {{ renderChallengeMode(); }}
const calls = [];
global.fetch = async (url, options) => {{
  calls.push({{ url, options }});
  const body = JSON.parse(options.body);
  return {{ ok: {str(not fail).lower()}, status: {500 if fail else 200}, statusText: "x",
    text: async () => JSON.stringify({{ state: "READY", challenge_mode_enabled: body.enabled,
      challenge_duration_sec: body.duration_sec, challenge_min_result_sec: body.min_result_sec, detail: "boom" }}) }};
}};
{fns}
renderChallengeMode();
(async () => {{ {body} }})();
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _change(control_id, value):
    handler = json.dumps(_onchange_handler(control_id))
    return f"""
$("{control_id}").value = "{value}";
await eval({handler});
"""


def test_changing_the_select_posts_all_three_values_immediately():
    result = _run(_change("challenge-mode-enabled", "true") + """
console.log(JSON.stringify({ n: calls.length, url: calls[0].url, method: calls[0].options.method,
  body: JSON.parse(calls[0].options.body),
  token: calls[0].options.headers["X-FitRace-Admin-Token"] }));
""")
    assert result["n"] == 1
    assert result["url"] == "/api/race/challenge"
    assert result["method"] == "POST"
    assert result["body"] == {
        "enabled": True,
        "duration_sec": 180,
        "min_result_sec": 10,
    }
    assert result["token"] == "secret"


def test_changing_either_number_input_posts_too():
    for control, value, key, expected in (
        ("challenge-duration", "90", "duration_sec", 90),
        ("challenge-min-result", "25", "min_result_sec", 25),
    ):
        result = _run(
            _change(control, value)
            + "console.log(JSON.stringify({ n: calls.length, body: JSON.parse(calls[0].options.body) }));"
        )
        assert result["n"] == 1, control
        assert result["body"][key] == expected, control


def test_state_change_render_after_a_successful_save_keeps_the_saved_value():
    result = _run(_change("challenge-mode-enabled", "true") + """
// A later state_change broadcast re-renders from state.race.
renderChallengeMode();
console.log(JSON.stringify({ enabled: $("challenge-mode-enabled").value }));
""")
    assert result["enabled"] == "true"


def test_failed_post_restores_the_previous_values_and_shows_the_error():
    result = _run(
        _change("challenge-mode-enabled", "true") + """
console.log(JSON.stringify({ enabled: $("challenge-mode-enabled").value,
  duration: $("challenge-duration").value, kind: $("race-message").kind,
  text: $("race-message").textContent }));
""",
        fail=True,
    )
    assert result["enabled"] == "false"
    assert result["duration"] == "180"
    assert result["kind"] == "error"
    assert result["text"]


def test_all_three_controls_are_disabled_while_running():
    result = _run("""
state.race = { state: "RUNNING", challenge_mode_enabled: true, challenge_duration_sec: 120, challenge_min_result_sec: 40 };
renderChallengeMode();
const running = ["challenge-mode-enabled", "challenge-duration", "challenge-min-result"].map((id) => $(id).disabled);
const values = [$("challenge-mode-enabled").value, $("challenge-duration").value, $("challenge-min-result").value];
state.race = { state: "READY", challenge_mode_enabled: false, challenge_duration_sec: 180, challenge_min_result_sec: 10 };
renderChallengeMode();
console.log(JSON.stringify({ running, values, ready: ["challenge-mode-enabled", "challenge-duration", "challenge-min-result"].map((id) => $(id).disabled) }));
""")
    assert result["running"] == [True, True, True]
    assert result["values"] == ["true", "120", "40"]
    assert result["ready"] == [False, False, False]


def test_there_is_no_separate_challenge_save_button_or_orphaned_strings():
    html = _source()
    assert 'id="btn-save-challenge"' not in html
    assert "button.save_challenge" not in html


def test_render_race_calls_render_challenge_mode():
    assert "renderChallengeMode()" in _function(_source(), "renderRace")


def test_dashboard_and_system_admin_have_no_challenge_controls():
    for page in ("index.html", "systemAdmin.html", "classAdmin.html"):
        assert "/api/race/challenge" not in _source(page), page
        assert "challenge-mode-enabled" not in _source(page), page
