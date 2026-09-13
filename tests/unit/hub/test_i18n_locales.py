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
