"""Game Admin (hub_server/static/gameAdmin.html) reads every string from the
hub locale files through /api/locales, like classAdmin and teams do
(docs/zh_cn_i18n_spec.md section 4). Page keys live under `game_admin.`; the
page's t() adds that prefix and falls back to the shared global keys
(readiness.*). Real page functions run under node; comments are stripped
before any source inspection.
"""

import json
import re

import pytest

from page_locale import (
    CJK_RE,
    COMPLETE_LOCALES,
    extract_function,
    messages_js,
    raw_messages,
    read_page,
    run_node,
    run_page_functions,
    script_of,
    strip_html_comments,
    strip_js_comments,
)

PAGE = "gameAdmin.html"
PREFIX = "game_admin."

# Keys that are looked up at run time from a computed name; they cannot be
# found by scanning for t("literal"), so their families are listed here and
# checked against the locale files instead.
DYNAMIC_KEY_FAMILIES = [
    "readiness.",
    "equipment_type.",
]


def _page_has_key(key: str, messages: dict) -> bool:
    return f"{PREFIX}{key}" in messages or key in messages


def _referenced_keys() -> set[str]:
    """Every literal key the page can ask for: data-i18n attributes plus
    t("...") / t('...') calls in the comment-stripped script."""
    html = strip_html_comments(read_page(PAGE))
    script = strip_js_comments(script_of(PAGE))
    keys = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', html))
    keys |= set(re.findall(r"""\bt\(\s*["']([A-Za-z0-9_.]+)["']""", script))
    return keys


# ---------------------------------------------------------------------------
# Structure: select picker, no inline dictionaries, no merge hack
# ---------------------------------------------------------------------------


def test_language_picker_is_a_select_filled_from_the_locale_api():
    html = strip_html_comments(read_page(PAGE))
    assert re.search(r'<select[^>]*id="language-select"', html)
    assert 'id="lang-en"' not in html
    assert 'id="lang-zh"' not in html
    assert "lang-toggle" not in html

    script = strip_js_comments(script_of(PAGE))
    assert 'fetch("/api/locales")' in script
    assert "/api/locales/${encodeURIComponent(locale)}" in script


def test_script_has_no_inline_dictionaries_or_readiness_merge_hack():
    script = strip_js_comments(script_of(PAGE))
    assert "dictionaries" not in script
    assert "mergeReadinessMessages" not in script
    assert "loadReadinessMessages" not in script


def test_no_cjk_literal_remains_in_markup_script_or_style():
    html = strip_html_comments(read_page(PAGE))
    html = re.sub(r"/\*.*?\*/", "", html, flags=re.DOTALL)
    script = strip_js_comments(script_of(PAGE))
    html_without_script = html.replace(script_of(PAGE), "")
    offenders = CJK_RE.findall(script) + CJK_RE.findall(html_without_script)
    assert not offenders, f"CJK literals left in {PAGE}: {offenders[:20]}"


