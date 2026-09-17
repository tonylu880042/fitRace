"""The venue dashboard (hub_server/static/index.html) shows two kinds of QR
codes: the self sign-up QR (header `#header-qr-container` plus every other
`.registration-empty-qr` block) and the Game Admin control-page QR
(`#game-admin-qr-card`). Per CLAUDE.md the dashboard is display-only -- no
local controls -- so visibility is driven purely by backend state carried
in every race-state payload (initial fetch + `state_change` WS messages),
never by a click on the dashboard itself.

This executes the REAL, unmodified applyDashboardQrVisibility() pulled out
of index.html's inline <script> via brace-depth matching (the same
technique as test_record_wall_relay.py) under node with a minimal fake
DOM, never a source-text grep -- so deleting the real wiring, or a nearby
comment containing the same substrings, cannot satisfy these assertions.
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


def _read_index() -> str:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def _matching_bracket_end(source: str, open_idx: int) -> int:
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
    raise ValueError("no matching close bracket found")


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_bracket_end(source, brace_open)
    return source[start : brace_end + 1]


def _run_node(script: str) -> str:
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return result.stdout.strip()


_DOM_STUB = """
function makeEl(id, classes) {
  return { id: id || null, style: {}, _classes: new Set(classes || []) };
}
function makeDocument(elements) {
  return {
    getElementById(id) {
      return elements.find((e) => e.id === id) || null;
    },
    querySelectorAll(selector) {
      const cls = selector.replace(".", "");
      return elements.filter((e) => e._classes.has(cls));
    },
  };
}
"""


def _extract_apply_fn() -> str:
    source = _strip_js_comments(_read_index())
    fn = _extract_function(source, "applyDashboardQrVisibility")
    assert "header-qr-container" in fn  # sanity: real source, not a stub
    assert "game-admin-qr-card" in fn
    return fn


def _run(data_js: str) -> dict:
    fn = _extract_apply_fn()
    script = f"""
{_DOM_STUB}
const headerQr = makeEl("header-qr-container");
const regQr1 = makeEl(null, ["registration-empty-qr"]);
const regQr2 = makeEl(null, ["registration-empty-qr"]);
const adminCard = makeEl("game-admin-qr-card");
const document = makeDocument([headerQr, regQr1, regQr2, adminCard]);

{fn}

applyDashboardQrVisibility({data_js});
console.log(JSON.stringify({{
  headerDisplay: headerQr.style.display || "",
  regQr1Display: regQr1.style.display || "",
  regQr2Display: regQr2.style.display || "",
  adminDisplay: adminCard.style.display || "",
}}));
"""
    return json.loads(_run_node(script))


def test_signup_false_hides_header_and_every_registration_qr_block():
    result = _run("{ signup_qr_visible: false, admin_qr_visible: true }")
    assert result["headerDisplay"] == "none"
    assert result["regQr1Display"] == "none"
    assert result["regQr2Display"] == "none"
    assert result["adminDisplay"] != "none"


def test_admin_false_hides_only_the_admin_card():
    result = _run("{ signup_qr_visible: true, admin_qr_visible: false }")
    assert result["adminDisplay"] == "none"
    assert result["headerDisplay"] != "none"
    assert result["regQr1Display"] != "none"
    assert result["regQr2Display"] != "none"


def test_missing_fields_default_to_visible():
    result = _run("{}")
    assert result["headerDisplay"] != "none"
    assert result["regQr1Display"] != "none"
    assert result["regQr2Display"] != "none"
    assert result["adminDisplay"] != "none"


def test_true_shows_after_having_been_hidden():
    fn = _extract_apply_fn()
    script = f"""
{_DOM_STUB}
const headerQr = makeEl("header-qr-container");
const regQr1 = makeEl(null, ["registration-empty-qr"]);
const adminCard = makeEl("game-admin-qr-card");
const document = makeDocument([headerQr, regQr1, adminCard]);

{fn}

applyDashboardQrVisibility({{ signup_qr_visible: false, admin_qr_visible: false }});
applyDashboardQrVisibility({{ signup_qr_visible: true, admin_qr_visible: true }});
console.log(JSON.stringify({{
  headerDisplay: headerQr.style.display || "",
  regQr1Display: regQr1.style.display || "",
  adminDisplay: adminCard.style.display || "",
}}));
"""
    result = json.loads(_run_node(script))
    assert result["headerDisplay"] != "none"
    assert result["regQr1Display"] != "none"
    assert result["adminDisplay"] != "none"


def test_both_false_hides_everything():
    result = _run("{ signup_qr_visible: false, admin_qr_visible: false }")
    assert result["headerDisplay"] == "none"
    assert result["regQr1Display"] == "none"
    assert result["regQr2Display"] == "none"
    assert result["adminDisplay"] == "none"
