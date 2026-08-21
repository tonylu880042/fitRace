"""A command ends when the board answers, not when the timeout expires.

Measured on the venue node: a STATUS the board answered with a single line
took 5.047s, because the read loop always drained the whole timeout window.
That dead air is most of what an operator waits through -- a pairing scan is
two channels of SCAN:START (5s of real discovery) plus SCAN:STOP (5s of
listening to nothing), so the UI sat for ~20s to do ~10s of work.

What must NOT terminate a read:

- FTMS telemetry, which streams continuously on a channel with connected
  machines. Ending on "any line" would cut a command off before its ack.
- DEVICE sightings during SCAN:START -- stopping at the first device found
  would report one machine and hide the rest, which is worse than slow.
"""

import time

import pytest

from edge_node.infrastructure.antenna.command_runner import (
    AntennaCommandRequest,
    AntennaCommandRunner,
)

WINDOW_SEC = 5.0


class ScriptedSerial:
    """Answers each written command with its own batch of lines.

    Per-write batches matter for the scan: SCAN:START and SCAN:STOP are two
    writes with two read windows, and a fake that dumped everything up front
    would make the STOP phase look like silence no matter what the code does.
    """

    batches: list[list[str]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_open = True
        self._queue = list(type(self).batches)
        self._pending = b""

    def write(self, data):
        if self._queue:
            batch = self._queue.pop(0)
            self._pending += "".join(f"{line}\r\n" for line in batch).encode()

    def read(self, size):
        if self._pending:
            chunk, self._pending = self._pending[:size], self._pending[size:]
            return chunk
        time.sleep(0.01)  # keep the poll loop from spinning hot
        return b""

    def close(self):
        self.is_open = False


class FakeSerialModule:
    def __init__(self, serial_cls):
        self.Serial = serial_cls
        self.SerialException = Exception


def _runner(monkeypatch, *batches):
    serial_cls = type("Scripted", (ScriptedSerial,), {"batches": list(batches)})
    runner = AntennaCommandRunner()
    monkeypatch.setattr(
        "edge_node.infrastructure.antenna.command_runner._load_serial",
        lambda: FakeSerialModule(serial_cls),
    )
    return runner


def _run(runner, **kwargs):
    started = time.monotonic()
    result = runner.run(
        AntennaCommandRequest(
            port="/dev/ttyFAKE",
            baudrate=115200,
            timeout_sec=WINDOW_SEC,
            **kwargs,
        )
    )
    return result, time.monotonic() - started


def test_status_returns_as_soon_as_the_board_answers(monkeypatch):
    runner = _runner(monkeypatch, ["STATUS:IDLE,0/2;"])

    result, elapsed = _run(runner, command="status")

    assert elapsed < 1.0, f"waited {elapsed:.1f}s for an answer already received"
    assert result["rx"] == ["STATUS:IDLE,0/2;"]


def test_an_ok_ack_ends_the_read(monkeypatch):
    runner = _runner(monkeypatch, ["REPORT:OK;"])

    result, elapsed = _run(runner, command="report", report_interval_ms=250)

    assert elapsed < 1.0
    assert result["rx"] == ["REPORT:OK;"]


def test_an_error_reply_ends_the_read_too(monkeypatch):
    """A failure must not cost the operator the full window either."""
    runner = _runner(monkeypatch, ["CONNECT:ERROR:BUSY;"])

    _, elapsed = _run(runner, command="connect", macs=["AA:BB:CC:DD:EE:01"])

    assert elapsed < 1.0


def test_telemetry_alone_never_ends_a_read(monkeypatch):
    """The trap: a connected channel streams FTMS frames the whole time, so
    treating any line as the answer would return before the real ack."""
    runner = _runner(
        monkeypatch,
        ['FTMS:AA:BB:CC:DD:EE:01,BIKE,{"rssi":-60,"instantaneous_speed":0.0};'],
    )

    _, elapsed = _run(runner, command="status")

    assert elapsed >= WINDOW_SEC - 0.5, "telemetry was mistaken for the answer"


def test_telemetry_before_the_ack_is_kept_and_the_ack_still_ends_the_read(
    monkeypatch,
):
    runner = _runner(
        monkeypatch,
        [
            'FTMS:AA:BB:CC:DD:EE:01,BIKE,{"rssi":-60,"instantaneous_speed":0.0};',
            "STATUS:SCANNING,1/2;",
        ],
    )

    result, elapsed = _run(runner, command="status")

    assert elapsed < 1.0
    assert len(result["rx"]) == 2
    assert result["rx"][-1] == "STATUS:SCANNING,1/2;"


def test_a_scan_still_listens_for_its_whole_window(monkeypatch):
    """Device sightings arrive as they are heard, and nothing says how many
    are left -- stopping at the first one would hide the rest."""
    runner = _runner(
        monkeypatch,
        ["DEVICE:AA:BB:CC:DD:EE:01,-55,Vmax21112,BIKE;"],  # answers SCAN:START
        [],  # SCAN:STOP goes unanswered
    )

    _, elapsed = _run(runner, command="scan", scan_duration_sec=1.0)

    # 1s of scanning, and the unanswered SCAN:STOP drains its own window:
    # total is at least both windows.
    assert elapsed >= 1.0 + WINDOW_SEC - 0.5


def test_a_scan_stop_ack_ends_the_stop_phase(monkeypatch):
    """The half of a scan that is pure waiting: the board acks SCAN:STOP
    immediately and the read used to sit out the rest of the window."""
    runner = _runner(
        monkeypatch,
        ["DEVICE:AA:BB:CC:DD:EE:01,-55,Vmax21112,BIKE;"],  # answers SCAN:START
        ["SCAN:OK;"],  # answers SCAN:STOP straight away
    )

    _, elapsed = _run(runner, command="scan", scan_duration_sec=1.0)

    assert elapsed < 1.0 + 1.0, f"scan took {elapsed:.1f}s; the stop phase waited"


def test_silence_still_costs_the_full_window(monkeypatch):
    """Unchanged behaviour when the board says nothing at all."""
    runner = _runner(monkeypatch)

    started = time.monotonic()
    runner.run(
        AntennaCommandRequest(
            port="/dev/ttyFAKE", baudrate=115200, timeout_sec=0.4, command="status"
        )
    )
    elapsed = time.monotonic() - started

    assert elapsed >= 0.35


@pytest.mark.parametrize("command", ["ping", "version"])
def test_query_commands_end_on_their_own_reply(monkeypatch, command):
    replies = {"ping": "PING:OK;", "version": "VERSION:1.4.2;"}
    runner = _runner(monkeypatch, [replies[command]])

    _, elapsed = _run(runner, command=command)

    assert elapsed < 1.0