def test_comment_stripped_script_is_still_valid_javascript(tmp_path):
    import subprocess

    path = tmp_path / "stripped.js"
    path.write_text(strip_js_comments(script_of(PAGE)), encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# Locale files carry every string the page uses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("locale", COMPLETE_LOCALES)
def test_every_referenced_key_exists_in_each_complete_locale(locale):
    messages = raw_messages(locale)
    missing = sorted(
        key for key in _referenced_keys() if not _page_has_key(key, messages)
    )
    assert not missing, f"{PAGE} asks for keys {locale} lacks: {missing}"


def test_dynamic_key_families_exist_in_the_locale_files():
    for locale in COMPLETE_LOCALES:
        messages = raw_messages(locale)
        for family in DYNAMIC_KEY_FAMILIES:
            assert any(key.startswith(family) for key in messages), (locale, family)


def test_page_strings_are_namespaced_and_complete_in_all_three_locales():
    keysets = {
        locale: {k for k in raw_messages(locale) if k.startswith(PREFIX)}
        for locale in COMPLETE_LOCALES
    }
    assert len(keysets["en-US"]) > 250
    assert keysets["zh-TW"] == keysets["en-US"] == keysets["zh-CN"]


def test_page_strings_differ_between_english_and_each_chinese_locale():
    # game_admin.title is the dashboard's pre-existing "Game Admin" product
    # name, identical in every language on purpose.
    proper_nouns = {"game_admin.title"}
    en = raw_messages("en-US")
    for locale in ("zh-TW", "zh-CN"):
        zh = raw_messages(locale)
        untranslated = sorted(
            k
            for k in en
            if k.startswith(PREFIX) and k not in proper_nouns and en[k] == zh[k]
        )
        assert not untranslated, (locale, untranslated)


# ---------------------------------------------------------------------------
# t(): namespace first, shared global key second, the key itself last
# ---------------------------------------------------------------------------


def _t(locale: str, calls: list[str]):
    return run_page_functions(
        PAGE,
        ["t"],
        messages_js(locale),
        f"console.log(JSON.stringify({json.dumps(calls)}.map((k) => t(k))));",
    )


def test_t_renders_simplified_chinese_from_the_zh_cn_messages():
    assert _t(
        "zh-CN",
        ["button.save_race", "button.start_race", "nav.dashboard", "panel.race_rules"],
    ) == ["保存比赛设置", "开始比赛", "仪表盘", "比赛规则"]


def test_t_keeps_traditional_chinese_for_zh_tw_and_english_for_en_us():
    assert _t("zh-TW", ["button.save_race"]) == ["儲存比賽設定"]
    assert _t("en-US", ["button.save_race"]) == ["Save Race Settings"]


def test_t_falls_back_to_a_shared_global_key_then_to_the_key():
    assert _t("zh-CN", ["readiness.no_config", "no.such.key"]) == [
        "开始前请先保存比赛设置。",
        "no.such.key",
    ]


def test_t_substitutes_params():
    out = run_page_functions(
        PAGE,
        ["t"],
        messages_js("zh-CN"),
        'console.log(JSON.stringify(t("message.cancel_heat_failed", {error: "boom"})));',
    )
    assert out == "取消失败：boom"


def test_a_locale_without_the_page_keys_still_renders_english():
    # it/fr/de-CH/sv have no game_admin.* keys; the hub serves en-US under
    # them (hub_server/infrastructure/locales.py load_locale).
    assert _t("it", ["button.save_race"]) == ["Save Race Settings"]


# ---------------------------------------------------------------------------
# Render path in Simplified Chinese
# ---------------------------------------------------------------------------


def test_apply_translations_fills_data_i18n_elements_in_simplified_chinese():
    html = read_page(PAGE)
    keys = sorted(set(re.findall(r'data-i18n="([^"]+)"', html)))
    out = run_page_functions(
        PAGE,
        ["t", "applyTranslations"],
        messages_js("zh-CN") + f"""
let currentLocale = "zh-CN";
const elements = {json.dumps(keys)}.map((key) => ({{ dataset: {{ i18n: key }}, textContent: "" }}));
const document = {{
  documentElement: {{ lang: "" }},
  querySelectorAll: () => elements,
}};
const node = {{ textContent: "", classList: {{ contains: () => false, toggle() {{}} }} }};
function $() {{ return node; }}
function renderRace() {{}}
function renderReadinessPanel() {{}}
function renderStations() {{}}
function syncMixedRaceFields() {{}}
function syncRaceFields() {{}}
""",
        """
applyTranslations();
console.log(JSON.stringify({
  lang: document.documentElement.lang,
  byKey: Object.fromEntries(elements.map((e) => [e.dataset.i18n, e.textContent])),
}));
""",
    )
    assert out["lang"] == "zh-CN"
    assert out["byKey"]["header.subtitle"] == "比赛设置与教练操作控制"
    assert out["byKey"]["nav.system_admin"] == "系统管理"
    assert out["byKey"]["nav.operator_unlock"] == "操作解锁"
    # Nothing is left showing a raw key.
    assert not [k for k, v in out["byKey"].items() if v == k]


def test_race_action_buttons_render_in_simplified_chinese():
    out = run_page_functions(
        PAGE,
        ["t", "renderRaceActionButtons"],
        messages_js("zh-CN") + """
let currentLocale = "zh-CN";
const nodes = {};
function $(id) {
  if (!nodes[id]) nodes[id] = { textContent: "", disabled: false, classList: { toggle() {} } };
  return nodes[id];
}
const state = {
  race: { state: "IDLE" }, readiness: { ready: true },
  raceConfigDirty: true, countdownActive: false, raceActionPending: null,
};
""",
        """
renderRaceActionButtons();
const first = [nodes["btn-save-race"].textContent, nodes["btn-start-race"].textContent];
state.raceActionPending = "start";
renderRaceActionButtons();
state.raceActionPending = null;
state.countdownActive = true;
renderRaceActionButtons();
console.log(JSON.stringify({ first, countdown: nodes["btn-start-race"].textContent }));
""",
    )
    assert out == {"first": ["保存比赛设置", "开始比赛"], "countdown": "倒计时进行中"}


# ---------------------------------------------------------------------------
# Loading and switching languages
# ---------------------------------------------------------------------------

LOADER_PRELUDE = """
const store = {};
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = v; },
};
const navigator = { language: "en-US" };
const requested = [];
const doc = { documentElement: { lang: "" } };
const document = doc;
const select = { value: "", innerHTML: "" };
function $(id) { return id === "language-select" ? select : null; }
function escapeHtml(v) { return String(v); }
let applied = 0;
function applyTranslations() { applied += 1; }
let currentLocale = "en-US";
let messages = {};
globalThis.fetch = async (url) => {
  requested.push(url.split("?")[0]);
  if (url === "/api/locales") {
    return { ok: true, json: async () => ({ locales: [
      { code: "en-US", label: "English" }, { code: "zh-CN", label: "简体中文" } ] }) };
  }
  const code = decodeURIComponent(url.split("?")[0].split("/").pop());
  return { ok: true, json: async () => ({ locale: code, messages: { "game_admin.button.save_race": "SAVED-" + code } }) };
};
"""


def test_set_language_loads_the_locale_stores_the_choice_and_re_renders():
    out = run_page_functions(
        PAGE,
        ["t", "loadLocale", "setLanguage"],
        LOADER_PRELUDE,
        """
setLanguage("zh-CN").then(() => console.log(JSON.stringify({
  requested, locale: currentLocale, stored: store["fitrace.locale"],
  lang: doc.documentElement.lang, selected: select.value, applied,
  text: t("button.save_race"),
})));
""",
    )
    assert out == {
        "requested": ["/api/locales/zh-CN"],
        "locale": "zh-CN",
        "stored": "zh-CN",
        "lang": "zh-CN",
        "selected": "zh-CN",
        "applied": 1,
        "text": "SAVED-zh-CN",
    }


def test_locale_list_populates_the_language_select_from_the_api():
    out = run_page_functions(
        PAGE,
        ["loadLocaleList"],
        LOADER_PRELUDE.replace(
            'let currentLocale = "en-US";', 'let currentLocale = "zh-CN";'
        ),
        """
loadLocaleList().then(() => console.log(JSON.stringify({
  html: select.innerHTML, value: select.value, requested })));
""",
    )
    assert out["requested"] == ["/api/locales"]
    assert out["value"] == "zh-CN"
    assert '<option value="zh-CN">简体中文</option>' in out["html"]
    assert '<option value="en-US">English</option>' in out["html"]


@pytest.mark.parametrize(
    "language,saved,expected",
    [
        ("zh-CN", None, "zh-CN"),
        ("zh-Hans", None, "zh-CN"),
        ("zh-TW", None, "zh-TW"),
        ("zh", None, "zh-TW"),
        ("fr-CH", None, "fr"),
        ("en-GB", None, "en-US"),
        ("zh-CN", "sv", "sv"),
    ],
)
def test_initial_locale_is_saved_choice_else_browser_language(
    language, saved, expected
):
    out = run_page_functions(
        PAGE,
        ["getBrowserLocale"],
        f"""
const localStorage = {{ getItem: () => {json.dumps(saved)} }};
const navigator = {{ language: {json.dumps(language)} }};
""",
        "console.log(JSON.stringify(getBrowserLocale()));",
    )
    assert out == expected


def test_start_up_waits_for_the_locale_before_rendering_or_connecting():
    script = strip_js_comments(script_of(PAGE))
    boot = extract_function(script, "boot")
    assert boot.index("loadLocale") < boot.index("applyTranslations")
    assert boot.index("applyTranslations") < boot.index("connectWebSocket")
    assert boot.index("applyTranslations") < boot.index("refreshAll")
    assert re.search(r"^\s*boot\(\);", script, re.MULTILINE)


def test_boot_still_starts_the_page_when_the_locale_fetch_fails():
    out = run_page_functions(
        PAGE,
        ["boot"],
        """
const calls = [];
async function loadLocaleList() { calls.push("list"); }
async function loadLocale() { calls.push("locale"); throw new Error("offline"); }
function syncMixedRaceFields() { calls.push("mixed"); }
function syncRaceFields() { calls.push("race"); }
function syncCompetitionFields() { calls.push("competition"); }
function syncVisibility() { calls.push("visibility"); }
function applyTranslations() { calls.push("apply"); }
function connectWebSocket() { calls.push("ws"); }
function refreshAll() { calls.push("refresh"); }
""",
        "boot().then(() => console.log(JSON.stringify(calls)));",
    )
    assert out.index("locale") < out.index("apply") < out.index("ws")
    assert "refresh" in out


# ---------------------------------------------------------------------------
# Simplified CSV template and export follow the page language
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "locale,expected",
    [("zh-CN", "zh-CN"), ("zh-TW", "zh-TW"), ("en-US", "en"), ("fr", "en")],
)
def test_roster_template_download_asks_for_the_page_language(locale, expected):
    out = run_page_functions(
        PAGE,
        ["downloadRosterTemplate"],
        f"""
let currentLocale = {json.dumps(locale)};
const state = {{ race: {{ config: {{}} }} }};
const urls = [];
function adminHeaders() {{ return {{}}; }}
function t(key) {{ return key; }}
function setMessage() {{}}
function parseAttachmentFilename() {{ return "x.csv"; }}
const document = {{
  createElement: () => ({{ click() {{}}, remove() {{}}, set href(v) {{}}, set download(v) {{}} }}),
  body: {{ appendChild() {{}} }},
}};
const URL = {{ createObjectURL: () => "blob:x", revokeObjectURL() {{}} }};
globalThis.fetch = async (url) => {{
  urls.push(url);
  return {{ ok: true, blob: async () => ({{}}), headers: {{ get: () => "" }} }};
}};
""",
        "downloadRosterTemplate().then(() => console.log(JSON.stringify(urls)));",
    )
    assert out == [f"/api/roster/template.csv?mode=individual&lang={expected}"]


