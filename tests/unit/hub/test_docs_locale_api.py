"""The operations manual (hub_server/static/docs.html) reads its text from the
hub locale files through /api/locales (docs/zh_cn_i18n_spec.md section 4):
every lang-en / lang-zh pair became one element carrying data-i18n (plain
text) or data-i18n-html (blocks with markup -- only on this page, and only
because the values come from our own locale files). Keys live under
`docs.`. Real page functions run under node.
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

PAGE = "docs.html"
PREFIX = "docs."

ALLOWED_TAGS = {
    "p", "ul", "ol", "li", "strong", "code", "h3", "table", "tr", "th", "td",
    "div", "a", "span", "br", "em",
}  # fmt: skip


def _html() -> str:
    return strip_html_comments(read_page(PAGE))


def _elements(attribute: str) -> list[tuple[str, str]]:
    """[(key, inner html)] of every element carrying `attribute`."""
    html = _html()
    found = []
    for match in re.finditer(rf'<(\w+)([^>]*?)\s{attribute}="([^"]+)"([^>]*)>', html):
        tag, key = match.group(1), match.group(3)
        start = match.end()
        depth = 1
        for tag_match in re.finditer(rf"<(/?){tag}\b[^>]*>", html[start:]):
            depth += -1 if tag_match.group(1) else 1
            if depth == 0:
                found.append((key, html[start : start + tag_match.start()]))
                break
    return found


def _normalized(markup: str) -> str:
    return " ".join(markup.split())


def _all_keys() -> set[str]:
    return {key for key, _ in _elements("data-i18n")} | {
        key for key, _ in _elements("data-i18n-html")
    }


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_language_pairs_and_toggle_buttons_are_gone():
    html = _html()
    script = strip_js_comments(script_of(PAGE))
    for needle in ("lang-en", "lang-zh", "data-lang", "lang-toggle", 'id="lang-'):
        assert needle not in html, needle
    assert not re.search(r"\bsetLang\b", script)
    assert "dataset.lang" not in script


def test_language_picker_is_a_select_filled_from_the_locale_api():
    assert re.search(r'<select[^>]*id="language-select"', _html())
    script = strip_js_comments(script_of(PAGE))
    assert 'fetch("/api/locales")' in script
    assert "/api/locales/${encodeURIComponent(locale)}" in script


def test_no_cjk_literal_remains_in_markup_script_or_style():
    html = re.sub(r"/\*.*?\*/", "", _html(), flags=re.DOTALL)
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


def test_the_page_is_actually_marked_up_for_translation():
    assert len(_elements("data-i18n")) >= 15
    assert len(_elements("data-i18n-html")) >= 8
    keys = _all_keys()
    for section in (
        "overview", "setup", "binding", "stations", "wifi", "updates", "power",
        "troubleshooting",
    ):  # fmt: skip
        assert f"docs.{section}.body" in keys, section
        assert f"docs.{section}.title" in keys, section
        assert f"docs.toc.{section}" in keys, section


# ---------------------------------------------------------------------------
# Locale files carry every string, identically structured
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("locale", COMPLETE_LOCALES)
def test_every_key_the_page_uses_exists_in_each_complete_locale(locale):
    messages = raw_messages(locale)
    missing = sorted(key for key in _all_keys() if key not in messages)
    assert not missing, f"{PAGE} asks for keys {locale} lacks: {missing}"


def test_docs_keys_are_complete_in_all_three_locales_and_nothing_else_has_them():
    keysets = {
        locale: {k for k in raw_messages(locale) if k.startswith(PREFIX)}
        for locale in COMPLETE_LOCALES
    }
    assert keysets["zh-TW"] == keysets["en-US"] == keysets["zh-CN"]
    # Every docs.* key is used by the page (no orphans left behind).
    assert keysets["en-US"] == _all_keys()
    for locale in ("it", "fr", "de-CH", "sv"):
        assert not [k for k in raw_messages(locale) if k.startswith(PREFIX)], locale


def test_english_in_the_markup_is_exactly_the_en_us_locale_text():
    """The English shown before (or without) the locale fetch must not drift
    from en-US.json."""
    en = raw_messages("en-US")
    for attribute in ("data-i18n", "data-i18n-html"):
        for key, inner in _elements(attribute):
            assert _normalized(inner) == _normalized(en[key]), key


def test_chinese_values_differ_from_english():
    en = raw_messages("en-US")
    for locale in ("zh-TW", "zh-CN"):
        zh = raw_messages(locale)
        same = sorted(k for k in en if k.startswith(PREFIX) and en[k] == zh[k])
        assert not same, (locale, same)


def _tags(markup: str) -> list[str]:
    return re.findall(r"</?(\w+)", markup)


def test_html_values_use_only_safe_tags_and_no_event_handlers():
    for locale in COMPLETE_LOCALES:
        for key, value in raw_messages(locale).items():
            if not key.startswith(PREFIX):
                continue
            assert set(_tags(value)) <= ALLOWED_TAGS, (locale, key, set(_tags(value)))
            assert not re.search(r"\son\w+\s*=", value), (locale, key)
            assert "javascript:" not in value.lower(), (locale, key)


def test_translated_blocks_keep_the_english_markup_structure():
    en = raw_messages("en-US")
    for locale in ("zh-TW", "zh-CN"):
        zh = raw_messages(locale)
        for key in en:
            if not key.startswith(PREFIX):
                continue
            assert _tags(zh[key]) == _tags(en[key]), (locale, key)


def test_footer_keeps_the_api_docs_link_in_every_locale():
    for locale in COMPLETE_LOCALES:
        assert '<a href="/docs"' in raw_messages(locale)["docs.footer"], locale


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

APPLY_FUNCTIONS = ["applyTranslations"]


def _apply(locale: str):
    texts = sorted({key for key, _ in _elements("data-i18n")})
    blocks = sorted({key for key, _ in _elements("data-i18n-html")})
    return run_page_functions(
        PAGE,
        APPLY_FUNCTIONS,
        messages_js(locale) + f"""
