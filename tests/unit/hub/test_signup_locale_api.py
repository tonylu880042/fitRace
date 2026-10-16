"""The athlete sign-up page (hub_server/static/signup.html) reads its text from
the hub locale files through /api/locales (docs/zh_cn_i18n_spec.md section
4). Page keys live under `signup_page.`; the page's t() adds that prefix and
falls back to a tiny built-in English map (only for text the page builds in
JavaScript), then to shared global keys (equipment_type.*). Real page
functions run under node; comments are stripped before any source
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
    matching_end,
    messages_js,
    raw_messages,
    read_page,
    run_node,
    run_page_functions,
    script_of,
    strip_html_comments,
    strip_js_comments,
)

PAGE = "signup.html"
PREFIX = "signup_page."
PARTIAL_LOCALES = ("it", "fr", "de-CH", "sv")


def _fallback_const() -> str:
    """The page's own built-in English map, as JS for harnesses that call t()."""
    script = strip_js_comments(script_of(PAGE))
    start = script.index("const FALLBACK_MESSAGES")
    brace = script.index("{", start)
    return script[start : matching_end(script, brace, "{", "}") + 1] + ";\n"


def _messages(locale: str) -> str:
    return _fallback_const() + messages_js(locale)


def _referenced_keys() -> set[str]:
    html = strip_html_comments(read_page(PAGE))
    script = strip_js_comments(script_of(PAGE))
    keys = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', html))
    keys |= set(re.findall(r"""\bt\(\s*["']([A-Za-z0-9_.]+)["']""", script))
    keys |= set(re.findall(r'"(equipment\.[a-z_]+)"', script))
    keys |= set(
        re.findall(r"""setAvatarProcessing\([^)]*?["'](avatar\.[a-z_]+)["']""", script)
    )
    keys |= set(
        re.findall(r"""messageKey\s*=[^;]*?["'](avatar\.[a-z_]+)["']""", script)
    )
    keys |= set(re.findall(r"""\?\s*["'](avatar\.[a-z_]+)["']""", script))
    keys |= set(re.findall(r""":\s*["'](avatar\.[a-z_]+)["']""", script))
    return keys


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_language_select_is_filled_from_the_locale_api_not_hardcoded():
    html = strip_html_comments(read_page(PAGE))
    select = re.search(r'<select[^>]*id="language-select".*?</select>', html, re.S)
    assert select, "language select missing"
    # Only an English placeholder option may be in the markup; the rest of
    # the list (and every label) comes from /api/locales.
    assert select.group(0).count("<option") <= 1
    assert not CJK_RE.search(select.group(0))

    script = strip_js_comments(script_of(PAGE))
    assert "fetch('/api/locales')" in script or 'fetch("/api/locales")' in script
    assert "/api/locales/${encodeURIComponent(" in script


def test_script_has_no_inline_dictionaries():
    script = strip_js_comments(script_of(PAGE))
    assert "signupLocaleOverrides" not in script
    assert "const fallbackMessages" not in script
    assert "signupMessages" not in script


def test_no_cjk_literal_remains_in_markup_script_or_style():
    html = strip_html_comments(read_page(PAGE))
    html = re.sub(r"/\*.*?\*/", "", html, flags=re.DOTALL)
    script = strip_js_comments(script_of(PAGE))
    html_without_script = html.replace(script_of(PAGE), "")
    offenders = CJK_RE.findall(script) + CJK_RE.findall(html_without_script)
    assert not offenders, f"CJK literals left in {PAGE}: {offenders[:20]}"


def test_no_hardcoded_english_station_word_is_built_in_javascript():
    # "Station 3" used to be assembled from a literal in several places.
    script = strip_js_comments(script_of(PAGE))
    assert not re.findall(r"`Station \$\{", script)


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
    # Strict: the page's own key must exist (a same-named global key, e.g. the
    # dashboard's signup.division, would otherwise hide a missing one).
    missing = sorted(
        key for key in _referenced_keys() if f"{PREFIX}{key}" not in messages
    )
    assert not missing, f"{PAGE} asks for keys {locale} lacks: {missing}"


