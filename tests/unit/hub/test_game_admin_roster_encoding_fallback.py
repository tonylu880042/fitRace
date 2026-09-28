"""Game Admin's roster CSV file picker (hub_server/static/gameAdmin.html)
must decode a selected file as UTF-8 first and fall back to Big5 when that
fails -- a zh-TW Excel export of a plain .csv is typically Big5, not UTF-8,
and reading it as UTF-8 (the old `reader.readAsText(file)` default) mangles
every Chinese character.

This executes the REAL `decodeRosterFileBytes()` pulled out of the page via
brace-depth extraction under node, feeding it real UTF-8, UTF-8-with-BOM,
and Big5 byte sequences (the Big5 bytes are produced via Python's own
`"...".encode("big5")`, not hand-typed) -- never a source-text grep -- so
deleting the real fallback turns this red instead of being satisfied by a
nearby comment.
"""

import json
import re
import subprocess
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


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


def _decode(byte_values: list[int]) -> str:
    source = _strip_js_comments(_read())
    decode_fn = _extract_function(source, "decodeRosterFileBytes")
    assert "TextDecoder" in decode_fn  # sanity: real source, not a stub

    harness = f"""
{decode_fn}
const bytes = new Uint8Array({json.dumps(byte_values)});
console.log(JSON.stringify({{ text: decodeRosterFileBytes(bytes.buffer) }}));
"""
    output = _run_node(harness)
    return json.loads(output)["text"]


def test_decodes_plain_utf8_bytes():
    payload = "name,division\nAlice,men\n".encode("utf-8")
    assert _decode(list(payload)) == "name,division\nAlice,men\n"


def test_decodes_utf8_with_bom():
    payload = "name,division\n小明,men\n".encode("utf-8-sig")
    # TextDecoder strips a leading UTF-8 BOM by default -- this pins that
    # the raw bytes still decode to the correct text, BOM or not.
    assert _decode(list(payload)) == "name,division\n小明,men\n"


def test_falls_back_to_big5_when_utf8_decoding_fails():
    payload = "姓名,組別".encode("big5")
    assert _decode(list(payload)) == "姓名,組別"
