"""The pairing scan runs behind the page, not in front of it.

A watch keeps its pairing screen open for 58 seconds. The scan used to hold
the POST open for ~20s behind a modal, so a third of that budget was gone
before the operator could type anything -- and a scan that missed the watch
cost another 20s, which does not fit at all.

Now start() returns immediately and the existing 1s status poll fills the
worklist as each channel reports, so the operator can bind the watch the
moment it appears. Rescan/cancel stay disabled until the scan ends: unlike
bind, they tear down a session the scan thread is still using.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from edge_node.infrastructure.fastapi import app as edge_app_module


def _page() -> str:
    return TestClient(edge_app_module.app).get("/").text


def _function(source: str, signature: str) -> str:
    """Slice one function out of the page. Stops at the next declaration of
    either kind -- the helpers here sit next to async ones."""
    start = source.index(signature)
    ends = [
        index
        for index in (
            source.find("\n    function ", start + 1),
            source.find("\n    async function ", start + 1),
        )
        if index != -1
    ]
    return source[start : min(ends)]


def test_no_modal_blocks_the_operator_during_a_scan():
    source = _page()
    assert (
        "pairing-scan-overlay" not in source
    ), "the scan modal is back -- it blocks the worklist for the whole scan"


def test_the_scan_indicator_is_the_inline_message_line():
    source = _page()
    fn = _function(source, "function showPairingScanning(active)")

    assert 'getElementById("pairing-scanning")' in fn
    assert "batch-progress-overlay" not in fn


def test_start_returns_to_a_usable_worklist_and_lets_the_poll_fill_it():
    source = _page()
    fn = _function(source, "async function startPairing()")

    assert "startPairingPoll();" in fn
    # start() answers before the scan finishes now, so the response carries
    # no candidates to seed with -- the poll does the filling.
    assert "initPairingWorklist([])" in fn


def test_the_poll_adds_candidates_as_channels_report_them():
    source = _page()
    fn = _function(source, "async function pollPairingStatus()")

    assert "addPairingCandidates(" in fn

    adder = _function(source, "function addPairingCandidates(candidates)")
    assert "pairingRowMeta.has(" in adder, "existing rows must not be reset"
    assert "pairingRowMeta.set(" in adder


def test_an_operator_edit_survives_a_candidate_arriving_mid_scan():
    """Rows carry the type and name the operator is typing; a later channel
    reporting must add rows, never rebuild them."""
    source = _page()
    adder = _function(source, "function addPairingCandidates(candidates)")

    assert "initPairingWorklist(" not in adder
    assert "pairingRowMeta = new Map()" not in adder


def test_rescan_and_cancel_stay_disabled_until_the_scan_ends():
    source = _page()
    controls = _function(source, "function setPairingScanControls(scanning)")

    assert 'getElementById("pairing-rescan-btn")' in controls
    assert 'getElementById("pairing-cancel-btn")' in controls

    poll = _function(source, "async function pollPairingStatus()")
    assert "setPairingScanControls(" in poll


def test_the_scan_indicator_clears_when_the_session_stops_scanning():
    source = _page()
    poll = _function(source, "async function pollPairingStatus()")

    assert 'payload.state === "scanning"' in poll
    assert "showPairingScanning(" in poll


def test_scan_progress_keys_exist_in_both_locales():
    locales_dir = Path(edge_app_module.__file__).resolve().parent.parent / "locales"
    en = json.loads((locales_dir / "en.json").read_text(encoding="utf-8"))
    zh_tw = json.loads((locales_dir / "zh_tw.json").read_text(encoding="utf-8"))

    for key in ("pairing.scanning", "pairing.scanning_elapsed"):
        assert key in en, key
        assert key in zh_tw, key
    assert set(en.keys()) == set(zh_tw.keys())


def test_the_scanning_copy_no_longer_tells_the_operator_to_wait():
    """The old string promised "20-40 seconds" of waiting, which is the
    behaviour this change removes."""
    locales_dir = Path(edge_app_module.__file__).resolve().parent.parent / "locales"
    en = json.loads((locales_dir / "en.json").read_text(encoding="utf-8"))

    assert "20-40" not in en["pairing.scanning"]
