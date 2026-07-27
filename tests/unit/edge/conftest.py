import pytest


@pytest.fixture(autouse=True)
def isolate_uart_lock_dir(monkeypatch, tmp_path):
    """Point the cross-process UART port lock at a per-test tmp_path.

    Without this, port_lock()'s default "data/uart-locks" is relative to the
    process cwd, which during a test run is the repo root -- every test that
    exercises AntennaCommandRunner or AntennaFtmsManager would otherwise
    leave real lock files behind in the working tree.
    """
    monkeypatch.setenv("FITRACE_UART_LOCK_DIR", str(tmp_path / "uart-locks"))
