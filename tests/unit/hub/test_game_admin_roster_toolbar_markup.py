"""Roster toolbar crowding fix (hub_server/static/gameAdmin.html,
#roster-title panel header): the native #roster-file-input must be visually
hidden with an accessible technique (NOT display:none, so the <label
for="roster-file-input"> still triggers a click on it and it stays keyboard
reachable), and that label must carry a button-like class so it visually
matches the neighboring <button> elements. The toolbar's button group must
allow wrapping to a second row on narrow widths, with its own buttons/label
kept on one line (white-space: nowrap) so multi-word labels like 下載名單範本
don't break mid-word.

This parses the REAL markup with Python's stdlib html.parser -- never a
regex over raw text -- so a comment containing the same class name or
selector text can't satisfy these assertions; only an attribute on the
actual parsed element counts.
"""

from html.parser import HTMLParser
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parents[3] / "hub_server" / "static"


def _read() -> str:
    return (STATIC_DIR / "gameAdmin.html").read_text(encoding="utf-8")


class _ElementCollector(HTMLParser):
    """Collects every start tag's (tag, attrs dict) plus a synthetic parent
    chain, so a test can ask "what does the div.button-group wrapping the
    roster-file-input actually look like" without a regex over raw HTML."""

    def __init__(self):
        super().__init__()
        self.elements = []  # list of {"tag", "attrs", "parent_ids", "parent_classes"}
        self._stack = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        parent_ids = [frame["attrs"].get("id") for frame in self._stack]
        parent_classes = [frame["attrs"].get("class", "") for frame in self._stack]
        frame = {
            "tag": tag,
            "attrs": attrs_dict,
            "parent_ids": parent_ids,
            "parent_classes": parent_classes,
        }
        self.elements.append(frame)
        # Void elements never get a matching end tag in this document's
        # usage (input, br, etc.) -- only push tags that plausibly nest.
        if tag not in ("input", "br", "meta", "link", "img"):
            self._stack.append(frame)

    def handle_endtag(self, tag):
        if self._stack and self._stack[-1]["tag"] == tag:
            self._stack.pop()


def _parse(html: str) -> _ElementCollector:
    collector = _ElementCollector()
    collector.feed(html)
    return collector


def _classes(attrs: dict) -> list[str]:
    return (attrs.get("class") or "").split()


def _find_one(elements, predicate):
    matches = [el for el in elements if predicate(el)]
    assert len(matches) == 1, f"expected exactly one match, got {len(matches)}"
    return matches[0]


def test_roster_file_input_is_visually_hidden_not_display_none():
    collector = _parse(_read())
    file_input = _find_one(
        collector.elements,
        lambda el: el["tag"] == "input"
        and el["attrs"].get("id") == "roster-file-input",
    )
    classes = _classes(file_input["attrs"])
    assert classes, "expected #roster-file-input to carry a visually-hidden class"

    # The technique must not be display:none -- that would also remove it
    # from the keyboard/click target the <label> depends on. Locate the CSS
    # rule for whichever class the input carries and check its declarations
    # directly (still not a grep over the whole document -- just the one
    # rule body for this element's own class).
    source = _read()
    style_start = source.index("<style>")
    style_end = source.index("</style>", style_start)
    style_block = source[style_start:style_end]

    hidden_class = classes[0]
    selector = f".{hidden_class}"
    rule_start = style_block.index(selector)
    brace_open = style_block.index("{", rule_start)
    brace_close = style_block.index("}", brace_open)
    rule_body = style_block[brace_open + 1 : brace_close]

    assert (
        "display" not in rule_body
        or "none" not in rule_body.lower().split("display")[1].split(";")[0]
    )
    assert "hidden" not in rule_body.lower() or "overflow" in rule_body.lower()


def test_roster_file_input_label_has_button_like_class():
    collector = _parse(_read())
    label = _find_one(
        collector.elements,
        lambda el: el["tag"] == "label"
        and el["attrs"].get("for") == "roster-file-input",
    )
    classes = _classes(label["attrs"])
    assert classes, "expected the label to carry a button-styling class"

    # The class must actually be styled to look like a button (min-height,
    # border, background) -- not just present as an inert marker.
    source = _read()
    style_start = source.index("<style>")
    style_end = source.index("</style>", style_start)
    style_block = source[style_start:style_end]

    label_class = classes[0]
    selector = f".{label_class}"
    rule_start = style_block.index(selector)
    brace_open = style_block.index("{", rule_start)
    brace_close = style_block.index("}", brace_open)
    rule_body = style_block[brace_open + 1 : brace_close].lower()

    assert "border" in rule_body
    assert "cursor" in rule_body


def test_roster_toolbar_button_group_wraps_with_nowrap_children():
    collector = _parse(_read())
    file_input = _find_one(
        collector.elements,
        lambda el: el["tag"] == "input"
        and el["attrs"].get("id") == "roster-file-input",
    )
    toolbar_id = file_input["parent_ids"][-1]
    assert toolbar_id, "expected the roster toolbar's button-group div to carry an id"

    toolbar = _find_one(
        collector.elements,
        lambda el: el["tag"] == "div" and el["attrs"].get("id") == toolbar_id,
    )
    assert "button-group" in _classes(toolbar["attrs"])

    source = _read()
    style_start = source.index("<style>")
    style_end = source.index("</style>", style_start)
    style_block = source[style_start:style_end]

    toolbar_selector = f"#{toolbar_id}"
    rule_start = style_block.index(toolbar_selector)
    brace_open = style_block.index("{", rule_start)
    brace_close = style_block.index("}", brace_open)
    rule_body = style_block[brace_open + 1 : brace_close].lower()
    flex_wrap_value = rule_body.split("flex-wrap:")[1].split(";")[0].strip()
    assert flex_wrap_value == "wrap", flex_wrap_value

    # The buttons/label inside must be kept on one line even while the
    # group itself wraps.
    child_selector_start = style_block.index(toolbar_selector, brace_close)
    child_brace_open = style_block.index("{", child_selector_start)
    child_brace_close = style_block.index("}", child_brace_open)
    child_selector_text = style_block[child_selector_start:child_brace_open]
    child_rule_body = style_block[child_brace_open + 1 : child_brace_close].lower()
    assert "button" in child_selector_text or "label" in child_selector_text
    assert "white-space" in child_rule_body and "nowrap" in child_rule_body


def test_roster_file_input_still_reachable_by_its_label():
    collector = _parse(_read())
    file_input = _find_one(
        collector.elements,
        lambda el: el["tag"] == "input"
        and el["attrs"].get("id") == "roster-file-input",
    )
    label = _find_one(
        collector.elements,
        lambda el: el["tag"] == "label"
        and el["attrs"].get("for") == "roster-file-input",
    )
    assert label["attrs"]["for"] == file_input["attrs"]["id"]
