import threading
import time
from dataclasses import dataclass, field

from edge_node.infrastructure.antenna import protocol
from edge_node.infrastructure.antenna.port_lock import PortBusyError, port_lock

# In-process lock: two browser tabs hitting the sync FastAPI endpoints can
# call AntennaCommandRunner.run() from two different threadpool threads at
# once. This keeps their whole open->command->read->close sessions from
# interleaving on the same UART.
_SERIAL_LOCK = threading.Lock()

# How long a web command waits to win the cross-process UART lock away from
# the edge runtime (AntennaFtmsManager) before giving up. The runtime only
# ever holds the port for short per-channel bursts, so this is generous
# headroom, not an expected wait.
PORT_LOCK_TIMEOUT_SEC = 5.0


@dataclass(frozen=True)
class AntennaCommandRequest:
    port: str
    command: str
    baudrate: int = 115200
    rtscts: bool = False
    timeout_sec: float = 5.0
    scan_duration_sec: float = 5.0
    macs: list[str] = field(default_factory=list)
    report_interval_ms: int | None = None
    raw_command: str | None = None


class AntennaCommandRunner:
    def __init__(self, event_log=None):
        self._event_log = event_log

    def run(self, request: AntennaCommandRequest) -> dict:
        serial_module = _load_serial()
        serial_port = None
        tx: list[str] = []
        rx: list[str] = []
        started = time.monotonic()

        # Hold the in-process lock for the whole open->command->read->close
        # session so two concurrent HTTP commands (e.g. two browser tabs)
        # can't interleave writes on the same UART and wedge the antenna
        # board. Inside that, hold the cross-process port lock for the same
        # span so the edge runtime (a separate process) can't open the port
        # underneath us either.
        with _SERIAL_LOCK:
            try:
                with port_lock(request.port, timeout_sec=PORT_LOCK_TIMEOUT_SEC):
                    try:
                        serial_port = serial_module.Serial(
                            port=request.port,
                            baudrate=request.baudrate,
                            rtscts=request.rtscts,
                            timeout=0.1,
                            exclusive=True,
                        )
                        commands = _build_commands(request)
                        for index, command in enumerate(commands):
                            serial_port.write(command.encode("ascii"))
                            tx.append(command.strip())
                            self._record_event("tx", request.port, command.strip())

                            if request.command == "scan" and index == 0:
                                rx.extend(
                                    self._read_lines(
                                        serial_port,
                                        request.scan_duration_sec,
                                        request.port,
                                    )
                                )
                                continue

                            rx.extend(
                                self._read_lines(
                                    serial_port, request.timeout_sec, request.port
                                )
                            )
                    except serial_module.SerialException as exc:
                        raise RuntimeError(f"UART connection failed: {exc}") from exc
                    finally:
                        if serial_port and getattr(serial_port, "is_open", False):
                            serial_port.close()
            except PortBusyError as exc:
                raise RuntimeError(
                    f"UART port {request.port!r} is busy: the edge runtime or "
                    f"another command is using it right now ({exc})"
                ) from exc

        return {
            "port": request.port,
            "baudrate": request.baudrate,
            "rtscts": request.rtscts,
            "command": request.command,
            "elapsed_sec": round(time.monotonic() - started, 3),
            "tx": tx,
            "rx": rx,
            "parsed": [protocol.parse_line(line) for line in rx],
        }

    def _read_lines(self, serial_port, duration_sec: float, port: str) -> list[str]:
        lines = _read_lines(serial_port, duration_sec)
        for line in lines:
            self._record_event("rx", port, line, parsed=protocol.parse_line(line))
        return lines

    def _record_event(self, direction: str, channel: str, message: str, parsed=None):
        if not self._event_log:
            return
        self._event_log.record(
            "uart",
            direction,
            channel=channel,
            message=message,
            parsed=parsed,
        )


def _load_serial():
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("pyserial is not installed on this Edge Node") from exc
    return serial


def _build_commands(request: AntennaCommandRequest) -> list[str]:
    command = request.command
    if command == "ping":
        return [protocol.build_ping()]
    if command == "status":
        return [protocol.build_status()]
    if command == "version":
        return [protocol.build_version()]
    if command == "scan":
        return [protocol.build_scan_start(), protocol.build_scan_stop()]
    if command == "connect":
        return [protocol.build_connect(request.macs)]
    if command == "disconnect_all":
        return [protocol.build_disconnect_all()]
    if command == "report":
        if request.report_interval_ms is None:
            raise ValueError("report_interval_ms is required for report command")
        return [protocol.build_report_interval(request.report_interval_ms)]
    if command == "reboot":
        return [protocol.build_reboot()]
    if command == "raw":
        if request.raw_command is None:
            raise ValueError("raw_command is required for raw command")
        return [protocol.normalize_raw_command(request.raw_command)]
    raise ValueError(f"unsupported antenna command: {command}")


def _read_lines(serial_port, duration_sec: float) -> list[str]:
    deadline = time.monotonic() + max(0.1, duration_sec)
    buffer = b""
    lines: list[str] = []

    while time.monotonic() < deadline:
        chunk = serial_port.read(256)
        if not chunk:
            continue
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            decoded = line.decode("ascii", errors="replace").strip()
            if decoded:
                lines.append(decoded)

    tail = buffer.decode("ascii", errors="replace").strip()
    if tail:
        lines.append(tail)
    return lines
