"""Operator home cards: don't cry "waiting" the instant pairing finishes.

finishPairing() restarts the edge runtime service (restart:true), which pauses
MQTT publishing for every binding for several seconds -- not just the one that
was just paired. The pill used to be a strict binary (live/waiting), so the
operator who just watched the pairing screen say "connected" would land on
the home view and see every card flip to "waiting". This gives the pill a
third, time-boxed state ("connecting") that holds for PAIRING_CONNECTING_GRACE_MS
after finishPairing() runs, so a card that simply hasn't reported yet doesn't
read as a failure.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from edge_node.infrastructure.fastapi import app as edge_app_module


def _page() -> str:
    return TestClient(edge_app_module.app).get("/").text


def _function(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("\n    function ", start)
    body = source[start:end]
    # Strip comment-only lines before returning. Otherwise a substring
    # assertion can be satisfied by a // comment that happens to contain the
    # same text as the real code -- the CLAUDE.md "comment silently makes the
    # suite green" trap -- while the actual statement underneath it was
    # deleted or altered. This applies the guard once, for every test in
    # this file that inspects an extracted function body.
    lines = [line for line in body.splitlines() if not line.strip().startswith("//")]
    return "\n".join(lines)


def _run_node(js: str) -> str:
    result = subprocess.run(
        ["node", "-e", js],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return result.stdout.strip()


# -- 1. pillStateForCard is a pure, testable function ------------------------


@pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to execute the extracted JS"
)
def test_pill_state_for_card_is_live_when_telemetry_is_fresh():
    source = _page()
    fn = _function(source, "function pillStateForCard(live, nowMs, connectingUntilMs)")

    out = _run_node(f"{fn}\nconsole.log(pillStateForCard(true, 1000, 5000));")
    assert out == "live"


@pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to execute the extracted JS"
)
def test_pill_state_for_card_is_connecting_inside_the_grace_window():
    source = _page()
    fn = _function(source, "function pillStateForCard(live, nowMs, connectingUntilMs)")

    out = _run_node(f"{fn}\nconsole.log(pillStateForCard(false, 1000, 5000));")
    assert out == "connecting"


@pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to execute the extracted JS"
)
def test_pill_state_for_card_is_waiting_once_the_grace_window_elapses():
    source = _page()
    fn = _function(source, "function pillStateForCard(live, nowMs, connectingUntilMs)")

    out = _run_node(f"{fn}\nconsole.log(pillStateForCard(false, 9000, 5000));")
    assert out == "waiting"


@pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required to execute the extracted JS"
)
def test_pill_state_for_card_treats_the_boundary_as_expired():
    """now == connectingUntilMs: the grace window has just elapsed, not still open."""
    source = _page()
    fn = _function(source, "function pillStateForCard(live, nowMs, connectingUntilMs)")

    out = _run_node(f"{fn}\nconsole.log(pillStateForCard(false, 5000, 5000));")
    assert out == "waiting"


# -- 2. updateBindingCardLeaves actually calls the helper --------------------


def test_update_binding_card_leaves_calls_pill_state_for_card():
    source = _page()
    fn = _function(source, "function updateBindingCardLeaves()")

    assert "pillStateForCard(" in fn
    # The old binary expression must be gone, not just supplemented -- a
    # leftover copy would satisfy a naive substring check while the pill
    # logic quietly stayed binary.
    assert 't("monitor.live") : t("monitor.waiting")' not in fn


def test_update_binding_card_leaves_reuses_the_existing_connecting_key():
    source = _page()
    fn = _function(source, "function updateBindingCardLeaves()")

    assert '"pairing.connecting"' in fn


def test_update_binding_card_leaves_calls_pill_state_for_card_with_the_real_clock():
    """The call site must pass the live wall clock, not a frozen/injected
    value -- a "connecting" pill that never re-evaluates against Date.now()
    would sit permanently optimistic, which is the one failure mode this
    feature exists to avoid."""
    source = _page()
    fn = _function(source, "function updateBindingCardLeaves()")

    assert "pillStateForCard(live, Date.now(), pairingConnectingUntilMs)" in fn


# -- 3. finishPairing arms the grace window before returning home -----------


def test_finish_pairing_arms_the_grace_window_before_going_home():
    source = _page()
    fn = _function(source, "async function finishPairing()")

    grace_index = fn.index(
        "pairingConnectingUntilMs = Date.now() + PAIRING_CONNECTING_GRACE_MS;"
    )
    go_home_index = fn.index("await goHome();")
    assert grace_index < go_home_index

    # Must be armed regardless of whether the fetch succeeded or threw --
    # the runtime restart happens server-side either way.
    finally_index = fn.index("} finally {")
    assert finally_index < grace_index


# -- 4. locale hint text ------------------------------------------------------


def test_locale_files_carry_the_updated_no_signal_hint():
    locales_dir = Path(edge_app_module.__file__).resolve().parent.parent / "locales"
    en = json.loads((locales_dir / "en.json").read_text(encoding="utf-8"))
    zh_tw = json.loads((locales_dir / "zh_tw.json").read_text(encoding="utf-8"))

    assert en["operator.card_last_signal_never"] == "No signal yet — pedal the machine"
    assert zh_tw["operator.card_last_signal_never"] == "尚未收到訊號 · 請踩動器材"

    # Every other key must stay untouched.
    assert set(en.keys()) == set(zh_tw.keys())
