"""Lookup precedence of the real t() on the pages that moved onto the locale
API: the page-prefixed key (`game_admin.`, `system_admin.`, `signup_page.`)
must win over a global key of the same name. The real signup.division
collision (global "Division" vs the page's "Division (Optional)") is the
live case; the synthetic cases guard the order for every page even where no
colliding key differs today.
"""

import json

import pytest

from page_locale import (
    extract_function,
    matching_end,
    messages_js,
    page_dictionaries,
    raw_messages,
    run_page_functions,
    script_of,
    strip_js_comments,
)

PAGES = [
    ("signup.html", "signup_page.", ["interpolate", "t"]),
    ("gameAdmin.html", "game_admin.", ["t"]),
    ("systemAdmin.html", "system_admin.", ["t"]),
]


def _fallback_const(page: str) -> str:
    """Signup's built-in English map (other pages have none)."""
    if page != "signup.html":
        return ""
    script = strip_js_comments(script_of(page))
    start = script.index("const FALLBACK_MESSAGES")
    brace = script.index("{", start)
    return script[start : matching_end(script, brace, "{", "}") + 1] + ";\n"


def _t(page: str, functions: list[str], prelude: str, calls: list[str]):
    return run_page_functions(
        page,
        functions,
        _fallback_const(page) + prelude,
        f"console.log(JSON.stringify({json.dumps(calls)}.map((k) => t(k))));",
    )


@pytest.mark.parametrize("page,prefix,functions", PAGES)
def test_page_prefixed_key_beats_a_global_key_of_the_same_name(page, prefix, functions):
    messages = {f"{prefix}x.key": "PAGE", "x.key": "GLOBAL", "y.key": "GLOBAL-ONLY"}
    out = _t(
        page, functions, f"let messages = {json.dumps(messages)};", ["x.key", "y.key"]
    )
    assert out == ["PAGE", "GLOBAL-ONLY"]


def test_signup_prefixed_key_beats_the_built_in_english_and_that_beats_a_global_key():
    out = _t(
        "signup.html",
        ["interpolate", "t"],
        """let messages = {
  "signup_page.signup.error": "PAGE",
  "signup.connection_error": "GLOBAL",
  "signup.station_unconfigured": "GLOBAL",
};""",
        ["signup.error", "signup.connection_error", "signup.station_unconfigured"],
    )
    built_in = raw_messages("en-US")
    assert out[0] == "PAGE"
    assert out[1] == built_in["signup_page.signup.connection_error"]
    assert out[2] == built_in["signup_page.signup.station_unconfigured"]


@pytest.mark.parametrize("locale", ["en-US", "zh-TW", "zh-CN"])
def test_signup_division_resolves_to_the_page_text_not_the_global_one(locale):
    messages = raw_messages(locale)
    page_text = messages["signup_page.signup.division"]
    assert page_text != messages["signup.division"], "collision no longer differs"
    out = _t(
        "signup.html",
        ["interpolate", "t"],
        messages_js(locale),
        ["signup.division", "signup.division_none"],
    )
    assert out[0] == page_text
    assert out[1] == messages["signup_page.signup.division_none"]


@pytest.mark.parametrize("page,prefix,functions", PAGES)
@pytest.mark.parametrize("locale", ["en-US", "zh-TW", "zh-CN"])
def test_every_real_colliding_key_resolves_to_the_page_text(
    page, prefix, functions, locale
):
    messages = raw_messages(locale)
    colliding = sorted(
        key for key in page_dictionaries(prefix)[locale] if key in messages
    )
    if not colliding:
        return  # e.g. System Admin: no page key shares a name with a global one
    out = _t(page, functions, messages_js(locale), colliding)
    assert out == [messages[f"{prefix}{key}"] for key in colliding]


def test_the_pages_t_functions_are_the_ones_under_test():
    for page, prefix, _ in PAGES:
        assert prefix in extract_function(script_of(page), "t")
