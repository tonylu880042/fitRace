"""Game Admin's readiness panel shows localized text. Its inline dictionaries
carry no readiness strings: readiness.* keys come from /api/locales/{locale}
and are merged into the dictionaries at load and on language change.

Real page functions run under node; comments are stripped before any source
inspection."""

import json
import re
import subprocess
from pathlib import Path

PAGE = Path(__file__).resolve().parents[3] / "hub_server" / "static" / "gameAdmin.html"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip(code: str) -> str:
    return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", code))


def _script() -> str:
    source = PAGE.read_text(encoding="utf-8")
    start = source.index("<script>") + len("<script>")
    return source[start : source.index("</script>", start)]


def _matching(source: str, open_idx: int, open_ch: str, close_ch: str) -> int:
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
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced")


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    if source[max(0, start - 6) : start] == "async ":
        start -= 6
    paren = source.index("(", start)
    brace = source.index("{", _matching(source, paren, "(", ")"))
    return source[start : _matching(source, brace, "{", "}") + 1]


def _run(functions: list[str], body: str):
    code = _strip(_script())
    bodies = "\n".join(_function(code, name) for name in functions)
    script = f"{PRELUDE}\n{bodies}\n{body}"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"node failed: {result.stderr}\n{script}"
    return json.loads(result.stdout)


# The page's own t() semantics, over small dictionaries.
PRELUDE = """
let currentLocale = "zh-TW";
const dictionaries = {
  "en-US": { "text.ready": "Ready" },
  "zh-TW": { "text.ready": "就緒" },
};
function t(key, params = {}) {
  let value = (dictionaries[currentLocale] || dictionaries["en-US"])[key]
    || dictionaries["en-US"][key] || key;
  Object.entries(params).forEach(([name, replacement]) => {
    value = value.replaceAll(`{${name}}`, String(replacement));
  });
  return value;
}
"""

ZH_READINESS = {
    "readiness.no_registered_athletes": "開始前請至少登記一位選手。",
    "readiness.relay_wrong_size": "第 {station} 站需要 {legs} 位，目前 {count} 位",
    "readiness.sound_disabled": "開始音效已關閉",
    "readiness.target_ok": "比賽目標有效。",
    "readiness.stations_partial": "{online} 個在線；{offline} 個離線",
}

PAYLOAD = {
    "ready": False,
    "blocking_issues": [
        "Register at least one athlete before starting.",
        "Station 2: relay team needs exactly 3 member(s); has 1.",
    ],
    "blocking_items": [
        {"code": "no_registered_athletes", "params": {}},
        {"code": "relay_wrong_size", "params": {"station": 2, "legs": 3, "count": 1}},
    ],
    "warnings": ["Start sound is disabled for this race.", "EN future warning"],
    "warning_items": [
        {"code": "sound_disabled", "params": {}},
        {"code": "not_translated_yet", "params": {}},
    ],
    "checks": {
        "target": {
            "status": "ok",
            "message": "Race target is valid.",
            "code": "target_ok",
            "params": {},
        },
        "stations": {
            "status": "warn",
            "message": "1 station(s) online; 1 offline station(s) will be excluded.",
            "code": "stations_partial",
            "params": {"online": 1, "offline": 1},
        },
        "sound": {
            "status": "warn",
            "message": "Silent start selected.",
            "code": "code_missing_from_locale",
            "params": {},
        },
    },
    "station_health": [],
}


def _with_merged_zh(body: str) -> str:
    return f"""
dictionaries["zh-TW"] = {{ ...dictionaries["zh-TW"], ...{json.dumps(ZH_READINESS)} }};
{body}
"""


# ---- pure helpers ----


def test_issue_texts_are_localized_skip_station_scoped_ones_and_fall_back():
    texts = _run(
        ["translateReadiness", "readinessIssueTexts"],
        _with_merged_zh(
            f"console.log(JSON.stringify(readinessIssueTexts({json.dumps(PAYLOAD)})));"
        ),
    )
    # The relay_wrong_size item has a station param: the per-station cards
    # own those, exactly like the old /^Station \d+:/ filter did.
    assert texts == [
        "開始前請至少登記一位選手。",
        "開始音效已關閉",
        "EN future warning",
    ]


def test_issue_texts_for_an_older_hub_filter_station_lines_by_their_prefix():
    legacy = {
        "blocking_issues": ["Station 1: x", "Target value must be greater than 0."],
        "warnings": ["Group 1 (rower) has no assigned station."],
    }
    texts = _run(
        ["translateReadiness", "readinessIssueTexts"],
        f"console.log(JSON.stringify(readinessIssueTexts({json.dumps(legacy)})));",
    )
    assert texts == [
        "Target value must be greater than 0.",
        "Group 1 (rower) has no assigned station.",
    ]


def test_check_message_is_localized_with_params_else_english():
    out = _run(
        ["translateReadiness", "readinessCheckMessage"],
        _with_merged_zh(f"""
const checks = {json.dumps(PAYLOAD["checks"])};
console.log(JSON.stringify([
  readinessCheckMessage(checks.target),
  readinessCheckMessage(checks.stations),
  readinessCheckMessage(checks.sound),
  readinessCheckMessage({{status: "info", message: "plain english"}}),
  readinessCheckMessage(undefined),
]));
"""),
    )
    assert out == [
        "比賽目標有效。",
        "1 個在線；1 個離線",
        "Silent start selected.",
        "plain english",
        "--",
    ]


