"""Shared helpers for tests of hub pages that read their text from the
locale files (hub_server/infrastructure/locales/*.json) via /api/locales.

Before the zh-CN work (docs/zh_cn_i18n_spec.md) gameAdmin / systemAdmin /
signup / docs carried inline dictionaries that tests evaluated under node.
Those strings now live in the locale JSON under a per-page prefix
(`game_admin.`, `system_admin.`, `signup_page.`, `docs.`); these helpers give
tests the same view of them, plus the usual "run real page JS under node"
plumbing.
"""

import json
import re
import subprocess
from pathlib import Path

from hub_server.infrastructure.locales import load_locale

REPO_ROOT = Path(__file__).resolve().parents[3]
STATIC_DIR = REPO_ROOT / "hub_server" / "static"
LOCALE_DIR = REPO_ROOT / "hub_server" / "infrastructure" / "locales"

COMPLETE_LOCALES = ("en-US", "zh-TW", "zh-CN")

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
CJK_RE = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")


def raw_messages(locale: str) -> dict:
    """The locale file itself (no fallback merge)."""
    return json.loads((LOCALE_DIR / f"{locale}.json").read_text(encoding="utf-8"))


def served_messages(locale: str) -> dict:
    """What GET /api/locales/{locale} returns (en-US merged underneath)."""
    return load_locale(locale)["messages"]


def page_dictionaries(prefix: str) -> dict:
    """{locale: {key-without-prefix: text}} for one page's strings, in the
    shape the old inline `dictionaries` object had."""
    return {
        locale: {
            key[len(prefix) :]: value
            for key, value in raw_messages(locale).items()
            if key.startswith(prefix)
        }
        for locale in COMPLETE_LOCALES
    }


def dictionary_blocks(prefix: str, zh_locale: str = "zh-TW") -> tuple[str, str]:
    """(en-US block, zh block) as `"key": "value",` lines -- the text shape
    the old inline dictionaries had, for tests whose assertions look for
    `"key":` or slice a value out of the block."""

    def render(locale: str) -> str:
        return "\n".join(
            f"{json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},"
            for key, value in page_dictionaries(prefix)[locale].items()
        )

    return render("en-US"), render(zh_locale)


def messages_js(locale: str) -> str:
    """JS declaring `messages` exactly as the page holds it after
    loadLocale(): the full served message set of one locale."""
    return f"let messages = {json.dumps(served_messages(locale), ensure_ascii=False)};"


def read_page(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def strip_js_comments(code: str) -> str:
    """Remove // and /* */ comments (string- and regex-literal-aware, so a
    URL, a "//" inside a literal or a quote inside /"/g survives). The page
    tests node --check the result to catch any mis-parse."""
    out = []
    i = 0
    in_str = None
    n = len(code)
    last_significant = ""
    while i < n:
        ch = code[i]
        if (
            not in_str
            and ch == "/"
            and not code.startswith("//", i)
            and not code.startswith("/*", i)
            and (last_significant == "" or last_significant in "(,=:[!&|?{};+-*%<>~^")
        ):
            # Regex literal: copy through the closing unescaped "/" (a "/"
            # inside [...] does not close it), then its flags.
            j = i + 1
            in_class = False
            while j < n:
                if code[j] == "\\":
                    j += 2
                    continue
                if code[j] == "[":
                    in_class = True
                elif code[j] == "]":
                    in_class = False
                elif code[j] == "/" and not in_class:
                    break
                elif code[j] == "\n":
                    break
                j += 1
            out.append(code[i : j + 1])
            i = j + 1
            last_significant = "/"
            continue
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if ch == in_str:
                in_str = None
                last_significant = '"'
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_str = ch
            out.append(ch)
            i += 1
        elif code.startswith("//", i):
            while i < n and code[i] != "\n":
                i += 1
        elif code.startswith("/*", i):
            end = code.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(ch)
            if not ch.isspace():
                last_significant = ch
            i += 1
    return "".join(out)


def strip_html_comments(html: str) -> str:
    return _HTML_COMMENT_RE.sub("", html)


def script_of(name: str) -> str:
    """The page's main inline <script> (the last one that has no src)."""
    scripts = re.findall(
        r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", read_page(name), re.DOTALL
    )
    assert scripts, f"no inline <script> in {name}"
    return max(scripts, key=len)


def matching_end(source: str, open_idx: int, open_ch: str, close_ch: str) -> int:
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
        elif char == open_ch:
            depth += 1
        elif char == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("unbalanced brackets")


def extract_function(source: str, name: str) -> str:
    """Source of `[async ]function name(...) {...}` by bracket matching."""
    start = source.index(f"function {name}(")
    if source[max(0, start - 6) : start] == "async ":
        start -= 6
    paren = source.index("(", start)
    brace = source.index("{", matching_end(source, paren, "(", ")"))
    return source[start : matching_end(source, brace, "{", "}") + 1]


def run_node(script: str):
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, f"node failed: {result.stderr}\n{script[:3000]}"
    return json.loads(result.stdout) if result.stdout.strip() else None


def run_page_functions(
    page: str, functions: list[str], prelude: str, body: str, strip_comments=True
):
    """Run real functions of a page's script under node. `prelude` is JS
    placed before them (state, stubs, `messages`), `body` after."""
    code = script_of(page)
    if strip_comments:
        code = strip_js_comments(code)
    bodies = "\n".join(extract_function(code, name) for name in functions)
    return run_node(f"{prelude}\n{bodies}\n{body}")
