"""Regression test: relay races were labelled "個人" (individual) on the
dashboard (hub_server/static/index.html).

Root cause: both the race-stage banner (getRaceStageDetails, ~line 2691)
and the header/type-indicator text (renderDashboardChrome, ~line 5540)
computed their competition label with the same two-way ternary --
`currentConfig?.competition_mode === "team" ? t("stage.team") :
t("stage.individual")` -- which has no branch for competition_mode ===
"relay", so a relay race fell into the "individual" branch at both sites.

Fix: a single shared helper, competitionStageLabel(config), resolves
team/individual/relay (new locale key "stage.relay", added to all six
locale files) and both call sites now delegate to it instead of
repeating the ternary.

This module extracts the real competitionStageLabel() (and the two call
sites) from index.html's inline <script> (comment-stripped, same
brace-depth technique used throughout tests/unit/hub/) and executes it
under node with a real interpolating t() backed by the actual locale
JSON files -- so a test failure means the real translated string came out
wrong, not merely that some "relay" text exists somewhere in the source.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"
LOCALES_DIR = (
    Path(__file__).resolve().parents[3] / "hub_server" / "infrastructure" / "locales"
)

LOCALE_FILES = ["de-CH", "en-US", "fr", "it", "sv", "zh-TW"]

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _stripped_script() -> str:
    source = _read_index()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


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
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _load_locale_messages(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as file:
        return json.load(file)


_T_STUB = """
function t(key, params = {}) {
  let value = MESSAGES[key] || key;
  Object.entries(params).forEach(([name, replacement]) => {
    value = value.replaceAll(`{${name}}`, String(replacement));
  });
  return value;
}
"""


def _run_node(js_source: str) -> str:
    result = subprocess.run(
        ["node", "-e", js_source], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{js_source}")
    return result.stdout


def _competition_stage_label_fn() -> str:
    return _strip_js_comments(
        _extract_function(_stripped_script(), "competitionStageLabel")
    )


def _call_label(locale: str, competition_mode) -> str:
    messages = _load_locale_messages(locale)
    fn = _competition_stage_label_fn()
    config = json.dumps({"competition_mode": competition_mode})
    script = f"""
const MESSAGES = {json.dumps(messages)};
{_T_STUB}
{fn}
const result = competitionStageLabel({config});
console.log(JSON.stringify({{ result }}));
"""
    output = _run_node(script)
    return json.loads(output.strip().splitlines()[-1])["result"]


def test_competition_stage_label_helper_is_defined():
    assert "function competitionStageLabel" in _stripped_script()


def test_relay_config_returns_the_relay_label():
    assert _call_label("en-US", "relay") == "Relay"
    assert _call_label("zh-TW", "relay") == "接力"


def test_team_config_returns_the_team_label():
    assert _call_label("en-US", "team") == "Team"
    assert _call_label("zh-TW", "team") == "團體"


def test_individual_config_returns_the_individual_label():
    assert _call_label("en-US", "individual") == "Individual"
    assert _call_label("en-US", None) == "Individual"
    assert _call_label("zh-TW", "individual") == "個人"


def test_stage_relay_key_present_in_every_locale_json_file():
    for locale in LOCALE_FILES:
        messages = _load_locale_messages(locale)
        assert "stage.relay" in messages, f"{locale}.json is missing stage.relay"


def test_call_sites_delegate_to_the_shared_helper():
    """Both offending sites (getRaceStageDetails' race-stage banner label,
    and renderDashboardChrome's header/type-indicator label) must call
    competitionStageLabel(currentConfig) -- not repeat the team/individual
    ternary inline, which is exactly how a relay race got silently
    mislabelled "individual" at both places."""
    script = _stripped_script()
    get_race_stage_details = _extract_function(script, "getRaceStageDetails")
    render_dashboard_chrome = _extract_function(script, "renderDashboardChrome")
    assert "competitionStageLabel(currentConfig)" in get_race_stage_details
    assert "competitionStageLabel(currentConfig)" in render_dashboard_chrome


def test_team_individual_ternary_exists_only_inside_the_helper():
    """Guards against a "fix" that adds competitionStageLabel() at one
    call site but leaves the old ternary duplicated (and un-fixed) at the
    other -- the raw ternary pattern must appear exactly once in the whole
    script: inside competitionStageLabel itself."""
    script = _stripped_script()
    occurrences = script.count('t("stage.team") : t("stage.individual")')
    assert occurrences == 1, (
        "expected the team/individual ternary to live only inside "
        f"competitionStageLabel, found it {occurrences} times"
    )
