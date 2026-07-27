import contextlib
import fcntl
import os
import re
import time
from pathlib import Path

# Both the edge runtime (edge_node.main -> AntennaFtmsManager) and the
# setup-page web service (uvicorn, a SEPARATE process) open the same UART
# device. This module gives them a shared cross-process advisory lock so
# only one of them ever has the port open at a time.
#
# NOTE: flock() is ADVISORY, not mandatory. It only keeps the two of us out
# of each other's way because both cooperating processes go through this
# helper before touching the port; nothing stops a third program from
# opening the device node directly and corrupting the stream anyway.

LOCK_DIR_ENV_VAR = "FITRACE_UART_LOCK_DIR"
# Relative on purpose: both services are started from the same working
# directory, exactly like the existing relative "data/edge_monitor.jsonl"
# event log, so a relative path here already points both processes at the
# same file without any extra configuration.
DEFAULT_LOCK_DIR = "data/uart-locks"
_POLL_INTERVAL_SEC = 0.02
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


class PortBusyError(RuntimeError):
    """Raised when a UART port's cross-process lock could not be acquired
    before the caller's timeout elapsed."""


def lock_path_for_port(port: str) -> Path:
    """Return the lock file path for `port`.

    Both AntennaCommandRunner and AntennaFtmsManager call this (indirectly,
    via port_lock()) with their own port string, so the same physical device
    (e.g. "/dev/ttyAMA0") always resolves to the same lock file regardless
    of which process or code path asks.
    """
    lock_dir = Path(os.environ.get(LOCK_DIR_ENV_VAR, DEFAULT_LOCK_DIR))
    lock_dir.mkdir(parents=True, exist_ok=True)
    file_name = _SAFE_CHARS.sub("_", port)
    return lock_dir / f"{file_name}.lock"


@contextlib.contextmanager
def port_lock(port: str, timeout_sec: float = 20.0):
    """Context manager holding an exclusive advisory lock on `port`.

    Retries acquisition every ~20ms until `timeout_sec` elapses, then raises
    PortBusyError naming the port. Always releases and closes the lock file
    descriptor, even if the caller's body raises.
    """
    path = lock_path_for_port(port)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
    try:
        deadline = time.monotonic() + timeout_sec
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise PortBusyError(
                        f"UART port {port!r} is locked by another process"
                    )
                time.sleep(_POLL_INTERVAL_SEC)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
