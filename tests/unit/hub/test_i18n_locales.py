import json
from pathlib import Path

from hub_server.infrastructure.locales import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    load_locale,
)


def test_all_supported_locales_have_matching_keys():
    base_keys = set(load_locale(DEFAULT_LOCALE)["messages"].keys())

    for locale in SUPPORTED_LOCALES:
        messages = load_locale(locale)["messages"]
        assert set(messages.keys()) == base_keys


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
