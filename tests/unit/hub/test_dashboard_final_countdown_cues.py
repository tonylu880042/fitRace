"""The dashboard plays the hub's final-seconds countdown cues (race_event
"countdown": 10, 5 tick; 3, 2, 1 spoken) with a big 3/2/1 overlay, plus the
finish sound when a timed race stops. Runs the real page functions -- the
WebSocket dispatch included -- under node."""

import json
import re
import subprocess
from pathlib import Path

INDEX = Path(__file__).resolve().parents[3] / "hub_server" / "static" / "index.html"
_LINE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


def _raw():
    src = INDEX.read_text(encoding="utf-8")
    return src


def _script():
    src = _raw()
    start = src.index("<script>") + len("<script>")
    return _LINE.sub("", _BLOCK.sub("", src[start : src.index("</script>", start)]))


def _function(source, name):
    start = source.index(f"function {name}(")
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


_NAMES = (
    "countdownCueSound",
    "playRaceCue",
    "showFinalSecondsOverlay",
    "handleRaceEvent",
    "shouldPlayFinishCue",
    "connectWebSocket",
)


def _run(messages, sound_enabled=True, reject_play=False):
    script = _script()
    fns = "\n".join(_function(script, n) for n in _NAMES)
    js = f"""
const audioIds = ["race-cue-tick-audio", "race-cue-3-audio", "race-cue-2-audio", "race-cue-1-audio", "race-cue-finish-audio"];
const plays = [];
const audios = {{}};
audioIds.forEach((id) => {{
  audios[id] = {{ id, currentTime: 0, play() {{ plays.push(id); return {json.dumps(reject_play)} ? Promise.reject(new Error("blocked")) : Promise.resolve(); }} }};
}});
const overlays = [];
let currentSoundEnabled = {json.dumps(sound_enabled)};
const document = {{
  getElementById: (id) => audios[id] || null,
  createElement: () => {{ const e = {{ id: "", className: "", innerHTML: "", classList: {{ add() {{}}, remove() {{}} }}, remove() {{}} }}; overlays.push(e); return e; }},
  body: {{ appendChild() {{}} }},
}};
function requestAnimationFrame(fn) {{ fn(); }}
const window = {{ location: {{ protocol: "http:", host: "h" }}, setTimeout() {{ return 1; }} }};
class Audio {{ constructor(src) {{ this.src = src; plays.push("new:" + src); }} play() {{ return Promise.resolve(); }} }}
let ws = null;
let wsSocket = null;
class WebSocket {{ constructor() {{ wsSocket = this; }} }}
let wsConnected = false;
let lastClassSegmentIndex = null;
let console_warns = 0;
console.warn = () => {{ console_warns++; }};
function stopFallbackRefresh() {{}}
function startFallbackRefresh() {{}}
function fetchState() {{}}
function fetchNodes() {{}}
function t(k) {{ return k; }}
{fns}
connectWebSocket();
(async () => {{
  let threw = false;
  try {{
    for (const m of {json.dumps(messages)}) {{
      wsSocket.onmessage({{ data: JSON.stringify(m) }});
      await new Promise((r) => setImmediate(r));
    }}
  }} catch (e) {{ threw = true; }}
  console.log(JSON.stringify({{ plays, overlays: overlays.map((o) => o.innerHTML), threw }}));
}})();
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _cue(seconds):
    return {
        "type": "race_event",
        "event": {"event_type": "countdown", "data": {"seconds_left": seconds}},
    }


def test_ten_and_five_play_the_tick_without_an_overlay():
    for secs in (10, 5):
        result = _run([_cue(secs)])
        assert result["plays"] == ["race-cue-tick-audio"], secs
        assert result["overlays"] == [], secs


def test_three_two_one_play_their_own_cue_and_show_the_big_number():
    for secs in (3, 2, 1):
        result = _run([_cue(secs)])
        assert result["plays"] == [f"race-cue-{secs}-audio"], secs
        assert len(result["overlays"]) == 1
        assert f">{secs}<" in result["overlays"][0], secs


def test_sound_off_shows_the_overlay_but_plays_nothing():
    result = _run([_cue(3)], sound_enabled=False)
    assert result["plays"] == []
    assert len(result["overlays"]) == 1


def test_a_rejected_play_never_throws():
    result = _run([_cue(3), _cue(10)], reject_play=True)
    assert result["threw"] is False
    assert result["plays"] == ["race-cue-3-audio", "race-cue-tick-audio"]


def test_other_event_types_and_thresholds_do_nothing():
    other = [
        {"type": "race_event", "event": {"event_type": "final_sprint", "data": {}}},
        {
            "type": "race_event",
            "event": {"event_type": "checkpoint_crossed", "data": {}},
        },
        _cue(7),
        {"type": "race_event"},
    ]
    result = _run(other)
    assert result["plays"] == [] and result["overlays"] == []


def test_finish_cue_fires_only_on_a_live_timed_race_stop():
    script = _script()
    fn = _function(script, "shouldPlayFinishCue")
    js = f"""
{fn}
const timed = {{ race_type: "time" }};
console.log(JSON.stringify([
  shouldPlayFinishCue("RUNNING", "STOPPED", "race", timed),
  shouldPlayFinishCue("RUNNING", "STOPPED", "race", {{ race_type: "watts" }}),
  shouldPlayFinishCue("IDLE", "STOPPED", "race", timed),      // page loaded into STOPPED
  shouldPlayFinishCue("STOPPED", "STOPPED", "race", timed),   // repeat broadcast
  shouldPlayFinishCue("RUNNING", "STOPPED", "race", {{ race_type: "distance" }}),
  shouldPlayFinishCue("RUNNING", "STOPPED", "class", timed),
  shouldPlayFinishCue("RUNNING", "STOPPED", "race", null),
  shouldPlayFinishCue("RUNNING", "RUNNING", "race", timed),
]));
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == [
        True,
        True,
        False,
        False,
        False,
        False,
        False,
        False,
    ]


def test_state_handler_asks_the_finish_rule_and_plays_it():
    handler = _function(_script(), "updateUIState")
    assert "shouldPlayFinishCue(previousState" in handler
    assert 'playRaceCue("finish")' in handler


def test_cue_audio_elements_are_preloaded_in_the_page():
    html = _raw()
    for name, wav in (
        ("tick", "countdown_tick"),
        ("3", "countdown_3"),
        ("2", "countdown_2"),
        ("1", "countdown_1"),
        ("finish", "finish"),
    ):
        assert re.search(
            rf'<audio id="race-cue-{name}-audio" preload="auto" src="/static/audio/{wav}\.wav">',
            html,
        ), name
