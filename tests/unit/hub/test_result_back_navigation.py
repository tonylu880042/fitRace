"""Back-to-dashboard control on the two public results pages.

result.html (the personal QR-scan result page) used to have a
"Back to Dashboard" button, but it lived ONLY inside the `#error-state`
block, which starts `style="display: none;"` -- so on the SUCCESS path
(an athlete scanning their own QR code, the normal case) no back control
ever rendered. results.html (the venue results wall) had no back control
anywhere at all.

Both pages now carry a single `.back-nav` control that sits outside every
conditionally-hidden state block, reusing the existing `result.back_button`
i18n key rather than minting a near-duplicate.

These tests read the served markup directly (no `node -e` execution
needed here, since the control's visibility is pure HTML/CSS -- it is not
toggled by any JS render path the way the card/error/loading states are).
The key assertion -- "back-button" not appearing inside the `#error-state`
block -- is what makes this test fail against the pre-fix file: on the old
markup, `class="back-button"` existed ONLY inside that block.
"""

import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"

_BACK_BUTTON_RE = re.compile(
    r'<button[^>]*class="back-button"[^>]*>.*?</button>', re.DOTALL
)


def _read(page: str) -> str:
    return (STATIC_DIR / page).read_text(encoding="utf-8")


def _tag_block(html: str, needle: str) -> str:
    """Return the full `<div ...> ... </div>` block that contains `needle`,
    matched by counting `<div` opens against `</div>` closes so nested divs
    inside the block don't fool a naive "find the next </div>" search."""
    marker_idx = html.index(needle)
    div_start = html.rindex("<div", 0, marker_idx)
    tag_end = html.index(">", div_start) + 1
    depth = 1
    i = tag_end
    while depth > 0:
        next_open = html.find("<div", i)
        next_close = html.find("</div>", i)
        if next_close == -1:
            raise ValueError("no matching closing </div> found")
        if next_open != -1 and next_open < next_close:
            depth += 1
            i = html.index(">", next_open) + 1
        else:
            depth -= 1
            i = next_close + len("</div>")
    return html[div_start:i]


# -- result.html ------------------------------------------------------------


def test_result_html_back_button_lives_outside_the_hidden_error_block():
    html = _read("result.html")
    error_block = _tag_block(html, 'id="error-state"')
    assert "back-button" not in error_block, (
        "the back control must be reachable on the SUCCESS path too -- "
        "putting it only inside #error-state (which starts display:none) "
        "is the exact bug this test exists to catch"
    )


def test_result_html_has_exactly_one_back_control():
    html = _read("result.html")
    assert html.count('class="back-button"') == 1


def test_result_html_back_control_reuses_shared_i18n_key():
    html = _read("result.html")
    match = _BACK_BUTTON_RE.search(html)
    assert match, "no back-button control found in result.html"
    tag = match.group(0)
    assert 'data-i18n="result.back_button"' in tag
    assert "goToDashboard()" in tag


def test_result_html_back_nav_wrapper_is_not_itself_hidden():
    html = _read("result.html")
    back_nav_block = _tag_block(html, 'class="back-nav"')
    assert "back-button" in back_nav_block
    open_tag_start = html.rindex("<div", 0, html.index('class="back-nav"'))
    open_tag = html[open_tag_start : html.index(">", open_tag_start) + 1]
    assert "display: none" not in open_tag


# -- results.html -------------------------------------------------------


def test_results_html_has_a_back_control():
    html = _read("results.html")
    assert html.count('class="back-button"') == 1


def test_results_html_back_control_reuses_shared_i18n_key():
    html = _read("results.html")
    match = _BACK_BUTTON_RE.search(html)
    assert match, "no back-button control found in results.html"
    tag = match.group(0)
    assert 'data-i18n="result.back_button"' in tag
    assert "goToDashboard()" in tag


def test_results_html_back_nav_wrapper_is_not_itself_hidden():
    html = _read("results.html")
    back_nav_block = _tag_block(html, 'class="back-nav"')
    assert "back-button" in back_nav_block
    open_tag_start = html.rindex("<div", 0, html.index('class="back-nav"'))
    open_tag = html[open_tag_start : html.index(">", open_tag_start) + 1]
    assert "display: none" not in open_tag


# -- mobile reachability: the control must not be able to overflow a --------
# 375px viewport (both pages are opened from a phone camera QR scan).


def _style_block(html: str) -> str:
    return html[html.index("<style>") : html.index("</style>")]


def _rule_body(css: str, selector: str) -> str:
    start = css.index(selector)
    open_idx = css.index("{", start)
    close_idx = css.index("}", open_idx)
    return css[open_idx:close_idx]


def test_result_html_back_button_css_has_no_fixed_overflow_width():
    css = _style_block(_read("result.html"))
    rule = _rule_body(css, ".back-button {")
    assert "width:" not in rule.replace(" ", "")


def test_results_html_back_button_css_has_no_fixed_overflow_width():
    css = _style_block(_read("results.html"))
    rule = _rule_body(css, ".back-button {")
    assert "width:" not in rule.replace(" ", "")
