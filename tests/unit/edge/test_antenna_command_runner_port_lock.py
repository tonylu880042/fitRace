import sys

import pytest

from edge_node.infrastructure.antenna import command_runner as command_runner_module
from edge_node.infrastructure.antenna.command_runner import (
    AntennaCommandRequest,
    AntennaCommandRunner,
)
from edge_node.infrastructure.antenna.port_lock import port_lock


class RecordingSerial:
    instances: list["RecordingSerial"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_open = True
        RecordingSerial.instances.append(self)

    def write(self, data):
        pass

    def read(self, size):
        return b""

    def close(self):
        self.is_open = False


class FakeSerialModule:
    def __init__(self, serial_cls):
        self.Serial = serial_cls
        self.SerialException = Exception


@pytest.fixture
def fake_serial(monkeypatch):
    RecordingSerial.instances = []
    module = FakeSerialModule(RecordingSerial)
    monkeypatch.setitem(sys.modules, "serial", module)
    return module


def test_run_raises_clear_error_when_port_already_locked(fake_serial, monkeypatch):
    # give up quickly instead of the real 5s default so the test is fast
    monkeypatch.setattr(command_runner_module, "PORT_LOCK_TIMEOUT_SEC", 0.1)
    runner = AntennaCommandRunner()
    port = "/dev/ttyAMA0"

    with port_lock(port, timeout_sec=5.0):
        # another holder (simulating the edge runtime) already owns the lock
        with pytest.raises(RuntimeError, match=r"/dev/ttyAMA0"):
            runner.run(
                AntennaCommandRequest(port=port, command="ping", timeout_sec=0.05)
            )

    assert RecordingSerial.instances == []


def test_run_succeeds_once_the_other_holder_releases(fake_serial, monkeypatch):
    monkeypatch.setattr(command_runner_module, "PORT_LOCK_TIMEOUT_SEC", 5.0)
    runner = AntennaCommandRunner()
    port = "/dev/ttyAMA0"

    with port_lock(port, timeout_sec=5.0):
        pass  # released before run() is called

    result = runner.run(
        AntennaCommandRequest(port=port, command="ping", timeout_sec=0.05)
    )

    assert result["port"] == port
    assert len(RecordingSerial.instances) == 1
