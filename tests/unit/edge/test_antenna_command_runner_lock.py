import sys
import threading

import pytest

from edge_node.infrastructure.antenna.command_runner import (
    AntennaCommandRequest,
    AntennaCommandRunner,
)


class RecordingSerial:
    """Fake serial.Serial that records concurrent-open ordering and open kwargs.

    Two browser tabs hitting the sync FastAPI endpoints can call
    AntennaCommandRunner.run() from two different threadpool threads at once.
    Without a lock held for the whole open->command->read->close session, both
    threads' Serial() opens could be active at the same time and interleave
    writes on the same UART. sleep() in __init__ widens the race window so a
    missing lock reliably shows up as max_active_seen > 1.
    """

    instances: list["RecordingSerial"] = []
    active_count = 0
    max_active_seen = 0
    _class_lock = threading.Lock()

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_open = True
        self._delivered = False
        RecordingSerial.instances.append(self)
        with RecordingSerial._class_lock:
            RecordingSerial.active_count += 1
            RecordingSerial.max_active_seen = max(
                RecordingSerial.max_active_seen, RecordingSerial.active_count
            )
        threading.Event().wait(0.05)

    def write(self, data):
        pass

    def read(self, size):
        if not self._delivered:
            self._delivered = True
            return b"BOOT:NO_LIST;\r\n"
        return b""

    def close(self):
        with RecordingSerial._class_lock:
            RecordingSerial.active_count -= 1
        self.is_open = False


class FakeSerialModule:
    def __init__(self, serial_cls):
        self.Serial = serial_cls
        self.SerialException = Exception


@pytest.fixture
def fake_serial(monkeypatch):
    RecordingSerial.instances = []
    RecordingSerial.active_count = 0
    RecordingSerial.max_active_seen = 0
    module = FakeSerialModule(RecordingSerial)
    monkeypatch.setitem(sys.modules, "serial", module)
    return module


def test_concurrent_run_calls_serialize_over_the_whole_serial_session(fake_serial):
    runner = AntennaCommandRunner()
    barrier = threading.Barrier(2)

    def worker(port):
        barrier.wait()
        runner.run(AntennaCommandRequest(port=port, command="ping", timeout_sec=0.05))

    threads = [
        threading.Thread(target=worker, args=(f"/dev/fake{i}",)) for i in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert RecordingSerial.max_active_seen == 1
    assert len(RecordingSerial.instances) == 2


def test_run_opens_serial_with_exclusive_true(fake_serial):
    runner = AntennaCommandRunner()

    runner.run(
        AntennaCommandRequest(port="/dev/fake0", command="ping", timeout_sec=0.05)
    )

    assert RecordingSerial.instances[-1].kwargs.get("exclusive") is True


def test_serial_open_failure_is_still_reported_as_runtime_error(monkeypatch):
    class ExplodingSerialModule:
        class SerialException(Exception):
            pass

        @staticmethod
        def Serial(**kwargs):
            raise ExplodingSerialModule.SerialException("port already in use")

    monkeypatch.setitem(sys.modules, "serial", ExplodingSerialModule)
    runner = AntennaCommandRunner()

    with pytest.raises(RuntimeError, match="UART connection failed"):
        runner.run(
            AntennaCommandRequest(port="/dev/fake0", command="ping", timeout_sec=0.05)
        )
