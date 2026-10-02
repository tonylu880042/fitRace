import json
from pathlib import Path

LOCALES_DIR = (
    Path(__file__).resolve().parents[3] / "hub_server" / "infrastructure" / "locales"
)

EXPECTED = {
    "zh-TW": "距離",
    "en-US": "Distance",
    "fr": "Distance",
    "it": "Distanza",
    "de-CH": "Distanz",
    "sv": "Distans",
}


def test_idle_distance_label_exists_in_every_locale():
    for locale, expected in EXPECTED.items():
        data = json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))
        assert data.get("idle.metric.distance") == expected, locale
