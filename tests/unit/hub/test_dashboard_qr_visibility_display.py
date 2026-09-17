"""The venue dashboard (hub_server/static/index.html) shows two kinds of QR
codes: the self sign-up QR (header `#header-qr-container` plus every other
`.registration-empty-qr` block) and the Game Admin control-page QR
(`#game-admin-qr-card`). Per CLAUDE.md the dashboard is display-only -- no
local controls -- so visibility is driven purely by backend state carried
in every race-state payload (initial fetch + `state_change` WS messages),
never by a click on the dashboard itself.

Hiding is DECLARATIVE, not per-element: applyDashboardQrVisibility() only
ever toggles `hide-signup-qr` / `hide-admin-qr` classes on document.body,
and a CSS rule (scoped by those body classes, verified below against the
real <style> block) hides #header-qr-container, .registration-empty-qr and
#game-admin-qr-card. This matters because `.registration-empty-qr` blocks
are rebuilt in several places OUTSIDE the updateUIState() call chain --
most importantly the WebSocket telemetry handler in connectWebSocket()
(untyped progress payloads -> renderLeaderboard()) and resetRace() (which
overwrites the leaderboard container's innerHTML *after* updateUIState()
already ran). An earlier per-element implementation (setting
`el.style.display` on whatever elements existed in the DOM at the moment
applyDashboardQrVisibility() ran) missed every block created by those
later, unrelated renders -- a hidden sign-up QR would silently reappear on
the very next telemetry tick with no state change at all. The body-class +
CSS approach covers any such block automatically, because the browser
re-evaluates the CSS rule against the DOM on every paint, not just when
JS last ran.

This executes the REAL, unmodified applyDashboardQrVisibility() /
renderRegistrationEmptyState() pulled out of index.html's inline <script>
via brace-depth matching (the same technique as test_record_wall_relay.py)
under node with a minimal fake DOM, and separately parses the real <style>
block (CSS comments stripped first) rather than grepping the whole file --
so deleting the real wiring, or a nearby comment containing the same
substrings, cannot satisfy these assertions.
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
function makeClassList(el) {
  return {
    add(c) { el._classes.add(c); },
    remove(c) { el._classes.delete(c); },
    toggle(c, force) {
      if (force === undefined) {
        if (el._classes.has(c)) { el._classes.delete(c); return false; }
        el._classes.add(c);
        return true;
      }
      if (force) el._classes.add(c); else el._classes.delete(c);
      return force;
    },
    contains(c) { return el._classes.has(c); },
  };
}
function makeEl(id, classes) {
  const el = { id: id || null, style: {}, _classes: new Set(classes || []) };
  el.classList = makeClassList(el);
  return el;
}
function makeDocument(body) {
  return { body };
}
"""


def _extract_apply_fn() -> str:
    source = _strip_js_comments(_read_index())
    fn = _extract_function(source, "applyDashboardQrVisibility")
    assert "hide-signup-qr" in fn  # sanity: real source, not a stub
    assert "hide-admin-qr" in fn
    return fn


def _run(data_js: str) -> dict:
    fn = _extract_apply_fn()
    script = f"""
{_DOM_STUB}
const body = makeEl(null, []);
const document = makeDocument(body);

{fn}

applyDashboardQrVisibility({data_js});
console.log(JSON.stringify({{
  hideSignup: document.body.classList.contains("hide-signup-qr"),
  hideAdmin: document.body.classList.contains("hide-admin-qr"),
}}));
"""
    return json.loads(_run_node(script))


# ---------------------------------------------------------------------------
# applyDashboardQrVisibility(): toggles the two body classes, nothing else.
# ---------------------------------------------------------------------------


def test_signup_false_sets_hide_signup_qr_body_class_only():
    result = _run("{ signup_qr_visible: false, admin_qr_visible: true }")
    assert result["hideSignup"] is True
    assert result["hideAdmin"] is False


def test_admin_false_sets_hide_admin_qr_body_class_only():
    result = _run("{ signup_qr_visible: true, admin_qr_visible: false }")
    assert result["hideSignup"] is False
    assert result["hideAdmin"] is True


def test_missing_fields_default_to_no_hide_classes():
    result = _run("{}")
    assert result["hideSignup"] is False
    assert result["hideAdmin"] is False


def test_both_false_sets_both_hide_classes():
    result = _run("{ signup_qr_visible: false, admin_qr_visible: false }")
    assert result["hideSignup"] is True
    assert result["hideAdmin"] is True


def test_hide_classes_are_removed_after_having_been_set():
    fn = _extract_apply_fn()
    script = f"""
{_DOM_STUB}
const body = makeEl(null, []);
const document = makeDocument(body);

{fn}

applyDashboardQrVisibility({{ signup_qr_visible: false, admin_qr_visible: false }});
applyDashboardQrVisibility({{ signup_qr_visible: true, admin_qr_visible: true }});
console.log(JSON.stringify({{
  hideSignup: document.body.classList.contains("hide-signup-qr"),
  hideAdmin: document.body.classList.contains("hide-admin-qr"),
}}));
"""
    result = json.loads(_run_node(script))
    assert result["hideSignup"] is False
    assert result["hideAdmin"] is False


