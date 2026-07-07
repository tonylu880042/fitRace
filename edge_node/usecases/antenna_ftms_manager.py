import asyncio
import logging
import math
import re
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from edge_node.domain.models import AntennaChannelConfig, EdgeNodeConfig, EquipmentBinding, TelemetryData
from edge_node.infrastructure.antenna import protocol

logger = logging.getLogger("edge_node.antenna_ftms_manager")
MAC_ADDRESS_PATTERN = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


@dataclass(frozen=True)
class ScannedDevice:
    address: str
    rssi: int
    name: str
    device_type: str


class AntennaFtmsManager:
    def __init__(
        self,
        edge_config: EdgeNodeConfig,
        on_telemetry: Callable[[TelemetryData], Awaitable[None]],
        serial_factory=None,
        scan_duration_sec: float = 8.0,
        command_timeout_sec: float = 3.0,
        report_interval_ms: int = 1000,
        rssi_tie_threshold_db: int = 5,
        event_log=None,
    ):
        if not edge_config.antenna_channels:
            raise ValueError("antenna_channels is required for antenna FTMS manager")
        self._edge_config = edge_config
        self._on_telemetry = on_telemetry
        self._serial_factory = serial_factory
        self._scan_duration_sec = scan_duration_sec
        self._command_timeout_sec = command_timeout_sec
        self._report_interval_ms = report_interval_ms
        self._rssi_tie_threshold_db = rssi_tie_threshold_db
        self._stop_event = threading.Event()
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._serials: dict[str, Any] = {}
        self._bindings_by_mac: dict[str, EquipmentBinding] = {}
        self._next_binding_index_by_channel: dict[str, int] = {}
        self._event_log = event_log

    async def start(self):
        self._loop = asyncio.get_running_loop()
        self._stop_event.clear()
        self._task = asyncio.create_task(asyncio.to_thread(self._run))

    async def stop(self):
        self._stop_event.set()
        for serial_port in list(self._serials.values()):
            try:
                serial_port.close()
            except Exception:
                pass
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _run(self):
        self._serials = {
            channel.id: self._open_serial(channel)
            for channel in self._edge_config.antenna_channels
        }
        try:
            boot_has_list = self._ping_channels()
            scan_results = self._scan_channels()
            assignments = assign_devices_by_rssi(
                scan_results,
                self._edge_config.antenna_channels,
                tie_threshold_db=self._rssi_tie_threshold_db,
            )
            assignments = filter_assignments_to_configured_macs(
                assignments,
                self._edge_config.equipment_bindings,
            )
            if any(assignments.values()):
                self._bindings_by_mac = bind_assignments_to_streams(
                    assignments,
                    self._edge_config.equipment_bindings,
                    self._edge_config.node_id,
                )
                self._disconnect_all_channels()
                self._connect_assignments(assignments)
            elif boot_has_list and all(boot_has_list.values()):
                logger.warning(
                    "Antenna scan found no configured targets; using saved target lists"
                )
                self._set_report_interval_all()
            else:
                logger.warning("Antenna scan found no configured targets")
            self._read_telemetry_loop()
        finally:
            for serial_port in self._serials.values():
                try:
                    serial_port.close()
                except Exception:
                    pass

    def _open_serial(self, channel: AntennaChannelConfig):
        if self._serial_factory:
            return self._serial_factory(channel)
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is not installed on this Edge Node") from exc
        return serial.Serial(
            port=channel.port,
            baudrate=channel.baudrate,
            rtscts=channel.rtscts,
            timeout=0.1,
        )

    def _ping_channels(self) -> dict[str, bool]:
        boot_has_list: dict[str, bool] = {}
        for channel_id, serial_port in self._serials.items():
            self._write(serial_port, protocol.build_ping(), channel_id)
            boot_lines = self._read_lines(
                serial_port,
                self._command_timeout_sec,
                max_lines=1,
                channel_id=channel_id,
            )
            logger.info("[%s] antenna boot response: %s", channel_id, boot_lines)
            parsed = protocol.parse_line(boot_lines[0]) if boot_lines else {}
            boot_has_list[channel_id] = bool(parsed.get("type") == "boot" and parsed.get("has_list"))
        return boot_has_list

    def _scan_channels(self) -> dict[str, list[ScannedDevice]]:
        for channel_id, serial_port in self._serials.items():
            self._write(serial_port, protocol.build_scan_start(), channel_id)

        scan_results = {channel_id: [] for channel_id in self._serials}
        deadline = time.monotonic() + max(0.1, self._scan_duration_sec)
        while time.monotonic() < deadline and not self._stop_event.is_set():
            for channel_id, serial_port in self._serials.items():
                line = self._read_line(serial_port, channel_id)
                if not line:
                    continue
                parsed = protocol.parse_line(line)
                if parsed.get("type") == "device" and parsed.get("rssi") is not None:
                    scan_results[channel_id].append(
                        ScannedDevice(
                            address=parsed["address"],
                            rssi=int(parsed["rssi"]),
                            name=parsed.get("name") or "",
                            device_type=parsed.get("device_type") or "UNKNOWN",
                        )
                    )
        for channel_id, devices in scan_results.items():
            logger.info("[%s] antenna scan found %s device(s)", channel_id, len(devices))

        for channel_id, serial_port in self._serials.items():
            self._write(serial_port, protocol.build_scan_stop(), channel_id)
        for channel_id, serial_port in self._serials.items():
            self._read_lines(serial_port, self._command_timeout_sec, channel_id=channel_id)
        return scan_results

    def _set_report_interval_all(self):
        for channel_id, serial_port in self._serials.items():
            self._write(serial_port, protocol.build_report_interval(self._report_interval_ms), channel_id)
            lines = self._read_lines(
                serial_port,
                self._command_timeout_sec,
                max_lines=1,
                channel_id=channel_id,
            )
            logger.info("[%s] antenna report interval response: %s", channel_id, lines)

    def _disconnect_all_channels(self):
        for channel_id, serial_port in self._serials.items():
            self._write(serial_port, protocol.build_disconnect_all(), channel_id)
            lines = self._read_lines(
                serial_port,
                self._command_timeout_sec,
                max_lines=1,
                channel_id=channel_id,
            )
            logger.info("[%s] antenna disconnect all response: %s", channel_id, lines)

    def _connect_assignments(self, assignments: dict[str, list[str]]):
        for channel_id, macs in assignments.items():
            if not macs:
                logger.warning("[%s] no antenna devices assigned after scan", channel_id)
                continue
            serial_port = self._serials[channel_id]
            self._write(serial_port, protocol.build_connect(macs), channel_id)
            connect_lines = self._read_lines(
                serial_port,
                self._command_timeout_sec,
                max_lines=1,
                channel_id=channel_id,
            )
            logger.info("[%s] antenna connect %s -> %s", channel_id, macs, connect_lines)
            self._write(serial_port, protocol.build_report_interval(self._report_interval_ms), channel_id)
            self._read_lines(
                serial_port,
                self._command_timeout_sec,
                max_lines=1,
                channel_id=channel_id,
            )

    def _read_telemetry_loop(self):
        while not self._stop_event.is_set():
            for channel_id, serial_port in self._serials.items():
                line = self._read_line(serial_port, channel_id)
                if not line:
                    continue
                parsed = protocol.parse_line(line)
                if parsed.get("type") != "telemetry":
                    continue
                telemetry = self._to_telemetry(channel_id, parsed)
                if not self._loop:
                    continue
                asyncio.run_coroutine_threadsafe(self._on_telemetry(telemetry), self._loop)

    def _to_telemetry(self, channel_id: str, parsed: dict[str, Any]) -> TelemetryData:
        mac = parsed["address"]
        binding = self._binding_for_mac(channel_id, mac)
        equipment_type = parsed.get("equipment_type") or "unknown"
        return TelemetryData(
            node_id=binding.node_id if binding else f"{self._edge_config.node_id}-{mac.replace(':', '').lower()}",
            edge_node_id=self._edge_config.node_id,
            mac_address=mac,
            equipment_id=binding.equipment_id if binding else mac,
            equipment_type=binding.equipment_type if binding else equipment_type,
            ftms_type=parsed.get("ftms_type") or parsed.get("device_type"),
            rssi=parsed.get("rssi"),
            instantaneous_speed_kph=float(parsed.get("instantaneous_speed_kph") or 0.0),
            cadence_rpm=int(round(float(parsed.get("cadence_rpm") or 0))),
            pace_sec_per_500m=parsed.get("pace_sec_per_500m"),
            power_watts=int(parsed.get("power_watts") or 0),
            heart_rate_bpm=0,
            distance_m=float(parsed.get("distance_m") or 0.0),
            total_energy_kcal=parsed.get("total_energy_kcal"),
            calories=parsed.get("total_energy_kcal"),
            elapsed_time_ms=0,
            timestamp_epoch_ms=int(time.time() * 1000),
            ftms_payload=parsed.get("ftms_payload"),
            raw_payload=parsed.get("raw_payload") or parsed.get("payload"),
        )

    def _binding_for_mac(self, channel_id: str, mac: str) -> EquipmentBinding | None:
        binding = self._bindings_by_mac.get(mac)
        if binding:
            return binding

        channel_bindings = [
            binding
            for binding in self._edge_config.equipment_bindings
            if binding.antenna_channel == channel_id
        ]
        for candidate in channel_bindings:
            if _normalize_device_id(candidate.ble_target) == _normalize_device_id(mac):
                self._bindings_by_mac[mac] = candidate
                logger.info("[%s] matched antenna target %s to %s", channel_id, mac, candidate.node_id)
                return candidate

        used_node_ids = {
            binding.node_id for binding in self._bindings_by_mac.values()
        }
        start_index = self._next_binding_index_by_channel.get(channel_id, 0)
        for index in range(start_index, len(channel_bindings)):
            candidate = channel_bindings[index]
            if candidate.node_id in used_node_ids:
                continue
            self._bindings_by_mac[mac] = candidate
            self._next_binding_index_by_channel[channel_id] = index + 1
            logger.info("[%s] assigned saved antenna target %s to %s", channel_id, mac, candidate.node_id)
            return candidate
        return None

    def _write(self, serial_port, command: str, channel_id: str | None = None):
        serial_port.write(command.encode("ascii"))
        self._record_uart_event("tx", channel_id, command.strip())

    def _read_lines(
        self,
        serial_port,
        duration_sec: float,
        max_lines: int | None = None,
        channel_id: str | None = None,
    ) -> list[str]:
        deadline = time.monotonic() + max(0.1, duration_sec)
        lines: list[str] = []
        while time.monotonic() < deadline and not self._stop_event.is_set():
            line = self._read_line(serial_port, channel_id)
            if line:
                lines.append(line)
                if max_lines is not None and len(lines) >= max_lines:
                    break
        return lines

    def _read_line(self, serial_port, channel_id: str | None = None) -> str | None:
        if hasattr(serial_port, "readline"):
            raw = serial_port.readline()
        else:
            raw = b""
        if not raw:
            return None
        if isinstance(raw, bytes):
            line = raw.decode("ascii", errors="replace").strip()
        else:
            line = str(raw).strip()
        if line:
            self._record_uart_event(
                "rx",
                channel_id,
                line,
                parsed=protocol.parse_line(line),
            )
        return line

    def _record_uart_event(
        self,
        direction: str,
        channel_id: str | None,
        message: str,
        parsed: dict[str, Any] | None = None,
    ):
        if not self._event_log:
            return
        self._event_log.record(
            "uart",
            direction,
            channel=channel_id,
            message=message,
            parsed=parsed,
        )


