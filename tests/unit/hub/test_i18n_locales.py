import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from hub_server.infrastructure import locales as locales_module
from hub_server.infrastructure.fastapi.app import app
from hub_server.infrastructure.locales import (
    SUPPORTED_LOCALES,
    list_locales,
    load_locale,
    normalize_locale,
)

LOCALE_DIR = (
    Path(__file__).resolve().parents[3] / "hub_server" / "infrastructure" / "locales"
)

# The complete locales: they must carry exactly the same key set. The other
# locales (it, fr, de-CH, sv) may be missing keys; load_locale() fills the gap
# with the English text.
COMPLETE_LOCALES = ("en-US", "zh-TW", "zh-CN")

# Characters that exist only in Traditional Chinese. None may survive in the
# zh-CN file. (The spec's list also had 幕, but that is a valid Simplified
# character -- 屏幕, 字幕 -- so 說 stands in for it.)
TRADITIONAL_ONLY_CHARS = "體設備隊賽號開終選與為這們時點機網線經進發長動應該電會區數據讀認證錄單門間問題頁顯標準說"

_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z0-9_]+\}")


def _raw(locale: str) -> dict:
    with open(LOCALE_DIR / f"{locale}.json", "r", encoding="utf-8") as file:
        return json.load(file)


def test_all_supported_locales_have_matching_keys():
    base_keys = set(_raw("en-US").keys())

    for locale in COMPLETE_LOCALES:
        assert set(_raw(locale).keys()) == base_keys, locale


def test_partial_locales_never_have_a_key_en_us_lacks():
    base_keys = set(_raw("en-US").keys())

    for locale in SUPPORTED_LOCALES:
        extra = set(_raw(locale).keys()) - base_keys
        assert not extra, f"{locale} has keys en-US lacks: {extra}"


def test_every_supported_locale_serves_the_full_en_us_key_set():
    base_keys = set(_raw("en-US").keys())

    for locale in SUPPORTED_LOCALES:
        assert set(load_locale(locale)["messages"].keys()) == base_keys, locale


def test_missing_key_falls_back_to_en_us_text(tmp_path, monkeypatch):
    (tmp_path / "en-US.json").write_text(
        json.dumps({"a": "Alpha", "b": "Bravo"}), encoding="utf-8"
    )
    (tmp_path / "sv.json").write_text(json.dumps({"a": "Alfa"}), encoding="utf-8")
    monkeypatch.setattr(locales_module, "_LOCALE_DIR", tmp_path)
    load_locale.cache_clear()
    try:
        messages = load_locale("sv")["messages"]
    finally:
        load_locale.cache_clear()

    assert messages == {"a": "Alfa", "b": "Bravo"}


def test_zh_cn_is_a_supported_locale_with_a_simplified_label():
    assert "zh-CN" in SUPPORTED_LOCALES
    labels = {entry["code"]: entry["label"] for entry in list_locales()}
    assert labels["zh-CN"] == "简体中文"
    assert labels["zh-TW"] == "繁體中文"


def test_chinese_locale_aliases():
    expected = {
        "zh-cn": "zh-CN",
        "zh-CN": "zh-CN",
        "zh-hans": "zh-CN",
        "zh-Hans": "zh-CN",
        "zh-sg": "zh-CN",
        "zh-hans-cn": "zh-CN",
        "zh": "zh-TW",
        "zh-tw": "zh-TW",
        "zh-hk": "zh-TW",
        "zh-hant": "zh-TW",
        "zh-Hant": "zh-TW",
    }
    for alias, locale in expected.items():
        assert normalize_locale(alias) == locale, alias


def test_zh_cn_messages_use_the_same_placeholders_as_en_us():
    en = _raw("en-US")
    for key, value in _raw("zh-CN").items():
        assert sorted(_PLACEHOLDER_RE.findall(value)) == sorted(
            _PLACEHOLDER_RE.findall(en[key])
        ), key


def test_zh_cn_messages_contain_no_traditional_only_characters():
    offenders = {
        key: value
        for key, value in _raw("zh-CN").items()
        if any(char in TRADITIONAL_ONLY_CHARS for char in value)
    }
    assert not offenders, offenders


