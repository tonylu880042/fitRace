"""Operator card: reconnect one machine, and say when it last spoke.

Two gaps the venue hit on the same card. A machine that lost its BLE link
sits at "waiting" with only a "remove" control, so the way back was to unbind
and re-pair it. And nothing on the card said WHEN the signal stopped, so
there was no way to tell a machine that dropped a minute ago from one that
has been dead since the morning.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from edge_node.infrastructure.fastapi import app as edge_app_module


def _page() -> str:
    return TestClient(edge_app_module.app).get("/").text


def _function(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("\n    function ", start)
    return source[start:end]


# -- 1. reconnect control ---------------------------------------------------


def test_each_card_carries_its_own_reconnect_control():
    source = _page()
    assert 'data-role="reconnect-binding"' in source


def test_reconnect_posts_the_single_device_endpoint_for_that_card():
    source = _page()
    fn = _function(source, "async function reconnectBinding(nodeId)")

    assert 'adminFetch("/api/antenna/reconnect-device"' in fn
    assert 'method: "POST"' in fn
    assert "JSON.stringify({ node_id: nodeId })" in fn
    # The whole-node command would re-push every channel's list; this control
    # is deliberately about one machine.
    assert "/api/antenna/reconnect-configured" not in fn


def test_reconnect_needs_no_confirmation_but_refreshes_either_way():
    """Non-destructive, so no confirm dialog -- but the card has to refresh
    whether the request succeeded or threw."""
    source = _page()
    fn = _function(source, "async function reconnectBinding(nodeId)")

    assert "window.confirm" not in fn
    try_index = fn.index("try {")
    catch_index = fn.index("} catch (error) {")
    refresh_index = fn.rindex("await loadConfig();")
    assert try_index < catch_index < refresh_index


def test_the_card_click_handler_wires_the_reconnect_role():
    source = _page()
    start = source.index(
        'document.getElementById("binding-cards").addEventListener("click"'
    )
    handler = source[start : source.index("\n    });", start)]

    assert "closest('[data-role=\"reconnect-binding\"]')" in handler
    assert "reconnectBinding(" in handler


# -- 2. last signal time ----------------------------------------------------


def test_the_card_has_a_last_signal_field():
    source = _page()
    assert 'data-field="last-signal"' in source


def test_last_signal_shows_the_clock_time_of_the_newest_sample():
    """A relative age alone ("12 minutes ago") makes an operator do the
    arithmetic; the wall-clock time is what gets compared against when the
    class started."""
    source = _page()
    fn = _function(source, "function updateBindingCardLeaves()")

    assert 'data-field="last-signal"' in fn
    assert "formatLastSignal(" in fn

    formatter = _function(source, "function formatLastSignal(")
    # Reuses the page's existing wall-clock formatter rather than growing a
    # second one that could drift from it.
    assert "formatEventTime(epochMs)" in formatter
    assert 't("operator.card_last_signal_never")' in formatter


def test_last_signal_prefers_whichever_source_spoke_most_recently():
    """MQTT can pause for tens of seconds during a pairing scan while UART
    frames keep arriving -- taking only the MQTT stamp would report a
    disconnection that never happened."""
    source = _page()
    fn = _function(source, "function updateBindingCardLeaves()")

    assert "lastSignalMs" in fn
    assert "Math.max(" in fn


# -- 3. i18n ----------------------------------------------------------------


def test_new_card_keys_exist_in_both_locales():
    locales_dir = Path(edge_app_module.__file__).resolve().parent.parent / "locales"
    en = json.loads((locales_dir / "en.json").read_text(encoding="utf-8"))
    zh_tw = json.loads((locales_dir / "zh_tw.json").read_text(encoding="utf-8"))

    for key in (
        "operator.card_reconnect",
        "operator.card_reconnect_working",
        "operator.card_reconnect_success",
        "operator.card_reconnect_failed",
        "operator.card_last_signal",
        "operator.card_last_signal_never",
    ):
        assert key in en, key
        assert key in zh_tw, key
    assert set(en.keys()) == set(zh_tw.keys())


def test_the_page_carries_no_hardcoded_chinese_for_the_new_controls():
    """Every user-facing string goes through the locale dictionaries."""
    source = _page()
    body = source[source.index("function renderBindingCards()") :]
    marker = body[: body.index("function updateBindingCardLeaves()")]
    assert "重新連線" not in marker
    assert "最後訊號" not in marker
