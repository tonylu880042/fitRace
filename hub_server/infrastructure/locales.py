import json
from functools import lru_cache
from pathlib import Path


SUPPORTED_LOCALES = ["en-US", "zh-TW", "it", "fr", "de-CH", "sv"]
DEFAULT_LOCALE = "en-US"

_ALIASES = {
    "en": "en-US",
    "en-us": "en-US",
    "zh": "zh-TW",
    "zh-tw": "zh-TW",
    "it-it": "it",
    "fr-fr": "fr",
    "de": "de-CH",
    "de-ch": "de-CH",
    "sv-se": "sv",
}


def normalize_locale(locale: str | None) -> str:
    if not locale:
        return DEFAULT_LOCALE
    value = locale.strip()
    if value in SUPPORTED_LOCALES:
        return value
    return _ALIASES.get(value.lower(), DEFAULT_LOCALE)


@lru_cache
def load_locale(locale: str) -> dict:
    normalized = normalize_locale(locale)
    path = Path(__file__).resolve().parent / "locales" / f"{normalized}.json"
    with open(path, "r", encoding="utf-8") as file:
        messages = json.load(file)
    return {"locale": normalized, "messages": messages}


def list_locales() -> list[dict]:
    labels = {
        "en-US": "English",
        "zh-TW": "繁體中文",
        "it": "Italiano",
        "fr": "Français",
        "de-CH": "Deutsch (Schweiz)",
        "sv": "Svenska",
    }
    return [{"code": code, "label": labels[code]} for code in SUPPORTED_LOCALES]
