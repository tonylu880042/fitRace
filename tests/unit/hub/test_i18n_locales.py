import json
from pathlib import Path

from hub_server.infrastructure.locales import DEFAULT_LOCALE, SUPPORTED_LOCALES, load_locale


def test_all_supported_locales_have_matching_keys():
    base_keys = set(load_locale(DEFAULT_LOCALE)["messages"].keys())

    for locale in SUPPORTED_LOCALES:
        messages = load_locale(locale)["messages"]
        assert set(messages.keys()) == base_keys


def test_locale_json_files_are_valid():
    locale_dir = Path(__file__).resolve().parents[3] / "hub_server" / "infrastructure" / "locales"

    for locale in SUPPORTED_LOCALES:
        with open(locale_dir / f"{locale}.json", "r", encoding="utf-8") as file:
            assert isinstance(json.load(file), dict)
