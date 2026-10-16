"""System Admin (hub_server/static/systemAdmin.html) reads every string from
the hub locale files through /api/locales, like classAdmin and teams do
(docs/zh_cn_i18n_spec.md section 4). Page keys live under `system_admin.`;
the page's t() adds that prefix and falls back to shared global keys. Real
page functions run under node; comments are stripped before any source
inspection.
"""

import json
import re
import subprocess

import pytest

from page_locale import (
    CJK_RE,
    COMPLETE_LOCALES,
    extract_function,
    messages_js,
    raw_messages,
    read_page,
    run_page_functions,
    script_of,
    strip_html_comments,
    strip_js_comments,
)

PAGE = "systemAdmin.html"
PREFIX = "system_admin."


def _page_has_key(key: str, messages: dict) -> bool:
    return f"{PREFIX}{key}" in messages or key in messages


def _referenced_keys() -> set[str]:
    html = strip_html_comments(read_page(PAGE))
    script = strip_js_comments(script_of(PAGE))
    keys = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', html))
    keys |= set(re.findall(r"""\bt\(\s*["']([A-Za-z0-9_.]+)["']""", script))
    return keys


# ---------------------------------------------------------------------------
# Structure
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


def test_script_has_no_inline_dictionaries():
    script = strip_js_comments(script_of(PAGE))
    assert "dictionaries" not in script


def test_no_cjk_literal_remains_in_markup_script_or_style():
    html = strip_html_comments(read_page(PAGE))
    html = re.sub(r"/\*.*?\*/", "", html, flags=re.DOTALL)
    script = strip_js_comments(script_of(PAGE))
    html_without_script = html.replace(script_of(PAGE), "")
    offenders = CJK_RE.findall(script) + CJK_RE.findall(html_without_script)
    assert not offenders, f"CJK literals left in {PAGE}: {offenders[:20]}"


def test_comment_stripped_script_is_still_valid_javascript(tmp_path):
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


def test_page_strings_are_namespaced_and_complete_in_all_three_locales():
    keysets = {
        locale: {k for k in raw_messages(locale) if k.startswith(PREFIX)}
        for locale in COMPLETE_LOCALES
    }
    assert len(keysets["en-US"]) > 120
    assert keysets["zh-TW"] == keysets["en-US"] == keysets["zh-CN"]


def test_page_strings_differ_between_english_and_each_chinese_locale():
    en = raw_messages("en-US")
    for locale in ("zh-TW", "zh-CN"):
        zh = raw_messages(locale)
        untranslated = sorted(k for k in en if k.startswith(PREFIX) and en[k] == zh[k])
        assert not untranslated, (locale, untranslated)


# ---------------------------------------------------------------------------
# t(): namespace first, shared global key second, the key itself last
# ---------------------------------------------------------------------------


def _t(locale: str, keys: list[str]):
    return run_page_functions(
        PAGE,
        ["t"],
        messages_js(locale),
        f"console.log(JSON.stringify({json.dumps(keys)}.map((k) => t(k))));",
    )


def test_t_renders_simplified_chinese_from_the_zh_cn_messages():
    assert _t(
        "zh-CN", ["nav.game_admin", "wifi.level_excellent", "wifi.disconnected"]
    ) == ["比赛管理", "极佳", "未连接"]


def test_t_keeps_traditional_chinese_for_zh_tw_and_english_for_en_us():
    assert _t("zh-TW", ["wifi.level_excellent"]) == ["極佳"]
    assert _t("en-US", ["wifi.level_excellent"]) == ["Excellent"]


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
        'console.log(JSON.stringify(t("message.backup_download_failed", {detail: "boom"})));',
    )
    assert out == "备份下载失败：boom"


def test_a_locale_without_the_page_keys_still_renders_english():
    assert _t("sv", ["wifi.level_excellent"]) == ["Excellent"]


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
const elements = {json.dumps(keys)}.map((key) => ({{
  dataset: {{ i18n: key }}, tagName: "SPAN", textContent: "",
  querySelector: () => null,
}}));
const labelSpan = {{ textContent: "" }};
elements.push({{ dataset: {{ i18n: "nav.signup" }}, tagName: "BUTTON", textContent: "",
  querySelector: () => labelSpan }});
const document = {{
  documentElement: {{ lang: "" }},
  querySelectorAll: () => elements,
}};
const node = {{ textContent: "", classList: {{ contains: () => false, toggle() {{}} }} }};
function $() {{ return node; }}
const state = {{ wifi: null }};
function renderEdges() {{}}
function renderStations() {{}}
function renderUpdate() {{}}
function renderWifiStatus() {{}}
""",
        """