def assign_devices_by_rssi(
    scan_results: dict[str, list[ScannedDevice]],
    channels: list[AntennaChannelConfig],
    tie_threshold_db: int = 5,
) -> dict[str, list[str]]:
    channel_ids = [channel.id for channel in channels]
    max_per_channel = max(1, math.ceil(_unique_device_count(scan_results) / len(channel_ids)))
    assignments = {channel_id: [] for channel_id in channel_ids}
    rssi_by_mac: dict[str, dict[str, int]] = {}
    for channel_id, devices in scan_results.items():
        for device in devices:
            rssi_by_mac.setdefault(device.address, {})[channel_id] = device.rssi

    candidates = sorted(
        rssi_by_mac.items(),
        key=lambda item: max(item[1].values()),
        reverse=True,
    )
    for mac, readings in candidates:
        visible_channels = [channel_id for channel_id in channel_ids if channel_id in readings]
        if not visible_channels:
            continue
        available = [
            channel_id for channel_id in visible_channels
            if len(assignments[channel_id]) < max_per_channel
        ] or visible_channels
        best_rssi = max(readings[channel_id] for channel_id in available)
        close = [
            channel_id for channel_id in available
            if abs(readings[channel_id] - best_rssi) < tie_threshold_db
        ]
        winner = min(close, key=lambda channel_id: (len(assignments[channel_id]), channel_ids.index(channel_id)))
        assignments[winner].append(mac)
    return assignments


