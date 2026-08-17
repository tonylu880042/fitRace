"""Class Admin is reachable from the other two admin consoles.

/classAdmin shipped with nothing linking to it: gameAdmin.html and
systemAdmin.html's header navs listed Dashboard/System-or-Game-Admin/Signup/
etc but never classAdmin, so the only way to reach the class console was to
type the URL by hand. This module pins that both pages now carry a nav
entry pointing at /classAdmin, wired through a real i18n key (not hardcoded
English), and that the key holds a genuinely different (not just spread-
copied) Chinese value in each page's inline zh-TW dictionary.

Mirrors the brace-matched dictionary extraction technique already used by
tests/unit/hub/test_static_page_i18n.py (which this module deliberately
does not edit) so a mis-nested edit to either dictionary object literal
fails loudly here too, not just silently in that file.
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

PAGES = ("gameAdmin.html", "systemAdmin.html")

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _read(page: str) -> str:
    """Strip HTML comments before returning the source. A nav button
    commented out of the DOM (`<!-- <button ...>Class Admin</button> -->`)
    is present in source but never renders -- exactly the "control exists
    but isn't reachable" bug this whole task is about -- so a bare
    substring search must not be satisfied by a comment containing it."""
    raw = (STATIC_DIR / page).read_text(encoding="utf-8")
    return _HTML_COMMENT_RE.sub("", raw)


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


def _extract_dictionaries_js(source: str) -> str:
    const_start = source.index("const dictionaries = {")
    const_open = source.index("{", const_start)
    const_close = _matching_brace_end(source, const_open)

    zh_marker = 'dictionaries["zh-TW"] = {'
    zh_start = source.index(zh_marker, const_close)
    zh_open = source.index("{", zh_start)
    zh_close = _matching_brace_end(source, zh_open)

    return source[const_start : zh_close + 1] + ";"


def _load_dictionaries(source: str) -> dict:
    js = _extract_dictionaries_js(source)
    js += "\nconsole.log(JSON.stringify(dictionaries));"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(js)
        tmp_file.flush()
        tmp_path = tmp_file.name
    try:
        result = subprocess.run(
            ["node", tmp_path], capture_output=True, text=True, timeout=5
        )
        assert (
            result.returncode == 0
        ), f"node failed to evaluate dictionaries: {result.stderr}"
        return json.loads(result.stdout)
    finally:
        Path(tmp_path).unlink()


_NAV_BUTTON_RE = re.compile(
    r"<button[^>]*onclick=\"window\.location\.href='/classAdmin'\"[^>]*>.*?</button>",
    re.DOTALL,
)


def test_both_admin_pages_have_a_nav_entry_pointing_at_class_admin():
    for page in PAGES:
        source = _read(page)
        match = _NAV_BUTTON_RE.search(source)
        assert match, f"{page}: no nav button targets /classAdmin"


def test_both_admin_pages_nav_entry_uses_i18n_not_hardcoded_english():
    for page in PAGES:
        source = _read(page)
        match = _NAV_BUTTON_RE.search(source)
        assert match, f"{page}: no nav button targets /classAdmin"
        tag = match.group(0)
        assert (
            'data-i18n="nav.class_admin"' in tag
        ), f'{page}: /classAdmin nav button is missing data-i18n="nav.class_admin"'


def test_new_key_exists_in_both_dictionaries_of_both_pages():
    for page in PAGES:
        source = _read(page)
        dictionaries = _load_dictionaries(source)
        assert "nav.class_admin" in dictionaries["en-US"], f"{page}: missing from en-US"
        assert "nav.class_admin" in dictionaries["zh-TW"], f"{page}: missing from zh-TW"


def test_new_key_zh_tw_value_differs_from_en_us_and_is_genuinely_chinese():
    cjk_re = re.compile(r"[一-鿿]")
    for page in PAGES:
        source = _read(page)
        dictionaries = _load_dictionaries(source)
        en_value = dictionaries["en-US"]["nav.class_admin"]
        zh_value = dictionaries["zh-TW"]["nav.class_admin"]
        assert zh_value != en_value, (
            f"{page}: nav.class_admin zh-TW value is identical to en-US "
            "(missing translation)"
        )
        assert cjk_re.search(
            zh_value
        ), f"{page}: zh-TW value has no CJK character: {zh_value!r}"


def test_class_admin_html_nav_is_left_untouched():
    # classAdmin.html already links out to Dashboard / Game Admin / System
    # Admin -- this task explicitly does not change that page.
    source = (STATIC_DIR / "classAdmin.html").read_text(encoding="utf-8")
    assert "classAdmin.nav_dashboard" in source
    assert "classAdmin.nav_game_admin" in source
    assert "classAdmin.nav_system_admin" in source
