"""Unattended auto-return-to-dashboard timer -- result.html ONLY.

result.html (the personal QR-scan result page) redirects back to `/` after
AUTO_REDIRECT_SECONDS (60) seconds of nobody touching the manual back
control, with a visible countdown next to that control
(`result.auto_redirect`, a six-locale key taking a `{seconds}` placeholder).

results.html (the venue QR wall athletes queue up at to find their card and
scan their own token) does NOT get this timer: bouncing the wall to the
dashboard every 60 seconds would leave the next person in the queue looking
at the dashboard instead of the wall. It keeps only the manual back control
added alongside the original fix -- see
tests/unit/hub/test_result_back_navigation.py for that. This module pins
the split is real: result.html schedules the redirect and results.html
carries none of the machinery for it (not just an unused function -- no
`setInterval` call anywhere in its script at all), so a later edit that
"restores symmetry" between the two pages by re-adding a wall redirect
fails here.

Extraction mirrors the brace-depth technique already used by
tests/unit/hub/test_result_pages_anonymous_display.py and runs the real
functions under `node -e` against a stubbed `setInterval`/`clearInterval`/
`document`/`window`/`t`.
"""

import json
import re
import subprocess
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"
LOCALES_DIR = (
    Path(__file__).resolve().parents[3] / "hub_server" / "infrastructure" / "locales"
)

_LINE_COMMENT_RE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_js_comments(code: str) -> str:
    without_blocks = _BLOCK_COMMENT_RE.sub("", code)
    return _LINE_COMMENT_RE.sub("", without_blocks)


def _read(page: str) -> str:
    return (STATIC_DIR / page).read_text(encoding="utf-8")


def _stripped_script(page: str) -> str:
    source = _read(page)
    start = source.index("<script>") + len("<script>")
    end = source.index("</script>", start)
    return _strip_js_comments(source[start:end])


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


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace_open = source.index("{", start)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _extract_auto_redirect_block(source: str) -> str:
    """Pull the whole self-contained auto-redirect unit from result.html --
    the AUTO_REDIRECT_SECONDS constant, its two `let` state variables, and
    the three functions (updateAutoRedirectNotice / startAutoRedirect /
    stopAutoRedirect / goToDashboard) -- as one slice, brace-matched off the
    LAST function in the block so it is robust to reordering the
    declarations in between."""
    start = source.index("const AUTO_REDIRECT_SECONDS = 60;")
    fn_marker = source.index("function goToDashboard(", start)
    brace_open = source.index("{", fn_marker)
    brace_end = _matching_brace_end(source, brace_open)
    return source[start : brace_end + 1]


def _load_locale(locale: str) -> dict:
    with open(LOCALES_DIR / f"{locale}.json", "r", encoding="utf-8") as file:
        return json.load(file)


_HARNESS_STUBS = """
const events = { intervalCalls: [], clearedIds: [] };
let nextIntervalId = 1;
function setInterval(fn, ms) {
  const id = nextIntervalId++;
  events.intervalCalls.push({ id, fn, ms });
  return id;
}
function clearInterval(id) {
  events.clearedIds.push(id);
}
const messages = { "result.auto_redirect": "Returning in {seconds}s" };
function t(key, params) {
  let value = messages[key] || key;
  if (params) {
    Object.entries(params).forEach(([name, replacement]) => {
      value = value.split(`{${name}}`).join(String(replacement));
    });
  }
  return value;
}
class FakeEl {
  constructor() { this.textContent = ""; }
}
const noticeEl = new FakeEl();
const document = {
  getElementById: (id) => (id === "auto-redirect-notice" ? noticeEl : null),
};
const window = { location: { href: "" } };
"""


def _run(driver_js: str) -> dict:
    source = _read("result.html")
    block = _strip_js_comments(_extract_auto_redirect_block(source))
    script = _HARNESS_STUBS + block + "\n" + driver_js
    result = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=5
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr}\nScript:\n{script}")
    return json.loads(result.stdout)