def test_referenced_key_scan_sees_the_flow_texts():
    keys = _referenced_keys()
    for key in (
        "signup.success",
        "signup.error_detail",
        "signup.connection_error",
        "signup.overwrite_warning",
        "avatar.too_large",
        "avatar.compress_failed",
        "equipment.fan_bike",
        "signup.station_name",
    ):
        assert key in keys, key


def test_page_strings_are_namespaced_and_complete_in_all_three_locales():
    keysets = {
        locale: {k for k in raw_messages(locale) if k.startswith(PREFIX)}
        for locale in COMPLETE_LOCALES
    }
    assert len(keysets["en-US"]) >= 38
    assert keysets["zh-TW"] == keysets["en-US"] == keysets["zh-CN"]


def test_existing_translations_of_the_other_locales_moved_into_their_files():
    en_keys = {k for k in raw_messages("en-US") if k.startswith(PREFIX)}
    for locale in PARTIAL_LOCALES:
        own = {k for k in raw_messages(locale) if k.startswith(PREFIX)}
        assert own <= en_keys, (locale, own - en_keys)
        assert f"{PREFIX}signup.submit" in own, locale
        assert f"{PREFIX}signup.athlete_name" in own, locale


def test_page_strings_differ_between_english_and_each_chinese_locale():
    en = raw_messages("en-US")
    for locale in ("zh-TW", "zh-CN"):
        zh = raw_messages(locale)
        untranslated = sorted(k for k in en if k.startswith(PREFIX) and en[k] == zh[k])
        assert not untranslated, (locale, untranslated)


def test_zh_tw_strings_moved_verbatim():
    zh = raw_messages("zh-TW")
    assert zh[f"{PREFIX}signup.subtitle"] == "選手自助報名"
    assert zh[f"{PREFIX}signup.submit"] == "確認報名"
    assert zh[f"{PREFIX}signup.station_unspecified"] == "Station: 未指定"
    assert (
        zh[f"{PREFIX}signup.overwrite_warning"]
        == "注意：此站位已有選手「{name}」登記，送出將會覆蓋！"
    )


# ---------------------------------------------------------------------------
# t()
# ---------------------------------------------------------------------------

T_FUNCTIONS = ["interpolate", "t"]


def _t(locale: str, calls: list[list]):
    return run_page_functions(
        PAGE,
        T_FUNCTIONS,
        _messages(locale),
        f"console.log(JSON.stringify({json.dumps(calls)}.map(([k, v]) => t(k, v || {{}}))));",
    )


def test_t_renders_simplified_chinese_from_the_zh_cn_messages():
    assert _t(
        "zh-CN",
        [
            ["signup.submit"],
            ["signup.success"],
            ["signup.connection_error"],
            ["signup.error_detail", {"detail": "x"}],
        ],
    ) == [
        "确认报名",
        "报名成功！请至现场大屏幕确认您的站位。",
        "连接错误，请确认网络连接。",
        "报名失败：x",
    ]


def test_t_keeps_traditional_chinese_for_zh_tw_and_english_for_en_us():
    assert _t("zh-TW", [["signup.submit"]]) == ["確認報名"]
    assert _t("en-US", [["signup.submit"]]) == ["Register"]


def test_t_serves_each_other_locale_its_own_translation_and_english_for_the_rest():
    assert _t("it", [["signup.submit"], ["signup.success"]]) == [
        "Registrati",
        "Registration complete. Please check the venue screen for your station.",
    ]


def test_t_falls_back_to_a_shared_global_key_then_to_the_key():
    out = _t("zh-CN", [["equipment_type.upright_bike"], ["no.such.key"]])
    assert out == ["直立式健身车", "no.such.key"]


def test_t_uses_the_built_in_english_when_no_locale_has_loaded():
    out = run_page_functions(
        PAGE,
        T_FUNCTIONS,
        _fallback_const() + "let messages = {};",
        """
console.log(JSON.stringify([
  t("signup.connection_error"),
  t("signup.error_detail", { detail: "boom" }),
  t("avatar.compress_failed"),
  t("signup.station_name", { number: 4 }),
  t("signup.athlete_name"),
]));
""",
    )
    en = raw_messages("en-US")
    assert out[0] == en[f"{PREFIX}signup.connection_error"]
    assert out[1] == "Registration failed: boom"
    assert out[2] == en[f"{PREFIX}avatar.compress_failed"]
    assert out[3] == "Station 4"
    # HTML-only text has no built-in copy: the English in the markup shows.
    assert out[4] == "signup.athlete_name"


