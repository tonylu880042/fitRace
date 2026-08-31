"""The scan must not hold the operator hostage.

A watch advertises for 58 seconds and then closes its pairing screen. The
scan used to run inside the POST, behind a modal, so ~20s of that budget was
gone before the operator could touch anything -- and a scan that missed the
watch cost another 20s, which does not fit.

So start() returns as soon as the session exists, the scan runs behind it,
and every candidate becomes bindable the moment its channel reports it.
`scan_executor` is the seam: production hands the work to a thread, tests run
it inline (see make_harness) so the rest of the suite keeps its
straight-line reads.
"""

from tests.unit.edge.test_pairing_session import device, make_config, make_harness


class DeferredExecutor:
    """Holds the scan instead of running it, so a test can look at the
    session mid-scan -- which is exactly what the operator now does."""

    def __init__(self):
        self.pending = []

    def __call__(self, run_scan):
        self.pending.append(run_scan)

    def run_next(self):
        self.pending.pop(0)()


def _harness(tmp_path, executor, **kwargs):
    return make_harness(
        make_config(),
        flag_path=tmp_path / "pairing.flag",
        scan_executor=executor,
        **kwargs,
    )


def test_start_returns_before_the_scan_has_run(tmp_path):
    executor = DeferredExecutor()
    harness = _harness(
        tmp_path,
        executor,
        scan_results_by_port={
            "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:01", -55, "Watch")],
        },
    )

    result = harness.session.start(temp_connect=False)

    assert result["session_id"]
    assert result["candidates"] == []
    assert harness.session.state == "scanning"
    assert harness.runner.calls == [], "the scan ran inside the request"


def test_the_scan_fills_the_session_in_the_background(tmp_path):
    executor = DeferredExecutor()
    harness = _harness(
        tmp_path,
        executor,
        scan_results_by_port={
            "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:01", -55, "Watch")],
        },
    )
    harness.session.start(temp_connect=False)

    executor.run_next()

    assert harness.session.state == "observing"
    macs = [c["mac"] for c in harness.session.status()["candidates"]]
    assert macs == ["AA:BB:CC:DD:EE:01"]


def test_a_channel_publishes_its_finds_before_the_other_channel_is_scanned(
    tmp_path,
):
    """The whole point for a 58-second watch: what uart-1 heard is bindable
    while uart-2 is still listening."""
    seen_mid_scan = {}
    executor = DeferredExecutor()
    harness = _harness(
        tmp_path,
        executor,
        scan_results_by_port={
            "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:01", -55, "Watch")],
            "/dev/ttyAMA4": [device("AA:BB:CC:DD:EE:02", -70, "Bike")],
        },
    )
    session = harness.session

    original_run = harness.runner.run

    def run(request):
        if request.command == "scan" and request.port == "/dev/ttyAMA4":
            seen_mid_scan["candidates"] = [
                c["mac"] for c in session.status()["candidates"]
            ]
        return original_run(request)

    harness.runner.run = run

    session.start(temp_connect=False)
    executor.run_next()

    assert seen_mid_scan["candidates"] == ["AA:BB:CC:DD:EE:01"]


def test_a_second_start_while_scanning_does_not_scan_again(tmp_path):
    executor = DeferredExecutor()
    harness = _harness(
        tmp_path,
        executor,
        scan_results_by_port={
            "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:01", -55, "Watch")],
        },
    )

    first = harness.session.start(temp_connect=False)
    second = harness.session.start(temp_connect=False)

    assert second["session_id"] == first["session_id"]
    assert len(executor.pending) == 1


def test_a_candidate_can_be_bound_while_the_other_channel_is_still_scanning(
    tmp_path,
):
    """Binding is the reason the scan stopped blocking: the operator taps
    the watch the moment it appears, without waiting for the scan to end."""
    executor = DeferredExecutor()
    harness = _harness(
        tmp_path,
        executor,
        scan_results_by_port={
            "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:01", -55, "Watch")],
            "/dev/ttyAMA4": [device("AA:BB:CC:DD:EE:02", -70, "Bike")],
        },
    )
    session = harness.session
    bound = {}

    original_run = harness.runner.run

    def run(request):
        if request.command == "scan" and request.port == "/dev/ttyAMA4":
            bound["result"] = session.bind(
                "AA:BB:CC:DD:EE:01",
                equipment_type="spin_bike",
                display_name="Watch",
            )
        return original_run(request)

    harness.runner.run = run

    session.start(temp_connect=False)
    executor.run_next()

    assert bound["result"]["binding"]["ble_target"] == "AA:BB:CC:DD:EE:01"
    saved = harness.config_holder["config"].equipment_bindings
    assert [b.ble_target for b in saved] == ["AA:BB:CC:DD:EE:01"]


def test_status_reports_scanning_until_the_scan_finishes(tmp_path):
    executor = DeferredExecutor()
    harness = _harness(
        tmp_path,
        executor,
        scan_results_by_port={
            "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:01", -55, "Watch")],
        },
    )

    harness.session.start(temp_connect=False)
    assert harness.session.status()["state"] == "scanning"

    executor.run_next()
    assert harness.session.status()["state"] == "observing"