_TICK_DRIVER = """
startAutoRedirect();
const first = events.intervalCalls[0];
const before = { ms: first.ms, notice: noticeEl.textContent, href: window.location.href };
for (let i = 0; i < 59; i++) { first.fn(); }
const at59 = { notice: noticeEl.textContent, href: window.location.href };
first.fn();
const at60 = {
  notice: noticeEl.textContent,
  href: window.location.href,
  cleared: events.clearedIds.includes(first.id),
};
console.log(JSON.stringify({ before, at59, at60 }));
"""


def test_result_html_auto_redirect_fires_at_exactly_sixty_ticks_of_a_one_second_interval():
    out = _run(_TICK_DRIVER)
    assert out["before"]["ms"] == 1000
    assert out["before"]["notice"] == "Returning in 60s"
    assert out["before"]["href"] == ""

    assert out["at59"]["notice"] == "Returning in 1s"
    assert out["at59"]["href"] == "", "must not redirect before the 60th tick"

    assert out["at60"]["href"] == "/"
    assert out["at60"]["cleared"] is True


_MANUAL_BACK_DRIVER = """
startAutoRedirect();
const first = events.intervalCalls[0];
goToDashboard();
console.log(JSON.stringify({
  href: window.location.href,
  cleared: events.clearedIds.includes(first.id),
}));
"""


def test_result_html_manual_back_control_clears_the_pending_timer():
    out = _run(_MANUAL_BACK_DRIVER)
    assert out["href"] == "/"
    assert out["cleared"] is True


# -- the 60-second delay is a single named constant, not a scattered magic
# number.


def test_auto_redirect_seconds_constant_is_sixty_on_result_html():
    source = _read("result.html")
    assert "const AUTO_REDIRECT_SECONDS = 60;" in source
    # the constant must be the only literal 60 driving the delay -- no
    # second hardcoded copy inside the timer functions themselves.
    block = _extract_auto_redirect_block(source)
    after_const = block[len("const AUTO_REDIRECT_SECONDS = 60;") :]
    assert "60" not in after_const


def test_result_html_calls_t_with_a_seconds_param_for_the_auto_redirect_key():
    source = _strip_js_comments(_read("result.html"))
    assert 't("result.auto_redirect", { seconds: autoRedirectRemaining })' in source


# -- i18n: the countdown key exists with a genuine, differing translation --
# in all six locales and the {seconds} placeholder survives everywhere.
# result.html is still the only page that renders it, but the key itself
# must not be deleted from any locale file while result.html depends on it.


def test_auto_redirect_key_exists_with_seconds_placeholder_in_every_locale():
    for locale in ("en-US", "zh-TW", "de-CH", "fr", "it", "sv"):
        messages = _load_locale(locale)
        assert "result.auto_redirect" in messages, f"missing from {locale}.json"
        assert (
            "{seconds}" in messages["result.auto_redirect"]
        ), f"{locale}.json result.auto_redirect lost its {{seconds}} token"


def test_auto_redirect_key_zh_tw_value_is_genuinely_translated():
    en = _load_locale("en-US")["result.auto_redirect"]
    zh = _load_locale("zh-TW")["result.auto_redirect"]
    assert zh != en
    assert re.search(r"[一-鿿]", zh)


# -- results.html (the QR wall) must carry NONE of this machinery. These --
# are the tests that would fail if someone later "restores symmetry" between
# the two pages by re-adding a redirect timer to the wall.


def test_results_html_has_no_redirect_timer_at_all():
    script = _stripped_script("results.html")
    assert "setInterval" not in script, (
        "results.html must never schedule a redirect timer -- athletes "
        "queueing at the QR wall must not get bounced to the dashboard "
        "while they are still looking for their card"
    )
    assert "AUTO_REDIRECT_SECONDS" not in script
    assert "startAutoRedirect" not in script
    assert "stopAutoRedirect" not in script
    assert "clearInterval" not in script


def test_results_html_never_references_the_auto_redirect_i18n_key():
    script = _stripped_script("results.html")
    assert "result.auto_redirect" not in script


def test_results_html_has_no_countdown_element_in_the_markup():
    html = _read("results.html")
    assert "auto-redirect-notice" not in html


def test_results_html_goto_dashboard_just_navigates_with_no_timer_wiring():
    script = _stripped_script("results.html")
    fn = _extract_function(script, "goToDashboard")
    assert "clearInterval" not in fn
    assert "stopAutoRedirect" not in fn
    assert 'window.location.href = "/"' in fn
