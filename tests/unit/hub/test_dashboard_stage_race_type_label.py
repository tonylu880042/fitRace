"""C4: the RUNNING stage banner interpolated the raw config.race_type
("time", "max_power" with the underscore swapped for a space) into
stage.running_main, so a zh-TW screen read "個人 time 賽事". The race type has
to go through the race_type.* locale keys like everywhere else.

Runs the real getRaceStageDetails()/raceTypeLabel() from index.html under
node, with the real locale JSON, and asserts on the rendered banner text.
"""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INDEX = ROOT / "hub_server" / "static" / "index.html"
LOCALES = ROOT / "hub_server" / "infrastructure" / "locales"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _script() -> str:
    source = INDEX.read_text(encoding="utf-8")
    start = source.index("<script>") + len("<script>")
    code = source[start : source.index("</script>", start)]
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", code))


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    depth, i, in_str = 0, source.index("{", start), None
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


def _banner_main(locale: str, race_type: str) -> str:
    messages = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
    script = _script()
    fns = "\n".join(
        _function(script, n)
        for n in ("raceTypeLabel", "competitionStageLabel", "getRaceStageDetails")
    )
    js = f"""
const MESSAGES = {json.dumps(messages)};
const messages = MESSAGES;
function t(key, params = {{}}) {{
  let value = MESSAGES[key] || key;
  Object.entries(params).forEach(([n, r]) => {{ value = value.replaceAll(`{{${{n}}}}`, String(r)); }});
  return value;
}}
function metricNumber(v) {{ return Number(v) || 0; }}
function buildMixedRaceTargetSummary() {{ return ""; }}
const document = {{ getElementById: () => null }};
let currentSessionMode = "race";
let currentState = "RUNNING";
let currentConfig = {{ race_type: {json.dumps(race_type)}, competition_mode: "individual",
  target_value: 500, duration_sec: 180 }};
{fns}
console.log(JSON.stringify(getRaceStageDetails("RUNNING").main));
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_time_race_shows_the_translated_race_type_in_chinese():
    assert _banner_main("zh-TW", "time") == "個人 時間 賽事"


def test_time_race_shows_the_translated_race_type_in_english():
    assert _banner_main("en-US", "time") == "Individual Time race"


def test_underscored_race_types_are_translated_too():
    assert "最大功率" in _banner_main("zh-TW", "max_power")
    assert "max_power" not in _banner_main("zh-TW", "max_power")


def test_unknown_race_type_falls_back_to_readable_raw_text():
    assert _banner_main("zh-TW", "sprint_x") == "個人 sprint x 賽事"
