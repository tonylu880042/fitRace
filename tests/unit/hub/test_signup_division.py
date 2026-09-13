"""signup.html gained an optional men/women division select next to the
team field. This executes the REAL, unmodified `submitForm` function
pulled out of hub_server/static/signup.html's inline <script> (the same
brace-matching extraction technique as test_dashboard_class_board.py and
test_anonymous_athlete_display_fallback.py) under `node`, with minimal DOM
stubs -- never a source-text grep, so deleting the real `division` wiring
would turn this red rather than being satisfied by a nearby comment.
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


def _read() -> str:
    return (STATIC_DIR / "signup.html").read_text(encoding="utf-8")


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


def _extract_async_function(source: str, name: str) -> str:
    marker = f"async function {name}("
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


def test_submit_sends_division_and_resets_select_after_success():
    source = _strip_js_comments(_read())
    submit_form = _extract_async_function(source, "submitForm")
    assert "division" in submit_form  # sanity: real source, not a stub

    harness = f"""
const state = {{
  divisionSelectValue: 'women',
  athleteNameValue: 'Tony',
  teamNameValue: 'RD',
}};

const athleteNameInput = {{ get value() {{ return state.athleteNameValue; }}, set value(v) {{ state.athleteNameValue = v; }} }};
const teamNameInput = {{ get value() {{ return state.teamNameValue; }}, set value(v) {{ state.teamNameValue = v; }} }};
const divisionSelect = {{ get value() {{ return state.divisionSelectValue; }}, set value(v) {{ state.divisionSelectValue = v; }} }};

const avatarProcessing = false;
const station = 1;
const hasQueryStation = true;
const successMsg = {{ style: {{}} }};
const errorMsg = {{ style: {{}} }};
const submitBtn = {{ style: {{}} }};
const btnText = {{ style: {{}} }};
const btnSpinner = {{ style: {{}} }};
const customPreview = {{ style: {{}} }};
let avatarBase64 = null;
function setActiveAvatarOption() {{}}
function setAvatarProcessing() {{}}
const defaultAvatarOption = null;
function t(key) {{ return key; }}

let capturedBody = null;
global.fetch = async (url, opts) => {{
  capturedBody = JSON.parse(opts.body);
  return {{ ok: true, json: async () => ({{}}) }};
}};

{submit_form}

(async () => {{
  await submitForm({{ preventDefault() {{}} }});
  console.log(JSON.stringify({{
    sentDivision: capturedBody.division,
    resetDivisionValue: divisionSelect.value,
    resetName: athleteNameInput.value,
  }}));
}})();
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["sentDivision"] == "women"
    assert result["resetDivisionValue"] == ""
    assert result["resetName"] == ""


def test_submit_sends_null_division_when_no_division_selected():
    source = _strip_js_comments(_read())
    submit_form = _extract_async_function(source, "submitForm")

    harness = f"""
const state = {{ divisionSelectValue: '', athleteNameValue: '', teamNameValue: '' }};

const athleteNameInput = {{ get value() {{ return state.athleteNameValue; }}, set value(v) {{ state.athleteNameValue = v; }} }};
const teamNameInput = {{ get value() {{ return state.teamNameValue; }}, set value(v) {{ state.teamNameValue = v; }} }};
const divisionSelect = {{ get value() {{ return state.divisionSelectValue; }}, set value(v) {{ state.divisionSelectValue = v; }} }};

const avatarProcessing = false;
const station = 1;
const hasQueryStation = true;
const successMsg = {{ style: {{}} }};
const errorMsg = {{ style: {{}} }};
const submitBtn = {{ style: {{}} }};
const btnText = {{ style: {{}} }};
const btnSpinner = {{ style: {{}} }};
const customPreview = {{ style: {{}} }};
let avatarBase64 = null;
function setActiveAvatarOption() {{}}
function setAvatarProcessing() {{}}
const defaultAvatarOption = null;
function t(key) {{ return key; }}

let capturedBody = null;
global.fetch = async (url, opts) => {{
  capturedBody = JSON.parse(opts.body);
  return {{ ok: true, json: async () => ({{}}) }};
}};

{submit_form}

(async () => {{
  await submitForm({{ preventDefault() {{}} }});
  console.log(JSON.stringify({{ sentDivision: capturedBody.division }}));
}})();
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert result["sentDivision"] is None
