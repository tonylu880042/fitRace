"""R6 dashboard: while the hub waits for more stations to sign up it
publishes challenge_start_at_epoch_ms; the (display-only) dashboard shows
"starting in N s, other stations can still sign up", counting down against
the hub's clock. Runs the real functions from index.html under node."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INDEX = ROOT / "hub_server" / "static" / "index.html"
LOCALES = ROOT / "hub_server" / "infrastructure" / "locales"
_LINE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


def _script():
    src = INDEX.read_text(encoding="utf-8")
    start = src.index("<script>") + len("<script>")
    return _LINE.sub("", _BLOCK.sub("", src[start : src.index("</script>", start)]))


def _function(source, name):
    start = source.index(f"function {name}(")
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


def _run(steps, locale="en-US"):
    messages = json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8"))
    script = _script()
    fns = "\n".join(
        _function(script, n)
        for n in ("applyChallengeWait", "renderChallengeWaitBanner")
    )
    js = f"""
const MESSAGES = {json.dumps(messages)};
function t(key, params = {{}}) {{
  let v = MESSAGES[key] || key;
  Object.entries(params).forEach(([n, r]) => {{ v = v.replaceAll(`{{${{n}}}}`, String(r)); }});
  return v;
}}
const banner = {{ style: {{ display: "none" }}, textContent: "" }};
const document = {{ getElementById: (id) => (id === "challenge-wait-banner" ? banner : null) }};
let localNow = 0;
Date.now = () => localNow;
let challengeStartAtEpochMs = null;
let hubClockOffsetMs = 0;
{fns}
{steps}
console.log(JSON.stringify({{ display: banner.style.display, text: banner.textContent }}));
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_banner_counts_down_to_the_start_time():
    result = _run(
        "localNow = 1000; applyChallengeWait({challenge_start_at_epoch_ms: 31000});"
        "localNow = 11_000; renderChallengeWaitBanner();"
    )
    assert result["display"] != "none"
    assert "20" in result["text"] and "other stations" in result["text"]


def test_banner_uses_the_hub_clock_not_the_browsers():
    # Browser clock is 100 s ahead of the hub's: remaining must still be 30 s.
    result = _run(
        "localNow = 100_000;"
        "applyChallengeWait({challenge_start_at_epoch_ms: 30_000, hub_now_epoch_ms: 0});"
    )
    assert "30" in result["text"]


def test_banner_is_hidden_when_nothing_is_pending():
    result = _run(
        "applyChallengeWait({challenge_start_at_epoch_ms: 5000}); applyChallengeWait({challenge_start_at_epoch_ms: null});"
    )
    assert result["display"] == "none"
    assert _run("applyChallengeWait({});")["display"] == "none"


def test_banner_never_shows_a_negative_number():
    result = _run(
        "localNow = 0; applyChallengeWait({challenge_start_at_epoch_ms: 1000}); localNow = 99_000; renderChallengeWaitBanner();"
    )
    assert "-" not in result["text"].split("—")[0]
    assert "0" in result["text"]


def test_chinese_banner_text():
    result = _run(
        "localNow = 0; applyChallengeWait({challenge_start_at_epoch_ms: 12_000});",
        locale="zh-TW",
    )
    assert result["text"] == "12 秒後開跑，其他站位仍可報名"


def test_update_ui_state_feeds_the_banner():
    handler = _function(_script(), "updateUIState")
    assert "applyChallengeWait(data)" in handler
