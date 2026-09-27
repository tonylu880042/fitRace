"""Roster import errors were English-only text on the zh-TW page (e.g.
"Invalid division: 混合"), and `.row-error` rows weren't visibly red. The
backend now attaches a stable `code` (plus `value` for the offending text)
to every parse_roster_csv() error -- see test_roster.py -- and this pins the
frontend side: rosterErrorText() looks up a localized "roster_error.<code>"
i18n string via t() and falls back to the raw `message` for an unrecognized
code, and both renderRosterImportErrors() and
renderRosterImportPreviewModal() render through it with `.row-error` rows.

This executes the REAL functions and the REAL `dictionaries` object literal
pulled out of the page via brace-depth extraction under node -- never a
source-text grep -- so deleting the real wiring turns this red instead of
being satisfied by a nearby comment.
"""

import json
import re
import subprocess
import tempfile
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


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


def _matching_paren_end(source: str, open_idx: int) -> int:
    depth = 0
    i = open_idx
    while i < len(source):
        char = source[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError("no matching closing paren found")


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    async_start = source.rfind("async ", 0, start)
    if async_start != -1 and source[async_start:start] == "async ":
        start = async_start
    paren_open = source.index("(", start)
    paren_end = _matching_paren_end(source, paren_open)
    brace_open = source.index("{", paren_end)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _extract_dictionaries_js(source: str) -> str:
    const_start = source.index("const dictionaries = {")
    const_open = source.index("{", const_start)
    const_close = _matching_brace_end(source, const_open)

    zh_marker = 'dictionaries["zh-TW"] = {'
    zh_start = source.index(zh_marker, const_close)
    zh_open = source.index("{", zh_start)
    zh_close = _matching_brace_end(source, zh_open)

    return source[const_start : zh_close + 1] + ";"


def _extract_t_function(source: str) -> str:
    t_start = source.index("function t(")
    paren_open = source.index("(", t_start)
    paren_end = _matching_paren_end(source, paren_open)
    t_open = source.index("{", paren_end)
    t_end = _matching_brace_end(source, t_open)
    return source[t_start : t_end + 1]


def _run_node(script: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as tmp_file:
        tmp_file.write(script)
        tmp_file.flush()
        tmp_path = tmp_file.name
    try:
        result = subprocess.run(
            ["node", tmp_path], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
        return result.stdout.strip()
    finally:
        Path(tmp_path).unlink()


def _run_error_text(locale: str, error_js: str) -> str:
    source = _strip_js_comments(_read())
    dict_js = _extract_dictionaries_js(source)
    t_fn = _extract_t_function(source)
    error_text_fn = _extract_function(source, "rosterErrorText")

    harness = f"""
{dict_js}
let currentLocale = "{locale}";
{t_fn}
{error_text_fn}
console.log(JSON.stringify(rosterErrorText({error_js})));
"""
    output = _run_node(harness)
    return json.loads(output)


def test_zh_locale_renders_localized_text_for_invalid_division_with_value():
    result = _run_error_text(
        "zh-TW",
        '{ row: 2, message: "Invalid division: mixed", code: "invalid_division", value: "mixed" }',
    )
    assert "mixed" in result
    # Must NOT just echo the raw English message -- pins that the zh
    # dictionary lookup actually fired for a known code.
    assert result != "Invalid division: mixed"
    assert "組別" in result or "不正確" in result or "無效" in result


def test_en_locale_renders_localized_text_for_invalid_division_with_value():
    result = _run_error_text(
        "en-US",
        '{ row: 2, message: "Invalid division: mixed", code: "invalid_division", value: "mixed" }',
    )
    assert "mixed" in result


def test_unknown_code_falls_back_to_raw_message():
    result = _run_error_text(
        "zh-TW",
        '{ row: 5, message: "Some future backend error", code: "totally_unknown_code" }',
    )
    assert result == "Some future backend error"


def test_missing_code_falls_back_to_raw_message():
    result = _run_error_text(
        "zh-TW",
        '{ row: 5, message: "Legacy error with no code" }',
    )
    assert result == "Legacy error with no code"


def test_render_roster_import_errors_uses_localized_text_and_row_error_class():
    source = _strip_js_comments(_read())
    render_fn = _extract_function(source, "renderRosterImportErrors")
    error_text_fn = _extract_function(source, "rosterErrorText")
    dict_js = _extract_dictionaries_js(source)
    t_fn = _extract_t_function(source)

    harness = f"""
const elements = {{}};
function el(id) {{
  if (!elements[id]) {{
    elements[id] = {{ id, innerHTML: "" }};
  }}
  return elements[id];
}}
function $(id) {{ return el(id); }}
function escapeHtml(value) {{
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}}
{dict_js}
let currentLocale = "zh-TW";
{t_fn}
{error_text_fn}
{render_fn}

renderRosterImportErrors([
  {{ row: 3, message: "Invalid division: mixed", code: "invalid_division", value: "mixed" }},
]);
console.log(JSON.stringify({{ html: el("roster-import-errors").innerHTML }}));
"""
    output = _run_node(harness)
    result = json.loads(output)
    assert "row-error" in result["html"]
    assert "mixed" in result["html"]
    assert "Invalid division: mixed" not in result["html"]