# ---- rendered panel ----


def _render_panel(locale: str, merged: bool) -> str:
    merge = (
        f'dictionaries["zh-TW"] = {{ ...dictionaries["zh-TW"], ...{json.dumps(ZH_READINESS)} }};'
        if merged
        else ""
    )
    return _run(
        [
            "readinessStatusClass",
            "translateReadiness",
            "readinessIssueTexts",
            "readinessCheckMessage",
            "stationReasonText",
            "renderReadinessPanel",
        ],
        f"""
currentLocale = {json.dumps(locale)};
{merge}
const out = {{}};
function $(id) {{ if (!out[id]) out[id] = {{ textContent: "", innerHTML: "" }}; return out[id]; }}
function escapeHtml(value) {{ return String(value ?? ""); }}
const state = {{ readiness: {json.dumps(PAYLOAD)} }};
renderReadinessPanel();
console.log(JSON.stringify(out["race-readiness-panel"].innerHTML));
""",
    )


def test_panel_renders_localized_issues_and_checks():
    html = _render_panel("zh-TW", merged=True)
    assert "開始前請至少登記一位選手。" in html
    assert "開始音效已關閉" in html
    assert "比賽目標有效。" in html
    assert "1 個在線；1 個離線" in html
    # Every sentence that has a locale message is no longer English.
    assert "Register at least one athlete" not in html
    assert "Race target is valid." not in html
    # No translation known -> the English sentence is the fallback.
    assert "EN future warning" in html
    assert "Silent start selected." in html


def test_panel_falls_back_to_english_before_the_locale_messages_arrive():
    html = _render_panel("zh-TW", merged=False)
    assert "Register at least one athlete before starting." in html
    assert "Race target is valid." in html


# ---- merging the server's readiness.* messages ----


def test_merge_copies_only_readiness_keys_into_that_locales_dictionary():
    merged = _run(
        ["mergeReadinessMessages"],
        """
mergeReadinessMessages("zh-TW", {
  "readiness.no_config": "請先儲存設定",
  "dashboard.title": "must not leak",
  "text.ready": "must not override",
});
console.log(JSON.stringify(dictionaries));
""",
    )
    assert merged["zh-TW"] == {
        "text.ready": "就緒",
        "readiness.no_config": "請先儲存設定",
    }
    assert merged["en-US"] == {"text.ready": "Ready"}


def test_load_fetches_the_locale_merges_and_rerenders():
    result = _run(
        ["mergeReadinessMessages", "loadReadinessMessages"],
        """
const calls = [];
let rendered = 0;
function renderReadinessPanel() { rendered += 1; }
globalThis.fetch = async (url) => {
  calls.push(url.split("?")[0]);
  return { ok: true, json: async () => ({ locale: "zh-TW", messages: { "readiness.no_config": "請先儲存設定" } }) };
};
loadReadinessMessages("zh-TW").then(() => console.log(JSON.stringify({
  calls, rendered, key: dictionaries["zh-TW"]["readiness.no_config"],
})));
""",
    )
    assert result == {
        "calls": ["/api/locales/zh-TW"],
        "rendered": 1,
        "key": "請先儲存設定",
    }


def test_load_failure_is_harmless_and_keeps_english_fallback():
    result = _run(
        ["mergeReadinessMessages", "loadReadinessMessages"],
        """
let rendered = 0;
function renderReadinessPanel() { rendered += 1; }
globalThis.fetch = async () => { throw new Error("offline"); };
loadReadinessMessages("zh-TW").then(() => console.log(JSON.stringify({
  rendered, merged: "readiness.no_config" in dictionaries["zh-TW"],
})));
""",
    )
    assert result == {"rendered": 0, "merged": False}


def test_page_start_up_loads_the_current_locales_readiness_messages():
    # Top-level start-up code cannot be executed in isolation; check the
    # comment-stripped tail of the script, in order, after applyTranslations().
    tail = _strip(_script()).rsplit("applyTranslations();", 1)[1]
    assert "loadReadinessMessages(currentLocale);" in tail
    assert tail.index("loadReadinessMessages(currentLocale);") < tail.index(
        "connectWebSocket();"
    )


def test_game_admin_has_no_inline_readiness_strings():
    dictionary_text = _script()
    assert '"readiness.' not in dictionary_text.split("function t(")[0]


def test_changing_language_refetches_that_locales_readiness_messages():
    result = _run(
        ["setLanguage"],
        """
const loaded = [];
const store = {};
const localStorage = { setItem: (k, v) => { store[k] = v; } };
function applyTranslations() {}
function loadReadinessMessages(locale) { loaded.push(locale); }
setLanguage("en-US");
console.log(JSON.stringify({ loaded, locale: currentLocale, store }));
""",
    )
    assert result == {
        "loaded": ["en-US"],
        "locale": "en-US",
        "store": {"fitrace.locale": "en-US"},
    }
