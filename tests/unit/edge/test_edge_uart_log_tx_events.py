import sys

from edge_node.infrastructure.antenna.command_runner import (
    AntennaCommandRequest,
    AntennaCommandRunner,
)
from edge_node.usecases.event_log import EdgeEventLog


class FakeSerial:
    """Minimal fake serial.Serial: answers PING with a single BOOT line."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_open = True
        self._delivered = False

    def write(self, data):
        pass

    def read(self, size):
        if not self._delivered:
            self._delivered = True
            return b"BOOT:NO_LIST;\r\n"
        return b""

    def close(self):
        self.is_open = False


class FakeSerialModule:
    Serial = FakeSerial
    SerialException = Exception


def test_command_runner_records_tx_event_without_parsed_field(tmp_path, monkeypatch):
    # Pins the backend contract the display fix depends on: TX uart events are
    # recorded with parsed=None (only RX events get protocol.parse_line
    # results), so the page's renderUartLog filter must not require `parsed`
    # to be truthy or every TX row disappears.
    monkeypatch.setitem(sys.modules, "serial", FakeSerialModule)
    event_log = EdgeEventLog(tmp_path / "edge_monitor.jsonl")
    runner = AntennaCommandRunner(event_log=event_log)

    runner.run(
        AntennaCommandRequest(port="/dev/fake0", command="ping", timeout_sec=0.05)
    )

    events = event_log.list_events(limit=10)
    tx_events = [e for e in events if e["direction"] == "tx"]
    assert len(tx_events) == 1
    assert tx_events[0]["source"] == "uart"
    assert tx_events[0]["message"] == "PING;"
    assert tx_events[0]["parsed"] is None

    rx_events = [e for e in events if e["direction"] == "rx"]
    assert len(rx_events) == 1
    assert rx_events[0]["parsed"] is not None
