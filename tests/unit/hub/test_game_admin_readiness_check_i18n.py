"""Regression test: the six readiness-check labels rendered by
renderReadinessPanel() in Game Admin (hub_server/static/gameAdmin.html) used
to be hardcoded English:

    const checkOrder = [["state","Race State"], ...];

That means a zh-TW operator still saw English labels ("Race State", "Target",
"Registrations", "Teams", "Station Health", "Sound") on every readiness
check card, in violation of CLAUDE.md's i18n rule ("never hardcode zh/en
strings in static pages"). This wires those six labels through the page's
own t()/dictionaries i18n mechanism (the same one gameAdmin.html already
uses for every other on-screen string), adding six new keys
(check.state/target/registrations/teams/stations/sound) to both the en-US
and zh-TW inline dictionaries.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

# Comment-stripping so a comment mentioning e.g. "Race State" can't satisfy
# the "no longer hardcoded" assertion in place of a real fix (CLAUDE.md's
# known "comment satisfies the assertion" trap).
_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


def _script() -> str:
    source = _read()
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _check_order_region() -> str:
    """The exact slice of renderReadinessPanel() that builds checkOrder,
    from a comment-stripped script -- so a comment can't hide the old
    hardcoded literals or fake the new t() calls."""
    script = _script()
    start = script.index("function renderReadinessPanel")
    end = script.index("function stationReasonText", start)
    return script[start:end]


NEW_CHECK_KEYS = {
    "check.state": ("Race State", "比賽狀態"),
    "check.target": ("Target", "目標設定"),
    "check.registrations": ("Registrations", "選手報名"),
    "check.teams": ("Teams", "隊伍編組"),
    "check.stations": ("Station Health", "場站狀態"),
    "check.sound": ("Sound", "音效"),
}


def test_check_order_labels_are_no_longer_hardcoded_english():
    body = _check_order_region()
    assert '"Race State"' not in body
    assert '"Target"' not in body
    assert '"Registrations"' not in body
    assert '"Teams"' not in body
    assert '"Station Health"' not in body
    assert '"Sound"' not in body


def test_check_order_labels_route_through_t():
    body = _check_order_region()
    for key in NEW_CHECK_KEYS:
        assert f't("{key}")' in body, f"checkOrder does not call t(\"{key}\")"


def test_check_labels_present_in_both_dictionaries_with_expected_copy():
    source = _read()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 12000]

    for key, (en_text, zh_text) in NEW_CHECK_KEYS.items():
        assert (
            f'"{key}": "{en_text}"' in en_block
        ), f"{key} missing/incorrect in en-US dictionary"
        assert (
            f'"{key}": "{zh_text}"' in zh_block
        ), f"{key} missing/incorrect in zh-TW dictionary"


def test_i18n_keys_stay_symmetric_between_dictionaries():
    source = _read()
    en_start = source.index('"en-US": {')
    zh_start = source.index('dictionaries["zh-TW"] = {')
    en_block = source[en_start:zh_start]
    zh_block = source[zh_start : zh_start + 12000]

    en_keys = set(re.findall(r'"([a-zA-Z0-9_.]+)":\s*"', en_block))
    zh_keys = set(re.findall(r'"([a-zA-Z0-9_.]+)":\s*"', zh_block))

    missing_from_zh = en_keys - zh_keys
    assert not missing_from_zh, f"keys missing from zh-TW dict: {missing_from_zh}"
