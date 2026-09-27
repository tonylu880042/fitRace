"""Post-race button labels in Game Admin (hub_server/static/gameAdmin.html)
were confusing operators mid-event: "Stop Race"/"Reset Race" read like race
control rather than "save this heat's results, then clear the stations for
the next one". This pins the clarified copy for both locales by loading the
REAL `dictionaries` object literal (and the real `t()` lookup function) out
of the page under node -- never a source-text grep -- so deleting the actual
wiring turns this red instead of being satisfied by a nearby comment.
"""

import json
import subprocess
import tempfile
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


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


def _extract_dictionaries_js(source: str) -> str:
    const_start = source.index("const dictionaries = {")
    const_open = source.index("{", const_start)
    const_close = _matching_brace_end(source, const_open)

    zh_marker = 'dictionaries["zh-TW"] = {'
    zh_start = source.index(zh_marker, const_close)
    zh_open = source.index("{", zh_start)
    zh_close = _matching_brace_end(source, zh_open)

    return source[const_start : zh_close + 1] + ";"


def _load_dictionaries(source: str) -> dict:
    js = _extract_dictionaries_js(source)
    js += "\nconsole.log(JSON.stringify(dictionaries));"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(js)
        tmp_file.flush()
        tmp_path = tmp_file.name
    try:
        result = subprocess.run(
            ["node", tmp_path], capture_output=True, text=True, timeout=5
        )
        assert (
            result.returncode == 0
        ), f"node failed to evaluate dictionaries: {result.stderr}"
        return json.loads(result.stdout)
    finally:
        Path(tmp_path).unlink()


_EXPECTED = {
    "button.stop_race": {
        "zh-TW": "結束本組（保存成績）",
        "en-US": "End Heat (Save Results)",
    },
    "button.reset_race": {
        "zh-TW": "清空賽道（成績保留）",
        "en-US": "Clear Stations (Results Kept)",
    },
    "button.load_next_heat": {
        "zh-TW": "下一組上場 →",
        "en-US": "Next Heat Up →",
    },
    "button.save_race": {
        "zh-TW": "儲存比賽設定",
        "en-US": "Save Race Settings",
    },
    "button.clear_results": {
        "zh-TW": "清除全部歷史成績",
        "en-US": "Clear All Past Results",
    },
    "button.cancel_current_heat": {
        "zh-TW": "退回這組（重新排隊）",
        "en-US": "Return Heat to Queue",
    },
    "confirm.reset_race": {
        "zh-TW": "確定清空賽道嗎？各站目前的選手登記與比賽設定會被清除，成績會保留；之後需要重新「儲存比賽設定」。",
        "en-US": "Clear stations? Current athlete registrations and race settings will be cleared; results are kept. You will need to Save Race Settings again.",
    },
}


def test_postrace_button_labels_are_clarified_for_both_locales():
    source = _read("gameAdmin.html")
    dictionaries = _load_dictionaries(source)

    for key, expected in _EXPECTED.items():
        assert dictionaries["en-US"][key] == expected["en-US"], key
        assert dictionaries["zh-TW"][key] == expected["zh-TW"], key


def test_postrace_button_labels_resolve_through_the_real_t_lookup():
    """Exercise the actual `t()` function (not just the raw dict) against
    the current locale switch, the way the page itself renders button text."""
    source = _read("gameAdmin.html")
    dict_js = _extract_dictionaries_js(source)

    t_start = source.index("function t(")
    paren_open = source.index("(", t_start)
    paren_depth = 0
    i = paren_open
    while i < len(source):
        if source[i] == "(":
            paren_depth += 1
        elif source[i] == ")":
            paren_depth -= 1
            if paren_depth == 0:
                break
        i += 1
    t_open = source.index("{", i)
    t_end = _matching_brace_end(source, t_open)
    t_fn = source[t_start : t_end + 1]

    harness = f"""
{dict_js}
let currentLocale = "zh-TW";
{t_fn}
console.log(JSON.stringify({{
  stop: t("button.stop_race"),
  reset: t("button.reset_race"),
}}));
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(harness)
        tmp_file.flush()
        tmp_path = tmp_file.name
    try:
        result = subprocess.run(
            ["node", tmp_path], capture_output=True, text=True, timeout=5
        )
        assert result.returncode == 0, f"node failed: {result.stderr}"
        output = json.loads(result.stdout)
    finally:
        Path(tmp_path).unlink()

    assert output["stop"] == "結束本組（保存成績）"
    assert output["reset"] == "清空賽道（成績保留）"
