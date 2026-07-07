import time
from dataclasses import dataclass, field

from edge_node.infrastructure.antenna import protocol


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

        try:
            serial_port = serial_module.Serial(
                port=request.port,
                baudrate=request.baudrate,
                rtscts=request.rtscts,
                timeout=0.1,
            )
            commands = _build_commands(request)
            for index, command in enumerate(commands):
                serial_port.write(command.encode("ascii"))
                tx.append(command.strip())
                self._record_event("tx", request.port, command.strip())

                if request.command == "scan" and index == 0:
                    rx.extend(self._read_lines(serial_port, request.scan_duration_sec, request.port))
                    continue

                rx.extend(self._read_lines(serial_port, request.timeout_sec, request.port))
        except serial_module.SerialException as exc:
            raise RuntimeError(f"UART connection failed: {exc}") from exc
        finally:
            if serial_port and getattr(serial_port, "is_open", False):
                serial_port.close()

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