def test_built_in_english_never_drifts_from_the_en_us_locale_file():
    script = strip_js_comments(script_of(PAGE))
    start = script.index("const FALLBACK_MESSAGES")
    brace = script.index("{", start)
    literal = script[brace : matching_end(script, brace, "{", "}") + 1]
    built_in = run_node(f"console.log(JSON.stringify({literal}));")
    assert built_in
    en = raw_messages("en-US")
    for key, value in built_in.items():
        assert value == en[f"{PREFIX}{key}"], key


# ---------------------------------------------------------------------------
# The whole flow, rendered in Simplified Chinese
# ---------------------------------------------------------------------------


def test_apply_translations_localizes_every_visible_label_and_placeholder():
    html = read_page(PAGE)
    text_keys = sorted(set(re.findall(r'data-i18n="([^"]+)"', html)))
    placeholder_keys = sorted(set(re.findall(r'data-i18n-placeholder="([^"]+)"', html)))
    out = run_page_functions(
        PAGE,
        [*T_FUNCTIONS, "applyTranslations"],
        _messages("zh-CN") + f"""
const texts = {json.dumps(text_keys)}.map((key) => ({{ dataset: {{ i18n: key }}, innerText: "" }}));
const holders = {json.dumps(placeholder_keys)}.map((key) => ({{ dataset: {{ i18nPlaceholder: key }}, placeholder: "" }}));
const document = {{
  querySelectorAll: (selector) => (selector === "[data-i18n]" ? texts : holders),
}};
""",
        """
applyTranslations();
console.log(JSON.stringify({
  text: Object.fromEntries(texts.map((e) => [e.dataset.i18n, e.innerText])),
  placeholder: Object.fromEntries(holders.map((e) => [e.dataset.i18nPlaceholder, e.placeholder])),
}));
""",
    )
    assert out["text"]["signup.subtitle"] == "选手自助报名"
    assert out["text"]["signup.avatar"] == "选手头像"
    # The success and error panels are part of the markup and are localized.
    assert out["text"]["signup.success"] == "报名成功！请至现场大屏幕确认您的站位。"
    assert out["text"]["signup.error"] == "报名失败，请稍后再试。"
    assert out["text"]["signup.division_men"] == "男子组"
    assert out["placeholder"]["signup.athlete_placeholder"] == "请输入姓名..."
    assert not [k for k, v in out["text"].items() if v == k]
    assert not [k for k, v in out["placeholder"].items() if v == k]


STATION_PRELUDE = """
const station = 3;
const hasQueryStation = true;
const equipmentNames = {
  fan_bike: "equipment.fan_bike", spin_bike: "equipment.spin_bike",
  treadmill: "equipment.treadmill", rower: "equipment.rower", ski_erg: "equipment.ski_erg",
};
const stationLbl = { innerText: "", style: {} };
const warningDiv = { innerText: "", style: {} };
const nodes = {
  "station-select-group": { style: {} },
  "station-select": { innerHTML: "", value: "", appendChild(o) { this.options.push(o); }, options: [] },
};
const document = {
  getElementById: (id) => nodes[id],
  createElement: () => ({ value: "", innerText: "" }),
};
const console_error = console.error; console.error = () => {};
"""


def _init_stations(locale: str, stations: dict, query_station=True):
    return run_page_functions(
        PAGE,
        [*T_FUNCTIONS, "equipmentLabel", "stationName", "initStations"],
        _messages(locale)
        + STATION_PRELUDE.replace(
            "const hasQueryStation = true;",
            f"const hasQueryStation = {json.dumps(query_station)};",
        ),
        f"""
globalThis.fetch = async () => ({{ ok: true, json: async () => ({{ stations: {json.dumps(stations)} }}) }});
initStations().then(() => console.log(JSON.stringify({{
  label: stationLbl.innerText, warning: warningDiv.innerText,
  warningShown: warningDiv.style.display,
  options: nodes["station-select"].options.map((o) => o.innerText),
}})));
""",
    )


