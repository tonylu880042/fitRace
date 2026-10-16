import json
from functools import lru_cache
from pathlib import Path

SUPPORTED_LOCALES = ["en-US", "zh-TW", "zh-CN", "it", "fr", "de-CH", "sv"]
DEFAULT_LOCALE = "en-US"

_LOCALE_DIR = Path(__file__).resolve().parent / "locales"

_ALIASES = {
    "en": "en-US",
    "en-us": "en-US",
    "zh": "zh-TW",
    "zh-tw": "zh-TW",
    "zh-hk": "zh-TW",
    "zh-hant": "zh-TW",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-sg": "zh-CN",
    "zh-hans-cn": "zh-CN",
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


def _read_messages(locale: str) -> dict:
    with open(_LOCALE_DIR / f"{locale}.json", "r", encoding="utf-8") as file:
        return json.load(file)


@lru_cache
def load_locale(locale: str) -> dict:
    """Messages for `locale`. Only en-US, zh-TW and zh-CN are required to be
    complete; every other locale is served on top of en-US so a key it has
    not translated yet shows English instead of the raw key."""
    normalized = normalize_locale(locale)
    messages = _read_messages(DEFAULT_LOCALE)
    if normalized != DEFAULT_LOCALE:
        messages = {**messages, **_read_messages(normalized)}
    return {"locale": normalized, "messages": messages}


def list_locales() -> list[dict]:
    labels = {
        "en-US": "English",
        "zh-TW": "繁體中文",
        "zh-CN": "简体中文",
        "it": "Italiano",
        "fr": "Français",
        "de-CH": "Deutsch (Schweiz)",
        "sv": "Svenska",
    }
    return [{"code": code, "label": labels[code]} for code in SUPPORTED_LOCALES]
