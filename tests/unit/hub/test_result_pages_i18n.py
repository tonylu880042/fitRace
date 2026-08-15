"""Six-locale i18n coverage for the two athlete-facing results pages.

hub_server/static/result.html (the personal result page reached from a QR
code) and hub_server/static/results.html (the venue results wall) used to
carry their own small {en: {...}, zh: {...}} inline dictionaries and a
document.documentElement.dataset.lang switch -- completely separate from the
six-locale system (en-US, zh-TW, it, fr, de-CH, sv) that GET /api/locales/
{code} already serves to index.html and classAdmin.html. Both pages now load
their strings from that same endpoint via a t()/messages pair and a
data-i18n attribute pass, exactly like those two pages.

The race-type / metric axis labels (distance, time, calories, max_power,
watts) used to be defined three times across the two files (result.html's
own raceTypeLabels table, plus results.html's raceTypeLabels table AND its
separate getMetricLabel() table, which were literally identical). All three
now resolve through the same race_type.* locale keys via getRaceTypeLabel()
(and results.html's getMetricLabel(), which now just delegates to it).

Per CLAUDE.md's testability guidance, this module executes the real
functions under `node -e` (brace-depth extraction, mirroring
tests/unit/hub/test_dashboard_i18n.py and
tests/unit/hub/test_result_pages_anonymous_display.py) so a test failure
means the translated string genuinely failed to reach the rendered output --
not merely that a t()/data-i18n reference exists somewhere in the source.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"
LOCALES_DIR = (
    Path(__file__).resolve().parents[3] / "hub_server" / "infrastructure" / "locales"
)

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CJK_RE = re.compile(r"[一-鿿]")


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read(page: str) -> str:
    return (STATIC_DIR / page).read_text(encoding="utf-8")


def _stripped_script(page: str) -> str:
    source = _read(page)
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


def _matching_brace_end(source: str, open_idx: int) -> int:
    """Mirrors tests/unit/hub/test_dashboard_class_board.py."""
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


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _t_stub() -> str:
    return "const t = (key) => `T[${key}]`;\n"


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as file:
        return json.load(file)


# -- 1. getRaceTypeLabel on both pages routes every race type through t() --


def _run_get_race_type_label(page: str, race_type_js: str) -> str:
    source = _stripped_script(page)
    fn = _extract_function(source, "getRaceTypeLabel")
    script = _t_stub() + fn + "\n" + f"console.log(getRaceTypeLabel({race_type_js}));"
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


RACE_TYPES_AND_KEYS = [
    ('"distance"', "race_type.distance"),
    ('"time"', "race_type.time"),
    ('"calories"', "race_type.calories"),
    ('"max_power"', "race_type.max_power"),
    ('"watts"', "race_type.watts"),
]


def test_result_html_get_race_type_label_routes_every_type_through_t():
    for race_type_js, key in RACE_TYPES_AND_KEYS:
        assert _run_get_race_type_label("result.html", race_type_js) == f"T[{key}]"


def test_results_html_get_race_type_label_routes_every_type_through_t():
    for race_type_js, key in RACE_TYPES_AND_KEYS:
        assert _run_get_race_type_label("results.html", race_type_js) == f"T[{key}]"


def test_get_race_type_label_falls_back_to_raw_value_for_unknown_type():
    """An unrecognised race type must still surface something (not blank)
    rather than silently disappearing -- mirrors
    test_race_state_label_unrecognised_state_falls_back_to_raw_value in
    test_dashboard_i18n.py."""
    out = _run_get_race_type_label("result.html", '"bogus_type"')
    assert out == "bogus_type"
    assert not out.startswith("T[")


# -- 2. results.html's getMetricLabel is not a second hardcoded table -- it
# delegates to the exact same race_type.* keys as getRaceTypeLabel, closing
# the "identical table defined twice in one file" duplication.


def _run_get_metric_label(race_type_js: str) -> str:
    source = _stripped_script("results.html")
    race_fn = _extract_function(source, "getRaceTypeLabel")
    metric_fn = _extract_function(source, "getMetricLabel")
    script = (
        _t_stub()
        + race_fn
        + "\n"
        + metric_fn
        + "\n"
        + f"console.log(getMetricLabel({race_type_js}));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def test_results_html_get_metric_label_matches_race_type_label_keys():
    for race_type_js, key in RACE_TYPES_AND_KEYS:
        assert _run_get_metric_label(race_type_js) == f"T[{key}]"


# -- 3. result.html's createMetric writes the t()-looked-up label into the
# actual metric-label cell HTML, not just a literal string that happens to
# look similar.


def _run_create_metric(label_key_js: str, value_js: str, unit_js: str) -> str:
    source = _stripped_script("result.html")
    escape_fn = (
        "function escapeHtml(str) {\n"
        "  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', \"'\": '&#039;' };\n"
        "  return String(str || '').replace(/[&<>\"']/g, m => map[m]);\n"
        "}\n"
    )
    fn = _extract_function(source, "createMetric")
    script = (
        _t_stub() + escape_fn + "class FakeEl {\n"
        "  constructor() { this.className = ''; this.innerHTML = ''; }\n"
        "}\n"
        + "const document = { createElement: (tag) => new FakeEl() };\n"
        + fn
        + "\n"
        + f"const el = createMetric({label_key_js}, {value_js}, {unit_js});\n"
        + "console.log(el.innerHTML);"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout


def test_create_metric_writes_translated_label_into_rendered_html():
    html = _run_create_metric('"race_type.distance"', '"500"', '"m"')
    assert "T[race_type.distance]" in html
    assert "500" in html
    assert "m" in html
    assert "Distance" not in html


def test_create_metric_never_falls_back_to_hardcoded_english_label():
    html = _run_create_metric('"race_type.max_power"', '"300"', '"W"')
    assert "T[race_type.max_power]" in html
    assert "Max Power" not in html


# -- 4. applyTranslations() actually writes REAL locale strings (not just a
# marker) into the DOM for a non-English locale. This is the check CLAUDE.md
# calls out specifically: a helper whose result never reaches the DOM.


def _run_apply_translations(page: str, locale: str, i18n_keys) -> dict:
    source = _stripped_script(page)
    fn = _extract_function(source, "applyTranslations")
    messages = _load_locale(locale)
    script = (
        f"const messages = {json.dumps(messages)};\n"
        + "function t(key) { return messages[key] || key; }\n"
        + "class FakeEl {\n"
        "  constructor(key) { this.dataset = { i18n: key }; this.textContent = ''; }\n"
        "}\n"
        + f"const elements = {json.dumps(i18n_keys)}.map((k) => new FakeEl(k));\n"
        + "const document = { querySelectorAll: (sel) => elements };\n"
        + fn
        + "\n"
        + "applyTranslations();\n"
        + "console.log(JSON.stringify(elements.map((e) => e.textContent)));"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    values = json.loads(result.stdout)
    return dict(zip(i18n_keys, values))


def test_result_html_apply_translations_writes_real_de_ch_strings_to_dom():
    keys = ["result.error_title", "result.back_button", "race_type.distance"]
    rendered = _run_apply_translations("result.html", "de-CH", keys)
    de = _load_locale("de-CH")
    for key in keys:
        assert rendered[key] == de[key]
    assert rendered["result.error_title"] == "Ergebnis nicht gefunden"
    assert rendered["result.back_button"] == "Zurück zur Übersicht"
    assert rendered["race_type.distance"] == "Distanz"


def test_results_html_apply_translations_writes_real_sv_strings_to_dom():
    keys = ["results.page_title", "results.empty_title", "race_type.max_power"]
    rendered = _run_apply_translations("results.html", "sv", keys)
    sv = _load_locale("sv")
    for key in keys:
        assert rendered[key] == sv[key]
    assert rendered["results.page_title"] == "Resultat"
    assert rendered["results.empty_title"] == "Inga tävlingar än"
    assert rendered["race_type.max_power"] == "Maxeffekt"


def test_result_html_apply_translations_writes_real_fr_strings_to_dom():
    keys = ["result.error_hint", "result.team_label", "result.station_label"]
    rendered = _run_apply_translations("result.html", "fr", keys)
    fr = _load_locale("fr")
    for key in keys:
        assert rendered[key] == fr[key]


# -- 5. Every i18n key these two pages reference (t("...") calls in the
# inline <script>, and data-i18n attribute values in the HTML) must exist in
# en-US.json -- mirrors
# test_dashboard_i18n.py::test_every_referenced_i18n_key_exists_in_en_us_locale.

_T_CALL_RE = re.compile(r'(?<![A-Za-z0-9_$])t\(\s*"([^"]+)"')
_DATA_I18N_ATTR_RE = re.compile(r'data-i18n="([^"]+)"')


def _referenced_i18n_keys(page: str) -> set:
    script_keys = set(_T_CALL_RE.findall(_stripped_script(page)))
    attr_keys = set(_DATA_I18N_ATTR_RE.findall(_read(page)))
    return script_keys | attr_keys


def test_every_referenced_i18n_key_exists_in_en_us_locale():
    keys = _referenced_i18n_keys("result.html") | _referenced_i18n_keys("results.html")
    assert (
        len(keys) >= 10
    ), f"only {len(keys)} i18n keys extracted -- the extraction regex may be broken"
    en = _load_locale("en-US")
    missing = sorted(key for key in keys if key not in en)
    assert not missing, f"result/results pages reference missing i18n keys: {missing}"


# -- 6. The new keys exist, are symmetric, and hold the exact intended
# translation in every locale (not a silently-reused English string, and not
# a placeholder). tests/unit/hub/test_i18n_locales.py already enforces
# global key symmetry across all locale files; this pins the actual VALUES
# for this batch of keys specifically.

NEW_LOCALE_KEYS = [
    "race_type.distance",
    "race_type.time",
    "race_type.calories",
    "race_type.max_power",
    "race_type.watts",
    "result.error_title",
    "result.error_hint",
    "result.back_button",
    "result.loading",
    "result.team_label",
    "result.station_label",
    "results.page_title",
    "results.subtitle",
    "results.meta_athletes",
    "results.updating",
    "results.empty_title",
    "results.empty_hint",
]

EXPECTED_VALUES = {
    "en-US": {
        "race_type.distance": "Distance",
        "race_type.time": "Time",
        "race_type.calories": "Calories",
        "race_type.max_power": "Max Power",
        "race_type.watts": "Power",
        "result.error_title": "Result not found",
        "result.back_button": "Back to Dashboard",
        "result.team_label": "Team",
        "result.station_label": "Station",
        "results.page_title": "Results",
        "results.meta_athletes": "Athletes",
        "results.empty_title": "No Races Yet",
    },
    "zh-TW": {
        "race_type.distance": "距離",
        "race_type.time": "時間",
        "race_type.calories": "卡路里",
        "race_type.max_power": "最大功率",
        "race_type.watts": "功率",
        "result.error_title": "找不到成績",
        "result.back_button": "回儀表板",
        "result.team_label": "隊伍",
        "result.station_label": "站位",
        "results.page_title": "成績",
        "results.meta_athletes": "選手數",
        "results.empty_title": "尚無比賽",
    },
    "de-CH": {
        "race_type.distance": "Distanz",
        "race_type.time": "Zeit",
        "race_type.calories": "Kalorien",
        "race_type.max_power": "Max. Leistung",
        "race_type.watts": "Leistung",
        "result.error_title": "Ergebnis nicht gefunden",
        "result.back_button": "Zurück zur Übersicht",
        "result.team_label": "Team",
        "result.station_label": "Station",
        "results.page_title": "Ergebnisse",
        "results.meta_athletes": "Athleten",
        "results.empty_title": "Noch keine Rennen",
    },
    "fr": {
        "race_type.distance": "Distance",
        "race_type.time": "Temps",
        "race_type.calories": "Calories",
        "race_type.max_power": "Puissance max",
        "race_type.watts": "Puissance",
        "result.error_title": "Résultat introuvable",
        "result.back_button": "Retour au tableau de bord",
        "result.team_label": "Équipe",
        "result.station_label": "Poste",
        "results.page_title": "Résultats",
        "results.meta_athletes": "Athlètes",
        "results.empty_title": "Aucune course pour le moment",
    },
    "it": {
        "race_type.distance": "Distanza",
        "race_type.time": "Tempo",
        "race_type.calories": "Calorie",
        "race_type.max_power": "Potenza max",
        "race_type.watts": "Potenza",
        "result.error_title": "Risultato non trovato",
        "result.back_button": "Torna al cruscotto",
        "result.team_label": "Squadra",
        "result.station_label": "Postazione",
        "results.page_title": "Risultati",
        "results.meta_athletes": "Atleti",
        "results.empty_title": "Nessuna gara ancora",
    },
    "sv": {
        "race_type.distance": "Distans",
        "race_type.time": "Tid",
        "race_type.calories": "Kalorier",
        "race_type.max_power": "Maxeffekt",
        "race_type.watts": "Effekt",
        "result.error_title": "Resultatet hittades inte",
        "result.back_button": "Tillbaka till instrumentpanelen",
        "result.team_label": "Lag",
        "result.station_label": "Station",
        "results.page_title": "Resultat",
        "results.meta_athletes": "Idrottare",
        "results.empty_title": "Inga tävlingar än",
    },
}


def test_new_keys_exist_in_every_locale():
    for locale in ("zh-TW", "en-US", "de-CH", "fr", "it", "sv"):
        messages = _load_locale(locale)
        for key in NEW_LOCALE_KEYS:
            assert key in messages, f"{key} missing from {locale}.json"


def test_new_keys_hold_the_expected_translation_in_every_locale():
    for locale, expected in EXPECTED_VALUES.items():
        messages = _load_locale(locale)
        for key, value in expected.items():
            assert (
                messages[key] == value
            ), f"{locale}.json[{key!r}] = {messages[key]!r}, expected {value!r}"


def test_new_keys_zh_tw_values_are_genuinely_chinese():
    zh = _load_locale("zh-TW")
    for key in NEW_LOCALE_KEYS:
        value = zh[key]
        assert _CJK_RE.search(
            value
        ), f"{key} zh-TW value has no CJK character: {value!r}"


def test_race_type_keys_are_a_single_shared_source_not_a_third_copy():
    """The task this module covers exists because distance/time/calories/
    max_power/watts wording was hardcoded three separate times (two tables
    in results.html, one in result.html). Pin that neither page still
    carries a standalone raceTypeLabels-style literal table -- the only
    place these words may appear as bare string literals in the inline
    <script> is inside the race_type.* locale JSON files themselves."""
    for page in ("result.html", "results.html"):
        script = _stripped_script(page)
        assert (
            "raceTypeLabels" not in script
        ), f"{page} still has a raceTypeLabels table"
        assert '"Distance Race"' not in script
        assert '"Max Power"' not in script
        assert '"最大功率"' not in script
