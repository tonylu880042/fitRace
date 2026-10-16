"""Game Admin's readiness panel shows localized text. The readiness.* strings
are ordinary locale-file messages (no `game_admin.` prefix, shared with the
hub backend); the page loads them with the rest of its locale from
/api/locales/{locale} and re-renders when the language changes.

Real page functions run under node (including the page's own t()); comments
are stripped before any source inspection."""

import json

from page_locale import extract_function, run_page_functions, script_of
from page_locale import strip_js_comments

PAGE = "gameAdmin.html"

ZH_READINESS = {
    "readiness.no_registered_athletes": "開始前請至少登記一位選手。",
    "readiness.relay_wrong_size": "第 {station} 站需要 {legs} 位，目前 {count} 位",
    "readiness.sound_disabled": "開始音效已關閉",
    "readiness.target_ok": "比賽目標有效。",
    "readiness.stations_partial": "{online} 個在線；{offline} 個離線",
}


def _prelude(readiness_messages: dict) -> str:
    """The page's state after loadLocale(): zh-TW with a few page keys and,
    optionally, the readiness.* messages."""
    messages = {"game_admin.text.ready": "就緒", **readiness_messages}
    return f"""
let currentLocale = "zh-TW";
let messages = {json.dumps(messages, ensure_ascii=False)};
"""


def _run(functions: list[str], body: str, readiness_messages=ZH_READINESS):
    # The page's own t() always runs, so these tests cannot drift from it.
    return run_page_functions(
        PAGE, ["t", *functions], _prelude(readiness_messages), body
    )


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


# ---- pure helpers ----


def test_issue_texts_are_localized_skip_station_scoped_ones_and_fall_back():
    texts = _run(
        ["translateReadiness", "readinessIssueTexts"],
        f"console.log(JSON.stringify(readinessIssueTexts({json.dumps(PAYLOAD)})));",
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
        f"""
const checks = {json.dumps(PAYLOAD["checks"])};
console.log(JSON.stringify([
  readinessCheckMessage(checks.target),
  readinessCheckMessage(checks.stations),
  readinessCheckMessage(checks.sound),
  readinessCheckMessage({{status: "info", message: "plain english"}}),
  readinessCheckMessage(undefined),
]));
""",
    )
    assert out == [
        "比賽目標有效。",
        "1 個在線；1 個離線",
        "Silent start selected.",
        "plain english",
        "--",
    ]


# ---- rendered panel ----

PANEL_FUNCTIONS = [
    "readinessStatusClass",
    "translateReadiness",
    "readinessIssueTexts",
    "readinessCheckMessage",
    "stationReasonText",
    "renderReadinessPanel",
]

RENDER_PANEL = f"""
const out = {{}};
function $(id) {{ if (!out[id]) out[id] = {{ textContent: "", innerHTML: "" }}; return out[id]; }}
function escapeHtml(value) {{ return String(value ?? ""); }}
const state = {{ readiness: {json.dumps(PAYLOAD)} }};
renderReadinessPanel();
console.log(JSON.stringify(out["race-readiness-panel"].innerHTML));
"""


def test_panel_renders_localized_issues_and_checks():
    html = _run(PANEL_FUNCTIONS, RENDER_PANEL)
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
    html = _run(PANEL_FUNCTIONS, RENDER_PANEL, readiness_messages={})
    assert "Register at least one athlete before starting." in html
    assert "Race target is valid." in html


def test_panel_renders_simplified_chinese_from_the_real_zh_cn_locale():
    # Same panel, the real zh-CN messages instead of the small fixture.
    from page_locale import messages_js

    html = run_page_functions(
        PAGE,
        ["t", *PANEL_FUNCTIONS],
        'let currentLocale = "zh-CN";\n' + messages_js("zh-CN"),
        RENDER_PANEL,
    )
    assert "开始前请至少登记一位选手。" in html
    assert "开始音效已关闭" in html or "这场比赛已关闭开始音效。" in html
    assert "比赛目标有效。" in html
    assert "Register at least one athlete" not in html
    assert "Race target is valid." not in html


# ---- loading and changing the language ----


def test_changing_language_loads_that_locale_and_rerenders_the_panel():
    # setLanguage -> loadLocale -> applyTranslations -> renderReadinessPanel,
    # all real page functions; only the network and the other renderers are
    # stubbed.
    result = run_page_functions(
        PAGE,
        [
            "t",
            "loadLocale",
            "setLanguage",
            "applyTranslations",
            *PANEL_FUNCTIONS,
        ],
        f"""
let currentLocale = "en-US";
let messages = {{}};
const store = {{}};
const localStorage = {{ setItem: (k, v) => {{ store[k] = v; }} }};
const out = {{}};
function $(id) {{ if (!out[id]) out[id] = {{ textContent: "", innerHTML: "", value: "", classList: {{ contains: () => false }} }}; return out[id]; }}
function escapeHtml(value) {{ return String(value ?? ""); }}
const document = {{ documentElement: {{ lang: "" }}, querySelectorAll: () => [] }};
function renderRace() {{}}
function renderStations() {{}}
function syncMixedRaceFields() {{}}
function syncRaceFields() {{}}
const state = {{ readiness: {json.dumps(PAYLOAD)} }};
const requested = [];
globalThis.fetch = async (url) => {{
  requested.push(url.split("?")[0]);
  return {{ json: async () => ({{ locale: "zh-TW", messages: {json.dumps(ZH_READINESS, ensure_ascii=False)} }}) }};
}};
""",
        """
setLanguage("zh-TW").then(() => console.log(JSON.stringify({
  requested, stored: store["fitrace.locale"],
  html: out["race-readiness-panel"].innerHTML,
})));
""",
    )
    assert result["requested"] == ["/api/locales/zh-TW"]
    assert result["stored"] == "zh-TW"
    assert "開始前請至少登記一位選手。" in result["html"]
    assert "Register at least one athlete" not in result["html"]


def test_page_has_no_inline_readiness_strings():
    # Readiness text comes from the locale files; the script must not carry
    # any readiness.* message value of its own (only keys it asks for).
    script = strip_js_comments(script_of(PAGE))
    assert "readiness.target_ok" not in script
    assert extract_function(script, "t")  # sanity: the real t() is there