def test_page_function_extraction_helper_finds_t():
    assert "game_admin." in extract_function(script_of(PAGE), "t")
    assert run_node("console.log(JSON.stringify(1));") == 1


def test_apply_translations_keeps_the_markup_english_when_a_key_has_no_message():
    # Locale fetch failed (or not finished): data-i18n elements must keep the
    # English already in the HTML instead of being overwritten with raw keys.
    out = run_page_functions(
        PAGE,
        ["t", "applyTranslations"],
        """
let messages = { "game_admin.nav.dashboard": "仪表盘" };
let currentLocale = "zh-CN";
const known = { dataset: { i18n: "nav.dashboard" }, textContent: "Dashboard" };
const unknown = { dataset: { i18n: "nav.system_admin" }, textContent: "System Admin" };
const document = {
  documentElement: { lang: "" },
  querySelectorAll: () => [known, unknown],
};
const node = { textContent: "", classList: { contains: () => false, toggle() {} } };
function $() { return node; }
function renderRace() {}
function renderReadinessPanel() {}
function renderStations() {}
function syncMixedRaceFields() {}
function syncRaceFields() {}
""",
        "applyTranslations(); console.log(JSON.stringify([known.textContent, unknown.textContent]));",
    )
    assert out == ["仪表盘", "System Admin"]