applyTranslations();
console.log(JSON.stringify({
  lang: document.documentElement.lang,
  byKey: Object.fromEntries(elements.slice(0, -1).map((e) => [e.dataset.i18n, e.textContent])),
  buttonSpan: labelSpan.textContent,
}));
""",
    )
    assert out["lang"] == "zh-CN"
    assert out["byKey"]["header.subtitle"] == "Edge 节点状态 · 软件更新 · 系统维护"
    assert out["byKey"]["nav.game_admin"] == "比赛管理"
    # A button holding a <span> gets its text in the span.
    assert out["buttonSpan"] == "报名"
    assert not [k for k, v in out["byKey"].items() if v == k]


def test_wifi_status_renders_in_simplified_chinese():
    out = run_page_functions(
        PAGE,
        ["t", "renderWifiStatus"],
        messages_js("zh-CN") + """
let currentLocale = "zh-CN";
const nodes = {};
function $(id) {
  if (!nodes[id]) nodes[id] = { textContent: "", className: "", setAttribute() {},
    querySelectorAll: () => [] };
  return nodes[id];
}
""",
        """
renderWifiStatus({ connected: true, quality_level: "excellent", rssi_dbm: -40, ssid: "FitRace" });
const connected = [nodes["wifi-level"].textContent, nodes["strip-wifi"].textContent];
renderWifiStatus({ connected: false });
console.log(JSON.stringify({ connected,
  off: [nodes["wifi-level"].textContent, nodes["wifi-sub"].textContent, nodes["strip-wifi"].textContent] }));
""",
    )
    assert out["connected"] == ["极佳", "FitRace -40 dBm"]
    assert out["off"] == ["未连接", "请选择要连接的 Wi-Fi 网络。", "未连接"]


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
  return { ok: true, json: async () => ({ locale: code, messages: { "system_admin.wifi.level_excellent": "LEVEL-" + code } }) };
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
  text: t("wifi.level_excellent"),
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
        "text": "LEVEL-zh-CN",
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
    assert boot.index("await loadLocale(") < boot.index("connectWebSocket")
    assert boot.index("await loadLocale(") < boot.index("applyTranslations")
    assert boot.index("await loadLocale(") < boot.index("refreshAll")
    assert re.search(r"^\s*boot\(\);", script, re.MULTILINE)


def test_boot_still_starts_the_page_when_the_locale_fetch_fails():
    out = run_page_functions(
        PAGE,
        ["boot"],
        """
const calls = [];
const state = { adminToken: "x" };
const localStorage = { getItem: () => "1", setItem() {} };
async function loadLocaleList() { calls.push("list"); }
async function loadLocale() { calls.push("locale"); throw new Error("offline"); }
function updateSignupLink() { calls.push("signup"); }
function applyTranslations() { calls.push("apply"); }
function connectWebSocket() { calls.push("ws"); }
function refreshAll() { calls.push("refresh"); }
""",
        "boot().then(() => console.log(JSON.stringify(calls)));",
    )
    assert out.index("locale") < min(out.index(c) for c in ("ws", "apply", "refresh"))
    assert "signup" in out


def test_apply_translations_keeps_the_markup_english_when_a_key_has_no_message():
    # Locale fetch failed (or not finished): data-i18n elements must keep the
    # English already in the HTML instead of being overwritten with raw keys,
    # including a button whose label sits in a <span>.
    out = run_page_functions(
        PAGE,
        ["t", "applyTranslations"],
        """
let messages = { "system_admin.nav.dashboard": "仪表盘" };
let currentLocale = "zh-CN";
const known = { dataset: { i18n: "nav.dashboard" }, tagName: "SPAN", textContent: "Dashboard", querySelector: () => null };
const unknown = { dataset: { i18n: "nav.game_admin" }, tagName: "SPAN", textContent: "Game Admin", querySelector: () => null };
const innerSpan = { textContent: "Signup" };
const unknownButton = { dataset: { i18n: "nav.signup" }, tagName: "BUTTON", textContent: "", querySelector: () => innerSpan };
const document = {
  documentElement: { lang: "" },
  querySelectorAll: () => [known, unknown, unknownButton],
};
const state = { wifi: null };
const node = { textContent: "", classList: { contains: () => false, toggle() {} } };
function $() { return node; }
function renderEdges() {}
function renderStations() {}
function renderUpdate() {}
function renderWifiStatus() {}
""",
        "applyTranslations(); console.log(JSON.stringify([known.textContent, unknown.textContent, innerSpan.textContent]));",
    )
    assert out == ["仪表盘", "Game Admin", "Signup"]