# ---------------------------------------------------------------------------
# CSS: the actual <style> block (comments stripped) must map each body
# class to `display: none !important` on the elements it's meant to hide.
# !important matters most for #header-qr-container, whose own markup
# carries an inline `style="display: flex; ..."` -- without !important a
# class-scoped rule loses to that inline style and never hides it.
# ---------------------------------------------------------------------------

_CSS_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)


def _strip_css_comments(css: str) -> str:
    return _CSS_BLOCK_COMMENT_RE.sub("", css)


def _style_block() -> str:
    source = _read_index()
    start = source.index("<style>") + len("<style>")
    end = source.index("</style>", start)
    return _strip_css_comments(source[start:end])


def _css_rules():
    return [
        (selector.strip(), body.strip())
        for selector, body in _CSS_RULE_RE.findall(_style_block())
    ]


def _find_rules(*selector_substrings):
    return [
        (selector, body)
        for selector, body in _css_rules()
        if all(sub in selector for sub in selector_substrings)
    ]


def _hides_important(body: str) -> bool:
    return "display" in body and "none" in body and "!important" in body


def test_css_hides_header_qr_container_under_hide_signup_qr():
    matches = _find_rules("hide-signup-qr", "header-qr-container")
    assert matches, "no CSS rule scopes #header-qr-container under .hide-signup-qr"
    assert any(_hides_important(body) for _, body in matches)


def test_css_hides_registration_empty_qr_under_hide_signup_qr():
    matches = _find_rules("hide-signup-qr", "registration-empty-qr")
    assert matches, "no CSS rule scopes .registration-empty-qr under .hide-signup-qr"
    assert any(_hides_important(body) for _, body in matches)


def test_css_hides_admin_qr_card_under_hide_admin_qr():
    matches = _find_rules("hide-admin-qr", "game-admin-qr-card")
    assert matches, "no CSS rule scopes #game-admin-qr-card under .hide-admin-qr"
    assert any(_hides_important(body) for _, body in matches)


# ---------------------------------------------------------------------------
# Regression: the exact bug reported. applyDashboardQrVisibility() runs
# once, then a completely unrelated render (renderRegistrationEmptyState(),
# the function every affected call site funnels through) rebuilds a fresh
# .registration-empty-qr block with no knowledge of the visibility flags.
# The body class set earlier must still be in place (nothing about
# rendering touches document.body), and the fresh block must carry the
# exact class the CSS rule above maps to display:none -- together proving
# the browser hides it with no further JS involvement, unlike the old
# per-element implementation which only ever reached elements that existed
# in the DOM at the moment it ran.
# ---------------------------------------------------------------------------


def _t_stub() -> str:
    return "const t = (key) => key;\n"


def _escape_html_stub() -> str:
    return (
        "function escapeHtml(value) {\n"
        "  return String(value === null || value === undefined ? '' : value)\n"
        "    .replace(/&/g, '&amp;')\n"
        "    .replace(/</g, '&lt;')\n"
        "    .replace(/>/g, '&gt;');\n"
        "}\n"
    )


def test_qr_hidden_by_apply_stays_hidden_across_a_later_unrelated_render():
    source = _strip_js_comments(_read_index())
    apply_fn = _extract_function(source, "applyDashboardQrVisibility")
    render_fn = _extract_function(source, "renderRegistrationEmptyState")
    script = f"""
{_DOM_STUB}
const body = makeEl(null, []);
const document = makeDocument(body);
{_t_stub()}
{_escape_html_stub()}
let currentSignupQrUrl = "https://example.invalid/qr.svg";

{apply_fn}
{render_fn}

// The bug scenario: visibility is applied once (e.g. a state_change with
// signup_qr_visible=false)...
applyDashboardQrVisibility({{ signup_qr_visible: false, admin_qr_visible: true }});

// ...then a render happens completely independently, exactly like the
// telemetry-triggered renderLeaderboard() path or resetRace()'s direct
// innerHTML assignment -- neither of which passes through
// applyDashboardQrVisibility() or even knows the flags exist.
const freshHtml = renderRegistrationEmptyState("Waiting for the race to start...");

console.log(JSON.stringify({{
  hideSignupStillSet: document.body.classList.contains("hide-signup-qr"),
  freshBlockHasTargetClass: freshHtml.includes('class="registration-empty-qr"'),
}}));
"""
    result = json.loads(_run_node(script))
    assert result["hideSignupStillSet"] is True
    assert result["freshBlockHasTargetClass"] is True