def test_zh_cn_mainland_terms():
    messages = _raw("zh-CN")
    assert messages["signup.division_men"] == "男子组"
    assert messages["signup.division_women"] == "女子组"
    assert messages["stage.relay"] == "接力"
    assert messages["export.header_rank"] == "名次"


def test_locales_api_serves_zh_cn():
    client = TestClient(app)
    listing = client.get("/api/locales").json()
    assert {"code": "zh-CN", "label": "简体中文"} in listing["locales"]
    payload = client.get("/api/locales/zh-CN").json()
    assert payload["locale"] == "zh-CN"
    assert payload["messages"]["signup.division_men"] == "男子组"
    assert client.get("/api/locales/zh-Hans").json()["locale"] == "zh-CN"


def test_division_keys_present_in_every_locale():
    division_keys = {
        "signup.division",
        "signup.division_none",
        "signup.division_men",
        "signup.division_women",
        "record_wall.division_men",
        "record_wall.division_women",
    }
    for locale in SUPPORTED_LOCALES:
        messages = load_locale(locale)["messages"]
        missing = division_keys - set(messages.keys())
        assert not missing, f"{locale} is missing {missing}"


def test_zh_tw_division_translations():
    messages = load_locale("zh-TW")["messages"]
    assert messages["signup.division_none"] == "不分組"
    assert messages["signup.division_men"] == "男子組"
    assert messages["signup.division_women"] == "女子組"


def test_relay_legs_record_wall_key_present_in_every_locale():
    for locale in SUPPORTED_LOCALES:
        messages = load_locale(locale)["messages"]
        assert "record_wall.relay_legs" in messages
        assert "{legs}" in messages["record_wall.relay_legs"]


def test_zh_tw_relay_legs_translation():
    messages = load_locale("zh-TW")["messages"]
    assert messages["record_wall.relay_legs"] == "接力 {legs} 棒"


def test_dashboard_relay_keys_present_in_every_locale():
    for locale in SUPPORTED_LOCALES:
        messages = load_locale(locale)["messages"]
        assert "{leg}" in messages["dashboard.relay_leg"]
        assert "{legs}" in messages["dashboard.relay_leg"]
        assert "{runner}" in messages["dashboard.relay_handoff"]


def test_zh_tw_dashboard_relay_translations():
    messages = load_locale("zh-TW")["messages"]
    assert messages["dashboard.relay_leg"] == "第 {leg}/{legs} 棒"
    assert messages["dashboard.relay_handoff"] == "換棒！下一棒：{runner}"


def test_stage_relay_key_present_in_every_locale():
    for locale in SUPPORTED_LOCALES:
        messages = load_locale(locale)["messages"]
        assert "stage.relay" in messages, f"{locale} is missing stage.relay"


def test_stage_relay_translations_zh_tw_and_en_us():
    assert load_locale("zh-TW")["messages"]["stage.relay"] == "接力"
    assert load_locale("en-US")["messages"]["stage.relay"] == "Relay"


def test_export_header_keys_present_in_every_locale():
    header_keys = {
        "export.header_race_start",
        "export.header_race_type",
        "export.header_target",
        "export.header_mode",
        "export.header_rank",
        "export.header_name",
        "export.header_division",
        "export.header_team",
        "export.header_station",
        "export.header_time_sec",
        "export.header_status",
        "export.header_distance_m",
        "export.header_calories",
        "export.header_max_power_w",
        "export.header_relay_members",
        "export.header_relay_splits",
    }
    for locale in SUPPORTED_LOCALES:
        messages = load_locale(locale)["messages"]
        missing = header_keys - set(messages.keys())
        assert not missing, f"{locale} is missing {missing}"


def test_zh_tw_export_header_translations():
    messages = load_locale("zh-TW")["messages"]
    assert messages["export.header_race_start"] == "比賽開始"
    assert messages["export.header_rank"] == "名次"
    assert messages["export.header_relay_splits"] == "接力分段時間（秒）"


def test_locale_json_files_are_valid():
    locale_dir = (
        Path(__file__).resolve().parents[3]
        / "hub_server"
        / "infrastructure"
        / "locales"
    )

    for locale in SUPPORTED_LOCALES:
        with open(locale_dir / f"{locale}.json", "r", encoding="utf-8") as file:
            assert isinstance(json.load(file), dict)