def test_station_badge_and_overwrite_warning_in_simplified_chinese():
    out = _init_stations(
        "zh-CN",
        {
            "3": {
                "equipment_type": "fan_bike",
                "node_id": "n1",
                "node_display_name": "BIKE_03",
                "registered": True,
                "athlete_name": None,
            }
        },
    )
    assert out["label"] == "站位 3 - 风阻单车 (设备：BIKE_03)"
    assert out["warning"] == "注意：此站位已有选手「站位 3」登记，提交将会覆盖！"
    assert out["warningShown"] == "block"


def test_unbound_station_badge_in_simplified_chinese():
    out = _init_stations("zh-CN", {"3": {"equipment_type": None, "registered": False}})
    assert out["label"] == "站位 3 (未绑定设备)"
    assert out["warningShown"] == "none"


def test_equipment_without_a_signup_label_uses_the_shared_translation():
    out = _init_stations(
        "zh-CN", {"3": {"equipment_type": "upright_bike", "registered": False}}
    )
    assert out["label"].startswith("站位 3 - 直立式健身车")


def test_station_picker_options_in_simplified_chinese():
    out = _init_stations(
        "zh-CN",
        {
            "1": {"equipment_type": "treadmill", "registered": False},
            "2": {"equipment_type": None, "registered": True, "athlete_name": "Amy"},
        },
        query_station=False,
    )
    assert out["options"] == [
        "站位 1 - 跑步机",
        "站位 2 - 未指派设备 [已登记：Amy]",
    ]


def test_station_picker_options_stay_english_with_the_built_in_fallback():
    out = run_page_functions(
        PAGE,
        [*T_FUNCTIONS, "equipmentLabel", "stationName", "initStations"],
        _fallback_const()
        + "let messages = {};"
        + STATION_PRELUDE.replace(
            "const hasQueryStation = true;", "const hasQueryStation = false;"
        ),
        """
globalThis.fetch = async () => ({ ok: true, json: async () => ({ stations: {
  "1": { equipment_type: "treadmill", registered: false } } }) });
initStations().then(() => console.log(JSON.stringify(nodes["station-select"].options.map((o) => o.innerText))));
""",
    )
    assert out == ["Station 1 - Treadmill"]


SUBMIT_PRELUDE = """
const hasQueryStation = true;
const station = 3;
let avatarProcessing = false;
let avatarBase64 = null;
const athleteNameInput = { value: "" };
const teamNameInput = { value: "" };
const divisionSelect = { value: "" };
const successMsg = { style: {}, innerText: "" };
const errorMsg = { style: {}, innerText: "" };
const submitBtn = { disabled: false };
const btnText = { style: {} };
const btnSpinner = { style: {} };
const customPreview = { style: {}, innerText: "" };
const defaultAvatarOption = {};
function setActiveAvatarOption() {}
function setAvatarProcessing() {}
"""


def _submit(locale: str, fetch_js: str):
    return run_page_functions(
        PAGE,
        [*T_FUNCTIONS, "submitForm"],
        _messages(locale) + SUBMIT_PRELUDE,
        f"""
globalThis.fetch = {fetch_js};
submitForm({{ preventDefault() {{}} }}).then(() => console.log(JSON.stringify(errorMsg.innerText)));
""",
    )


def test_registration_failure_message_in_simplified_chinese():
    out = _submit(
        "zh-CN",
        "async () => ({ ok: false, json: async () => ({ detail: 'Station busy' }) })",
    )
    assert out == "报名失败：Station busy"


def test_registration_failure_without_detail_falls_back_to_the_generic_message():
    out = _submit("zh-CN", "async () => ({ ok: false, json: async () => ({}) })")
    assert out == "报名失败：报名失败，请稍后再试。"


def test_connection_error_in_simplified_chinese():
    out = _submit("zh-CN", "async () => { throw new Error('offline'); }")
    assert out == "连接错误，请确认网络连接。"