def filter_assignments_to_configured_macs(
    assignments: dict[str, list[str]],
    bindings: list[EquipmentBinding],
) -> dict[str, list[str]]:
    configured_macs = {
        _normalize_device_id(binding.ble_target)
        for binding in bindings
        if MAC_ADDRESS_PATTERN.match(binding.ble_target)
    }
    if not configured_macs:
        return assignments
    return {
        channel_id: [
            mac for mac in macs if _normalize_device_id(mac) in configured_macs
        ]
        for channel_id, macs in assignments.items()
    }


def bind_assignments_to_streams(
    assignments: dict[str, list[str]],
    bindings: list[EquipmentBinding],
    edge_node_id: str,
) -> dict[str, EquipmentBinding]:
    result: dict[str, EquipmentBinding] = {}
    bindings_by_target = {
        _normalize_device_id(binding.ble_target): binding
        for binding in bindings
        if binding.ble_target
    }
    bindings_by_channel: dict[str | None, list[EquipmentBinding]] = {}
    for binding in bindings:
        bindings_by_channel.setdefault(binding.antenna_channel, []).append(binding)

    fallback_index = 1
    for channel_id, macs in assignments.items():
        channel_bindings = list(bindings_by_channel.get(channel_id, []))
        for index, mac in enumerate(macs):
            matched_binding = bindings_by_target.get(_normalize_device_id(mac))
            if matched_binding:
                result[mac] = matched_binding
            elif index < len(channel_bindings):
                result[mac] = channel_bindings[index]
            else:
                result[mac] = EquipmentBinding(
                    node_id=f"{edge_node_id}-antenna-{fallback_index:02d}",
                    equipment_id=mac,
                    equipment_type="unknown",
                    ble_target=mac,
                    antenna_channel=channel_id,
                )
                fallback_index += 1
    return result


def _unique_device_count(scan_results: dict[str, list[ScannedDevice]]) -> int:
    return len({device.address for devices in scan_results.values() for device in devices})


def _normalize_device_id(value: str | None) -> str:
    return (value or "").strip().upper()