let currentLocale = {json.dumps(locale)};
const texts = {json.dumps(texts)}.map((key) => ({{ dataset: {{ i18n: key }}, textContent: "" }}));
const blocks = {json.dumps(blocks)}.map((key) => ({{ dataset: {{ i18nHtml: key }}, innerHTML: "" }}));
const document = {{
  documentElement: {{ lang: "" }},
  querySelectorAll: (selector) => (selector === "[data-i18n]" ? texts : blocks),
}};
""",
        """
applyTranslations();
console.log(JSON.stringify({
  lang: document.documentElement.lang,
  text: Object.fromEntries(texts.map((e) => [e.dataset.i18n, e.textContent])),
  html: Object.fromEntries(blocks.map((e) => [e.dataset.i18nHtml, e.innerHTML])),
}));
""",
    )


def test_manual_renders_in_simplified_chinese():
    out = _apply("zh-CN")
    assert out["lang"] == "zh-CN"
    assert out["text"]["docs.toc.overview"] == "1 · 系统概览"
    assert out["text"]["docs.overview.title"] == "系统概览"
    overview = out["html"]["docs.overview.body"]
    assert "树莓派" in overview
    assert "<code>uart-1</code>" in overview
    assert "<table>" in overview and "</table>" in overview
    assert "System Overview" not in json.dumps(out, ensure_ascii=False)
    # No key is left showing itself.
    assert not [k for k, v in out["text"].items() if v == k]
    assert not [k for k, v in out["html"].items() if v == k]


def test_manual_keeps_rendering_traditional_chinese_for_zh_tw():
    out = _apply("zh-TW")
    assert out["lang"] == "zh-TW"
    assert out["text"]["docs.overview.title"] == "系統概觀"
    assert "樹莓派" in out["html"]["docs.overview.body"]


def test_manual_renders_english_for_en_us():
    out = _apply("en-US")
    assert out["text"]["docs.overview.title"] == "System Overview"
    assert "Raspberry Pi" in out["html"]["docs.overview.body"]


def test_elements_without_a_loaded_message_keep_their_english_markup():
    out = run_page_functions(
        PAGE,
        APPLY_FUNCTIONS,
        """
let messages = {};
const text = { dataset: { i18n: "docs.title" }, textContent: "Operations Manual" };
const block = { dataset: { i18nHtml: "docs.setup.body" }, innerHTML: "<p>English</p>" };
const document = {
  documentElement: { lang: "" },
  querySelectorAll: (selector) => (selector === "[data-i18n]" ? [text] : [block]),
};
let currentLocale = "zh-CN";
""",
        "applyTranslations(); console.log(JSON.stringify({ text, block }));",
    )
    assert out["text"]["textContent"] == "Operations Manual"
    assert out["block"]["innerHTML"] == "<p>English</p>"


def test_text_elements_use_text_content_and_html_blocks_use_inner_html():
    # A "<" in a plain data-i18n value must never become markup.
    out = run_page_functions(
        PAGE,
        APPLY_FUNCTIONS,
        """
let messages = { "x.text": "<b>not bold</b>", "x.html": "<b>bold</b>" };
const text = { dataset: { i18n: "x.text" }, textContent: "", innerHTML: "" };
const block = { dataset: { i18nHtml: "x.html" }, textContent: "", innerHTML: "" };
const document = {
  documentElement: { lang: "" },
  querySelectorAll: (selector) => (selector === "[data-i18n]" ? [text] : [block]),
};
let currentLocale = "en-US";
""",
        "applyTranslations(); console.log(JSON.stringify({ text, block }));",
    )
    assert out["text"]["textContent"] == "<b>not bold</b>"
    assert out["text"]["innerHTML"] == ""
    assert out["block"]["innerHTML"] == "<b>bold</b>"


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
  return { ok: true, json: async () => ({ locale: code, messages: { "docs.title": "T-" + code } }) };
};
"""


def test_set_language_loads_the_locale_stores_the_choice_and_re_renders():
    out = run_page_functions(
        PAGE,
        ["loadLocale", "setLanguage"],
        LOADER_PRELUDE,
        """
setLanguage("zh-CN").then(() => console.log(JSON.stringify({
  requested, locale: currentLocale, stored: store["fitrace.locale"],
  lang: doc.documentElement.lang, selected: select.value, applied,
  text: messages["docs.title"],
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
        "text": "T-zh-CN",
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


def test_start_up_waits_for_the_locale_and_still_runs_without_it():
    script = strip_js_comments(script_of(PAGE))
    boot = extract_function(script, "boot")
    assert boot.index("await loadLocale(") < boot.index("applyTranslations")
    assert re.search(r"^\s*boot\(\);", script, re.MULTILINE)

    out = run_page_functions(
        PAGE,
        ["boot"],
        """
const calls = [];
async function loadLocaleList() { calls.push("list"); }
async function loadLocale() { calls.push("locale"); throw new Error("offline"); }
function applyTranslations() { calls.push("apply"); }
""",
        "boot().then(() => console.log(JSON.stringify(calls)));",
    )
    assert out == ["list", "locale", "apply"]