def test_avatar_status_messages_in_simplified_chinese():
    out = run_page_functions(
        PAGE,
        [*T_FUNCTIONS, "setAvatarProcessing"],
        _messages("zh-CN") + """
let avatarProcessing = false;
const avatarProcessingMsg = { innerText: "", className: "" };
const submitBtn = { disabled: false };
""",
        """
const seen = [];
for (const key of ["avatar.processing", "avatar.ready", "avatar.too_large", "avatar.compress_failed"]) {
  setAvatarProcessing(false, key);
  seen.push(avatarProcessingMsg.innerText);
}
console.log(JSON.stringify(seen));
""",
    )
    assert out == [
        "正在手机上压缩照片...",
        "照片已准备完成，将以压缩版本上传。",
        "照片太大，请选择 8MB 以下的图片。",
        "无法处理此照片，请选择另一张图片。",
    ]


# ---------------------------------------------------------------------------
# Loading and switching languages
# ---------------------------------------------------------------------------

LOADER_PRELUDE = _fallback_const() + """
const store = {};
const localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = v; },
};
const navigator = { language: "en-US" };
const requested = [];
const doc = { documentElement: { lang: "" } };
const document = doc;
const languageSelect = { value: "", innerHTML: "" };
function escapeHtml(v) { return String(v); }
let applied = 0;
function applyTranslations() { applied += 1; }
let stationsInitialised = 0;
async function initStations() { stationsInitialised += 1; }
let currentLocale = "en-US";
let messages = {};
globalThis.fetch = async (url) => {
  requested.push(url.split("?")[0]);
  if (url === "/api/locales") {
    return { ok: true, json: async () => ({ locales: [
      { code: "en-US", label: "English" }, { code: "zh-CN", label: "简体中文" } ] }) };
  }
  const code = decodeURIComponent(url.split("?")[0].split("/").pop());
  return { ok: true, json: async () => ({ locale: code, messages: { "signup_page.signup.submit": "GO-" + code } }) };
};
"""


def test_load_locale_fetches_stores_and_re_renders_the_page():
    out = run_page_functions(
        PAGE,
        [*T_FUNCTIONS, "loadLocale"],
        LOADER_PRELUDE,
        """
loadLocale("zh-CN").then(() => console.log(JSON.stringify({
  requested, locale: currentLocale, stored: store["fitrace.locale"],
  lang: doc.documentElement.lang, selected: languageSelect.value,
  applied, stations: stationsInitialised, text: t("signup.submit"),
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
        "stations": 1,
        "text": "GO-zh-CN",
    }


def test_load_locale_survives_a_failed_fetch_and_keeps_rendering():
    out = run_page_functions(
        PAGE,
        [*T_FUNCTIONS, "loadLocale"],
        LOADER_PRELUDE.replace(
            "globalThis.fetch = async (url) => {",
            "globalThis.fetch = async (url) => { throw new Error('offline');",
            1,
        ),
        """
loadLocale("zh-CN").then(() => console.log(JSON.stringify({
  applied, stations: stationsInitialised, text: t("signup.connection_error"),
})));
""",
    )
    assert out["applied"] == 1 and out["stations"] == 1
    assert out["text"] == raw_messages("en-US")[f"{PREFIX}signup.connection_error"]


def test_locale_list_populates_the_language_select_from_the_api():
    out = run_page_functions(
        PAGE,
        ["loadLocaleList"],
        LOADER_PRELUDE.replace(
            'let currentLocale = "en-US";', 'let currentLocale = "zh-CN";'
        ),
        """
loadLocaleList().then(() => console.log(JSON.stringify({
  html: languageSelect.innerHTML, value: languageSelect.value, requested })));
""",
    )
    assert out["requested"] == ["/api/locales"]
    assert out["value"] == "zh-CN"
    assert '<option value="zh-CN">简体中文</option>' in out["html"]


@pytest.mark.parametrize(
    "language,saved,expected",
    [
        ("zh-CN", None, "zh-CN"),
        ("zh-Hans", None, "zh-CN"),
        ("zh-TW", None, "zh-TW"),
        ("zh", None, "zh-TW"),
        ("fr-CH", None, "fr"),
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


def test_start_up_loads_the_language_list_and_the_locale():
    script = strip_js_comments(script_of(PAGE))
    init = extract_function(script, "initializeSignupPage")
    assert "loadLocaleList()" in init
    assert "await loadLocale(" in init
