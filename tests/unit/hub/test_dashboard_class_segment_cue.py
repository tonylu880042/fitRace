"""Tests for the class-mode segment-change audio cue on the dashboard
(`hub_server/static/index.html`).

A training class advances through segments silently unless something plays
a sound when the board crosses a segment boundary. This module covers two
layers, per CLAUDE.md's testability guidance (a correct helper whose result
never reaches the DOM/Audio is the recurring defect in this codebase):

  1. `shouldPlayClassSegmentCue` -- a PURE decision function (no DOM/Audio
     access) executed under `node -e`, mirroring the extraction technique in
     tests/unit/hub/test_dashboard_class_board.py.
  2. `updateClassSegmentCue` -- the wiring function every class board render
     path calls. Executed under `node -e` with a stub `playClassSegmentChangeCue`
     spy, proving the cue is invoked (or withheld) at the right moments, not
     merely that the decision function exists somewhere in the source.
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
    async_marker = f"async function {name}("
    marker = f"function {name}("
    if async_marker in source:
        start = source.index(async_marker)
    else:
        start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# 1. shouldPlayClassSegmentCue -- pure decision function.
# ---------------------------------------------------------------------------


def _run_should_play(session_mode, state, previous_index, new_index) -> bool:
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "shouldPlayClassSegmentCue"))
    args = ", ".join(
        json.dumps(value) for value in (session_mode, state, previous_index, new_index)
    )
    script = f"{fn}\nconsole.log(JSON.stringify(shouldPlayClassSegmentCue({args})));"
    return json.loads(_run_node(script))


def test_should_play_false_when_previous_index_is_null_first_render():
    assert _run_should_play("class", "RUNNING", None, 0) is False


def test_should_play_true_on_genuine_index_change():
    assert _run_should_play("class", "RUNNING", 0, 1) is True


def test_should_play_false_when_index_unchanged():
    assert _run_should_play("class", "RUNNING", 1, 1) is False


def test_should_play_false_when_not_class_session_mode():
    assert _run_should_play("race", "RUNNING", 0, 1) is False


def test_should_play_false_when_not_running():
    assert _run_should_play("class", "STOPPED", 0, 1) is False
    assert _run_should_play("class", "READY", 0, 1) is False
    assert _run_should_play("class", "IDLE", 0, 1) is False


def test_should_play_false_when_new_index_is_null():
    assert _run_should_play("class", "RUNNING", 0, None) is False


def test_should_play_true_on_backward_index_change():
    # A genuine index change fires regardless of direction -- the decision
    # is "did the index change", not "did it advance".
    assert _run_should_play("class", "RUNNING", 2, 1) is True


# ---------------------------------------------------------------------------
# 2. updateClassSegmentCue -- wiring. Proves the cue is actually invoked (or
# withheld) at render time, not merely that the pure decision function above
# is correct in isolation.
# ---------------------------------------------------------------------------


def _run_update_class_segment_cue(
    session_mode, state, initial_last_index, clocks_js: str
) -> dict:
    """Defines the module-level globals updateClassSegmentCue reads/writes
    (currentSessionMode, currentState, lastClassSegmentIndex) and a spy
    standing in for playClassSegmentChangeCue, then calls
    updateClassSegmentCue once per clock in `clocks_js` (a JS array of
    clock objects), in order -- mirroring successive renders of the same
    page session."""
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "updateClassSegmentCue"))
    should_play_fn = _strip_js_comments(
        _extract_function(source, "shouldPlayClassSegmentCue")
    )
    script = (
        f"let currentSessionMode = {json.dumps(session_mode)};\n"
        f"let currentState = {json.dumps(state)};\n"
        f"let lastClassSegmentIndex = {json.dumps(initial_last_index)};\n"
        "let playCallCount = 0;\n"
        "function playClassSegmentChangeCue() { playCallCount += 1; }\n"
        + should_play_fn
        + "\n"
        + fn
        + "\n"
        + f"const clocks = {clocks_js};\n"
        + "clocks.forEach((clock) => updateClassSegmentCue(clock));\n"
        + "console.log(JSON.stringify({ playCallCount, lastClassSegmentIndex }));"
    )
    return json.loads(_run_node(script))


def test_update_class_segment_cue_does_not_fire_on_first_render():
    result = _run_update_class_segment_cue("class", "RUNNING", None, '[{"index": 0}]')
    assert result["playCallCount"] == 0
    assert result["lastClassSegmentIndex"] == 0


def test_update_class_segment_cue_fires_on_genuine_index_change():
    result = _run_update_class_segment_cue(
        "class", "RUNNING", None, '[{"index": 0}, {"index": 1}]'
    )
    assert result["playCallCount"] == 1
    assert result["lastClassSegmentIndex"] == 1


def test_update_class_segment_cue_does_not_fire_on_repeated_render_of_same_index():
    # Every projector refresh mid-segment (the 250ms tick) re-renders the
    # same index repeatedly -- none of those renders may beep.
    result = _run_update_class_segment_cue(
        "class", "RUNNING", None, '[{"index": 0}, {"index": 0}, {"index": 0}]'
    )
    assert result["playCallCount"] == 0


def test_update_class_segment_cue_fires_once_per_transition_across_many_ticks():
    result = _run_update_class_segment_cue(
        "class",
        "RUNNING",
        None,
        '[{"index": 0}, {"index": 0}, {"index": 1}, {"index": 1}, {"index": 2}]',
    )
    assert result["playCallCount"] == 2


def test_update_class_segment_cue_resets_tracking_when_not_running():
    # A class stops (or the page is not on a running class); the next time
    # it starts again must be treated as a fresh first render, not compared
    # against whatever segment was last shown.
    result = _run_update_class_segment_cue("class", "STOPPED", None, '[{"index": 2}]')
    assert result["playCallCount"] == 0
    assert result["lastClassSegmentIndex"] is None


def test_update_class_segment_cue_never_fires_in_race_mode():
    result = _run_update_class_segment_cue(
        "race", "RUNNING", None, '[{"index": 0}, {"index": 1}]'
    )
    assert result["playCallCount"] == 0


# ---------------------------------------------------------------------------
# 3. Reconnect suppression: ws.onclose must reset lastClassSegmentIndex so
# the next fetchState() after reopening is treated as a first render.
# ---------------------------------------------------------------------------


def test_ws_onclose_resets_last_class_segment_index():
    source = _read_index()
    stripped = _strip_js_comments(source)
    onclose_start = stripped.index("ws.onclose = () => {")
    brace_open = stripped.index("{", onclose_start)
    brace_end = _matching_brace_end(stripped, brace_open)
    onclose_body = stripped[onclose_start : brace_end + 1]
    assert "lastClassSegmentIndex = null" in onclose_body, (
        "ws.onclose must reset lastClassSegmentIndex to null so a reconnect "
        f"is never mistaken for a live segment change; onclose body was: "
        f"{onclose_body}"
    )


# ---------------------------------------------------------------------------
# 4. Sound preference and audio failure tolerance.
# ---------------------------------------------------------------------------


def test_play_class_segment_change_cue_respects_sound_disabled():
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "playClassSegmentChangeCue"))
    script = (
        "let currentSoundEnabled = false;\n"
        "let audioPlayCalled = false;\n"
        "function Audio(src) { this.play = () => { audioPlayCalled = true; return Promise.resolve(); }; }\n"
        "const stubAudio = new Audio();\n"
        'const document = { getElementById: (id) => (id === "class-segment-cue-audio" ? stubAudio : null) };\n'
        + fn
        + "\n"
        + "playClassSegmentChangeCue().then(() => {\n"
        + "  console.log(JSON.stringify({ audioPlayCalled }));\n"
        + "});\n"
    )
    result = json.loads(_run_node(script))
    assert result["audioPlayCalled"] is False


def test_play_class_segment_change_cue_plays_when_sound_enabled():
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "playClassSegmentChangeCue"))
    script = (
        "let currentSoundEnabled = true;\n"
        "let audioPlayCalled = false;\n"
        "let currentTimeSetTo = null;\n"
        "const stubAudio = {\n"
        "  set currentTime(value) { currentTimeSetTo = value; },\n"
        "  play: () => { audioPlayCalled = true; return Promise.resolve(); },\n"
        "};\n"
        'const document = { getElementById: (id) => (id === "class-segment-cue-audio" ? stubAudio : null) };\n'
        + fn
        + "\n"
        + "playClassSegmentChangeCue().then(() => {\n"
        + "  console.log(JSON.stringify({ audioPlayCalled, currentTimeSetTo }));\n"
        + "});\n"
    )
    result = json.loads(_run_node(script))
    assert result["audioPlayCalled"] is True
    assert result["currentTimeSetTo"] == 0


def test_play_class_segment_change_cue_tolerates_blocked_autoplay():
    """A rejected play() promise must not throw out of the function -- a
    blocked autoplay must never break the caller (and therefore never break
    board rendering, since updateClassSegmentCue calls this fire-and-forget)."""
    source = _read_index()
    fn = _strip_js_comments(_extract_function(source, "playClassSegmentChangeCue"))
    script = (
        "let currentSoundEnabled = true;\n"
        "const stubAudio = {\n"
        "  set currentTime(value) {},\n"
        "  play: () => Promise.reject(new Error('NotAllowedError')),\n"
        "};\n"
        'const document = { getElementById: (id) => (id === "class-segment-cue-audio" ? stubAudio : null) };\n'
        + fn
        + "\n"
        + "playClassSegmentChangeCue().then(() => {\n"
        + "  console.log(JSON.stringify({ resolved: true }));\n"
        + "}).catch((err) => {\n"
        + "  console.log(JSON.stringify({ resolved: false, error: String(err) }));\n"
        + "});\n"
    )
    result = json.loads(_run_node(script))
    assert result["resolved"] is True


def test_update_ui_state_tracks_sound_enabled_from_race_state():
    """currentSoundEnabled must be assigned from
    data.start_countdown_sound_enabled, not left stuck at its default --
    the "computed but never assigned" failure mode this project keeps
    losing review rounds to."""
    source = _read_index()
    stripped = _strip_js_comments(source)
    assert re.search(
        r"currentSoundEnabled\s*=\s*data\.start_countdown_sound_enabled\s*!==\s*false",
        stripped,
    ), "updateUIState must assign currentSoundEnabled from the race state payload"
