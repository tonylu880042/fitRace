"""Mixed-race ("多器材分組") rendering on the two athlete-facing results
pages: hub_server/static/results.html (the venue results wall) and
hub_server/static/result.html (the single-athlete QR-code page).

Mirrors the brace-depth extraction technique used by
tests/unit/hub/test_result_pages_i18n.py and
tests/unit/hub/test_result_pages_anonymous_display.py so a failure here
means the real page source stopped grouping/labelling mixed-race results
correctly, not that a description of it changed. As those modules note,
naive brace matching runs over the RAW (un-stripped) script, so any
comment this module's helpers sit near must never contain a stray
apostrophe or an unbalanced brace -- see hub_server/static/result.html and
results.html for the existing convention this follows.

Non-mixed rendering is exercised elsewhere (test_result_pages_i18n.py,
test_result_pages_anonymous_display.py, test_result_pages_number_format.py)
and must stay byte-for-byte unaffected by anything added here.
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


def _run(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout


# -- 1. getRaceTypeLabel("mixed") on both pages resolves race_type.mixed --


def test_get_race_type_label_mixed_routes_through_t_on_both_pages():
    for page in ("result.html", "results.html"):
        source = _stripped_script(page)
        fn = _extract_function(source, "getRaceTypeLabel")
        script = _t_stub() + fn + '\nconsole.log(getRaceTypeLabel("mixed"));'
        assert _run(script).strip() == "T[race_type.mixed]"


# -- 2. getEquipmentTypeLabel resolves equipment_type.<type>, falls back to
#    the raw type on a locale miss.


def _run_get_equipment_type_label(page: str, type_js: str) -> str:
    source = _stripped_script(page)
    fn = _extract_function(source, "getEquipmentTypeLabel")
    script = _t_stub() + fn + f"\nconsole.log(getEquipmentTypeLabel({type_js}));"
    return _run(script).strip()


def test_get_equipment_type_label_routes_through_t():
    for page in ("result.html", "results.html"):
        out = _run_get_equipment_type_label(page, '"rowing_machine"')
        assert out == "T[equipment_type.rowing_machine]"


def test_get_equipment_type_label_falls_back_to_raw_type_on_miss():
    source = _stripped_script("results.html")
    fn = _extract_function(source, "getEquipmentTypeLabel")
    script = (
        "const t = (key) => key;\n"
        + fn
        + '\nconsole.log(getEquipmentTypeLabel("some_new_machine"));'
    )
    assert _run(script).strip() == "some_new_machine"


# -- 3. results.html groupHeadingText joins equipment/race-type/category --


def test_group_heading_text_joins_equipment_race_type_and_category_label():
    source = _stripped_script("results.html")
    equip_fn = _extract_function(source, "getEquipmentTypeLabel")
    race_fn = _extract_function(source, "getRaceTypeLabel")
    heading_fn = _extract_function(source, "groupHeadingText")
    group = {
        "equipment_types": ["rowing_machine"],
        "race_type": "time",
        "label": "2 min",
    }
    script = (
        "const t = (key) => key.startsWith('equipment_type.') ? "
        "{'equipment_type.rowing_machine': 'Rowing Machine'}[key] : "
        "{'race_type.time': 'Time'}[key] || key;\n"
        + equip_fn
        + "\n"
        + race_fn
        + "\n"
        + heading_fn
        + "\n"
        + f"console.log(groupHeadingText({json.dumps(group)}));"
    )
    assert _run(script).strip() == "Rowing Machine · Time · 2 min"


def test_group_heading_text_joins_multiple_equipment_types_with_slash():
    source = _stripped_script("results.html")
    equip_fn = _extract_function(source, "getEquipmentTypeLabel")
    race_fn = _extract_function(source, "getRaceTypeLabel")
    heading_fn = _extract_function(source, "groupHeadingText")
    group = {
        "equipment_types": ["treadmill", "curved_treadmill"],
        "race_type": "distance",
        "label": "500 m",
    }
    script = (
        "const t = (key) => key;\n"
        + equip_fn
        + "\n"
        + race_fn
        + "\n"
        + heading_fn
        + "\n"
        + f"console.log(groupHeadingText({json.dumps(group)}));"
    )
    out = _run(script).strip()
    assert "treadmill/curved_treadmill" in out


# -- 4. results.html rankDisplay: "#3" or the em-dash placeholder for null --


def _run_rank_display(rank_js: str, lang_js: str = '"en-US"') -> str:
    source = _stripped_script("results.html")
    fn = _extract_function(source, "rankDisplay")
    script = fn + f"\nconsole.log(rankDisplay({rank_js}, {lang_js}));"
    return _run(script).strip()


def test_rank_display_formats_a_real_rank():
    assert _run_rank_display("3") == "#3"


def test_rank_display_shows_placeholder_for_null_rank():
    assert _run_rank_display("null") == "—"


def test_rank_display_shows_placeholder_for_undefined_rank():
    assert _run_rank_display("undefined") == "—"


# -- 5. results.html buildGroupSections partitions athletes by group_index,
#    preserving arrival order, with ungrouped rows collected last.


def _run_build_group_sections(race_js: str, athletes_js: str) -> list:
    source = _stripped_script("results.html")
    fn = _extract_function(source, "buildGroupSections")
    script = (
        fn
        + f"\nconst sections = buildGroupSections({race_js}, {athletes_js});\n"
        + "console.log(JSON.stringify(sections.map((s) => ({"
        + "group_index: s.group ? s.group.group_index : null,"
        + "names: s.athletes.map((a) => a.athlete_name)"
        + "}))));"
    )
    return json.loads(_run(script))


def test_build_group_sections_orders_by_group_index_then_ungrouped_last():
    race_js = json.dumps(
        {
            "groups": [
                {"group_index": 0, "race_type": "distance", "label": "500 m"},
                {"group_index": 1, "race_type": "time", "label": "2 min"},
            ]
        }
    )
    athletes_js = json.dumps(
        [
            {"athlete_name": "Alice", "group_index": 0},
            {"athlete_name": "Dan", "group_index": 1},
            {"athlete_name": "Erin", "group_index": None},
            {"athlete_name": "Bob", "group_index": 0},
        ]
    )
    sections = _run_build_group_sections(race_js, athletes_js)

    assert [s["group_index"] for s in sections] == [0, 1, None]
    assert sections[0]["names"] == ["Alice", "Bob"]
    assert sections[1]["names"] == ["Dan"]
    assert sections[2]["names"] == ["Erin"]


def test_build_group_sections_omits_ungrouped_section_when_none_present():
    race_js = json.dumps(
        {"groups": [{"group_index": 0, "race_type": "distance", "label": "500 m"}]}
    )
    athletes_js = json.dumps([{"athlete_name": "Alice", "group_index": 0}])
    sections = _run_build_group_sections(race_js, athletes_js)

    assert len(sections) == 1
    assert sections[0]["group_index"] == 0


def test_build_group_sections_group_with_no_rows_still_renders_empty():
    race_js = json.dumps(
        {
            "groups": [
                {"group_index": 0, "race_type": "distance", "label": "500 m"},
                {"group_index": 1, "race_type": "time", "label": "2 min"},
            ]
        }
    )
    athletes_js = json.dumps([{"athlete_name": "Dan", "group_index": 1}])
    sections = _run_build_group_sections(race_js, athletes_js)

    assert [s["group_index"] for s in sections] == [0, 1]
    assert sections[0]["names"] == []
    assert sections[1]["names"] == ["Dan"]


# -- 6. result.html groupEquipmentText / getEquipmentTypeLabel wiring -----


def test_group_equipment_text_joins_equipment_names():
    source = _stripped_script("result.html")
    equip_fn = _extract_function(source, "getEquipmentTypeLabel")
    text_fn = _extract_function(source, "groupEquipmentText")
    group = {"equipment_types": ["rowing_machine"]}
    script = (
        "const t = (key) => "
        "({'equipment_type.rowing_machine': 'Rowing Machine'}[key] || key);\n"
        + equip_fn
        + "\n"
        + text_fn
        + "\n"
        + f"console.log(groupEquipmentText({json.dumps(group)}));"
    )
    assert _run(script).strip() == "Rowing Machine"


def test_group_equipment_text_empty_for_no_group():
    source = _stripped_script("result.html")
    equip_fn = _extract_function(source, "getEquipmentTypeLabel")
    text_fn = _extract_function(source, "groupEquipmentText")
    script = (
        "const t = (key) => key;\n"
        + equip_fn
        + "\n"
        + text_fn
        + "\nconsole.log(JSON.stringify(groupEquipmentText(null)));"
    )
    assert json.loads(_run(script)) == ""


# -- 7. result.html renderResult wires data.group into the rendered page:
#    race-type text uses the group race_type, rank shows "n / group size"
#    or the em-dash placeholder for an ungrouped row, and the equipment
#    header row is filled in only when a group is present. Uses the same
#    stub set as test_result_pages_anonymous_display.py's
#    _run_render_result_athlete_name, plus t()/getEquipmentTypeLabel/
#    groupEquipmentText for the new group-aware behaviour this covers.


def _run_render_result(data_js: str) -> dict:
    source = _read("result.html")
    display_name_fn = _strip_js_comments(
        _extract_function(source, "athleteDisplayName")
    )
    equip_fn = _strip_js_comments(_extract_function(source, "getEquipmentTypeLabel"))
    group_equip_fn = _strip_js_comments(_extract_function(source, "groupEquipmentText"))
    render_fn = _strip_js_comments(_extract_function(source, "renderResult"))

    script = (
        "const t = (key) => "
        "({'equipment_type.rowing_machine': 'Rowing Machine'}[key] || key);\n"
        + display_name_fn
        + "\n"
        + equip_fn
        + "\n"
        + group_equip_fn
        + "\n"
        + "function escapeHtml(str) { return String(str || ''); }\n"
        + "class FakeEl {\n"
        "  constructor() { this.textContent = ''; this.innerHTML = ''; this.style = {}; this.classList = { _set: new Set(), add(c){this._set.add(c);}, remove(c){this._set.delete(c);} }; this.children = []; }\n"
        "  appendChild(child) { this.children.push(child); }\n"
        "}\n"
        + "const elements = {};\n"
        + "function el(id) { if (!elements[id]) elements[id] = new FakeEl(); return elements[id]; }\n"
        + "const document = {\n"
        "  documentElement: { dataset: { lang: 'en-US' } },\n"
        "  getElementById: (id) => el(id),\n"
        "};\n"
        + "const window = {};\n"
        + "function getRaceTypeLabel(raceType) { return `label:${raceType}`; }\n"
        + "function formatDate() { return ''; }\n"
        + "function formatNumber(value) { return String(value); }\n"
        + "function formatTime() { return ''; }\n"
        + "function createMetric() { return new FakeEl(); }\n"
        + render_fn
        + f"\nconst data = {data_js};\n"
        + "renderResult(data);\n"
        + "console.log(JSON.stringify({\n"
        "  rank: el('rank').textContent,\n"
        "  raceType: el('race-type').textContent,\n"
        "  equipment: el('group-equipment').textContent,\n"
        "  equipmentVisible: el('group-equipment').classList._set.has('visible')\n"
        "}));"
    )
    return json.loads(_run(script))


def test_render_result_grouped_row_shows_rank_over_group_size_and_group_race_type():
    data_js = json.dumps(
        {
            "athlete": {"athlete_name": "Dan", "rank": 1, "station_number": 4},
            "race": {"race_type": "mixed"},
            "total_athletes": 2,
            "group": {
                "group_index": 1,
                "race_type": "time",
                "equipment_types": ["rowing_machine"],
                "label": "2 min",
            },
        }
    )
    out = _run_render_result(data_js)
    assert out["rank"] == "1 / 2"
    assert out["raceType"] == "label:time"
    assert out["equipment"] == "Rowing Machine"
    assert out["equipmentVisible"] is True


def test_render_result_ungrouped_row_shows_placeholder_rank_and_mixed_race_type():
    data_js = json.dumps(
        {
            "athlete": {"athlete_name": "Erin", "rank": None, "station_number": 5},
            "race": {"race_type": "mixed"},
            "total_athletes": None,
            "group": None,
        }
    )
    out = _run_render_result(data_js)
    assert out["rank"] == "—"
    assert out["raceType"] == "label:mixed"
    assert out["equipment"] == ""
    assert out["equipmentVisible"] is False


def test_render_result_non_mixed_row_unaffected_by_group_wiring():
    data_js = json.dumps(
        {
            "athlete": {"athlete_name": "Zed", "rank": 2, "station_number": 1},
            "race": {"race_type": "distance"},
            "total_athletes": 5,
        }
    )
    out = _run_render_result(data_js)
    assert out["rank"] == "2 / 5"
    assert out["raceType"] == "label:distance"
    assert out["equipment"] == ""
    assert out["equipmentVisible"] is False


# -- 8. Locale symmetry / value pins for the new keys ----------------------

NEW_KEYS = [
    "race_type.mixed",
    "equipment_type.treadmill",
    "equipment_type.curved_treadmill",
    "equipment_type.spin_bike",
    "equipment_type.fan_bike",
    "equipment_type.upright_bike",
    "equipment_type.recumbent_bike",
    "equipment_type.elliptical",
    "equipment_type.stair_climber",
    "equipment_type.rowing_machine",
    "equipment_type.ski_erg",
]

_CJK_RE = re.compile(r"[一-鿿]")


def test_new_keys_exist_in_every_locale():
    for locale in ("zh-TW", "en-US", "de-CH", "fr", "it", "sv"):
        messages = _load_locale(locale)
        for key in NEW_KEYS:
            assert key in messages, f"{key} missing from {locale}.json"


def test_race_type_mixed_and_zh_tw_equipment_values_are_genuinely_chinese():
    zh = _load_locale("zh-TW")
    for key in NEW_KEYS:
        assert _CJK_RE.search(zh[key]), f"{key} zh-TW value has no CJK character"


def test_en_us_key_values_pinned():
    en = _load_locale("en-US")
    assert en["race_type.mixed"] == "Multi-equipment"
    assert en["equipment_type.rowing_machine"] == "Rowing Machine"
    assert en["equipment_type.ski_erg"] == "Ski Erg"


def test_zh_tw_key_values_pinned():
    zh = _load_locale("zh-TW")
    assert zh["race_type.mixed"] == "多器材分組"
    assert zh["equipment_type.rowing_machine"] == "划船機"
    assert zh["equipment_type.treadmill"] == "跑步機"


def test_get_equipment_type_label_builds_the_dynamic_key_on_both_pages():
    # getEquipmentTypeLabel builds its lookup key dynamically
    # (`equipment_type.${type}`) rather than as a literal t("...") call per
    # type, so pin that the dynamic-key template is actually present.
    for page in ("result.html", "results.html"):
        assert "equipment_type.${type}" in _stripped_script(page)
