import os
import subprocess
import sys
import textwrap

import pytest

from edge_node.infrastructure.antenna.port_lock import (
    PortBusyError,
    lock_path_for_port,
    port_lock,
)


def test_second_acquire_from_same_process_raises_port_busy_error():
    port = "/dev/ttyFAKE0"
    with port_lock(port, timeout_sec=5.0):
        with pytest.raises(PortBusyError, match="ttyFAKE0"):
            with port_lock(port, timeout_sec=0.2):
                pass  # pragma: no cover - must not be reached


def test_lock_is_released_after_context_exits():
    port = "/dev/ttyFAKE1"
    with port_lock(port, timeout_sec=5.0):
        pass

    # second acquire must succeed promptly now that the first is released
    with port_lock(port, timeout_sec=0.2):
        pass


def test_different_ports_map_to_different_lock_files():
    path_a = lock_path_for_port("/dev/ttyAMA0")
    path_b = lock_path_for_port("/dev/ttyAMA4")

    assert path_a != path_b
    assert path_a.name == "_dev_ttyAMA0.lock"
    assert path_b.name == "_dev_ttyAMA4.lock"


def test_same_port_maps_to_same_lock_file_from_both_call_sites():
    # simulate command_runner deriving the path from request.port and the
    # manager deriving it from AntennaChannelConfig.port -- both just pass
    # the same port string through this same helper.
    from_command_runner = lock_path_for_port("/dev/ttyAMA0")
    from_manager = lock_path_for_port("/dev/ttyAMA0")

    assert from_command_runner == from_manager


def test_cross_process_lock_conflict_and_release(tmp_path, monkeypatch):
    lock_dir = tmp_path / "uart-locks"
    monkeypatch.setenv("FITRACE_UART_LOCK_DIR", str(lock_dir))

    child_script = textwrap.dedent("""
        import time
        from edge_node.infrastructure.antenna.port_lock import port_lock

        with port_lock("/dev/ttyAMA0", timeout_sec=5.0):
            time.sleep(1.5)
        """)

    child_env = dict(os.environ)
    child_env["FITRACE_UART_LOCK_DIR"] = str(lock_dir)

    child = subprocess.Popen(
        [sys.executable, "-c", child_script],
        cwd=str(_repo_root()),
        env=child_env,
    )
    try:
        _wait_for_child_to_hold_lock(lock_dir, "/dev/ttyAMA0")

        with pytest.raises(PortBusyError):
            with port_lock("/dev/ttyAMA0", timeout_sec=0.3):
                pass  # pragma: no cover - must not be reached

        child.wait(timeout=5)

        # child released the lock on exit; parent can now acquire it
        with port_lock("/dev/ttyAMA0", timeout_sec=2.0):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def _repo_root():
    import pathlib

    return pathlib.Path(__file__).resolve().parents[3]


def _wait_for_child_to_hold_lock(lock_dir, port, timeout_sec: float = 3.0):
    """Poll until the child process has actually acquired the lock, instead
    of a fixed sleep that could race the child's startup time."""
    import time as _time

    from edge_node.infrastructure.antenna.port_lock import lock_path_for_port

    deadline = _time.monotonic() + timeout_sec
    lock_path = lock_dir / lock_path_for_port(port).name
    while _time.monotonic() < deadline:
        try:
            with port_lock(port, timeout_sec=0.05):
                pass
        except PortBusyError:
            return
        _time.sleep(0.02)
    raise AssertionError(f"child never acquired the lock at {lock_path}")
