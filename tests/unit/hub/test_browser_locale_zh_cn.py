"""Browser-language fallback on the locale-API pages (docs/zh_cn_i18n_spec.md
section 4): mainland/Simplified browsers get zh-CN, Taiwan/Hong Kong/bare
"zh" browsers keep zh-TW. The page's real getBrowserLocale() is executed
under node, never grepped."""

import json
import re
import subprocess
from pathlib import Path

import pytest

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

# Pages that carry their own copy of getBrowserLocale().
PAGES = [
    "index.html",
    "classAdmin.html",
    "teams.html",
    "result.html",
    "results.html",
    "gameAdmin.html",
    "systemAdmin.html",
    "signup.html",
]


def _function_source(page: str, name: str) -> str:
    source = (STATIC_DIR / page).read_text(encoding="utf-8")
    match = re.search(rf"function {name}\(", source)
    assert match, f"{page} has no {name}()"
    depth = 0
    i = source.index("{", match.start())
    start = match.start()
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1
    raise AssertionError("unbalanced braces")


def _browser_locale(page: str, language: str, saved: str | None = None) -> str:
    script = (
        f"const localStorage = {{ getItem: () => {json.dumps(saved)} }};\n"
        f"const navigator = {{ language: {json.dumps(language)} }};\n"
        + _function_source(page, "getBrowserLocale")
        + "\nconsole.log(getBrowserLocale());"
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize(
    "language", ["zh-CN", "zh-cn", "zh-Hans", "zh-Hans-CN", "zh-SG"]
)
def test_simplified_browser_languages_get_zh_cn(page, language):
    assert _browser_locale(page, language) == "zh-CN"


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("language", ["zh-TW", "zh-HK", "zh-Hant", "zh"])
def test_traditional_browser_languages_keep_zh_tw(page, language):
    assert _browser_locale(page, language) == "zh-TW"


@pytest.mark.parametrize("page", PAGES)
def test_other_browser_languages_unchanged_and_saved_choice_wins(page):
    assert _browser_locale(page, "en-GB") == "en-US"
    assert _browser_locale(page, "fr-CH") == "fr"
    assert _browser_locale(page, "zh-CN", saved="sv") == "sv"


# ---------------------------------------------------------------------------
# Anonymous-athlete fallback words on the public result pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", ["result.html", "results.html"])
def test_anonymous_athlete_fallback_is_simplified_for_zh_cn(page):
    script = (
        _function_source(page, "athleteDisplayName")
        + "\nconsole.log(JSON.stringify(["
        + 'athleteDisplayName({ station_number: 4 }, "zh-CN"),'
        + 'athleteDisplayName({}, "zh-CN"),'
        + 'athleteDisplayName({ station_number: 4 }, "zh-TW"),'
        + 'athleteDisplayName({}, "zh-TW")]));'
    )
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["站位 4", "选手", "站位 4", "選手"]
