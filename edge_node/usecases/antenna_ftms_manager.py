import asyncio
import logging
import math
import re
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from edge_node.domain.models import (
    AntennaChannelConfig,
    EdgeNodeConfig,
    EquipmentBinding,
    TelemetryData,
)
from edge_node.infrastructure.antenna import protocol
from edge_node.infrastructure.antenna.port_lock import PortBusyError, port_lock
from edge_node.usecases.pairing_session import is_pairing_active

logger = logging.getLogger("edge_node.antenna_ftms_manager")
MAC_ADDRESS_PATTERN = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
# antenna board firmware hard limit: CONNECT silently ignores MACs beyond 3
MAX_MACS_PER_CHANNEL = 3

# Three-valued PING/BOOT outcome per channel. The antenna board's NVS target
# list is the source of truth at startup, and the three states drive very
# different (and differently risky) actions:
#   HAS_LIST   -- board already holds a saved target list and reconnects it
#                 on its own; the runtime must not disturb it.
#   NO_LIST    -- board explicitly has no saved list. NVS survives a board
#                 power-cycle, so this only ever means an operator just ran
#                 DISCONNECT:ALL or the board is fresh/unconfigured -- a
#                 human is already present, so the runtime waits for them to
#                 start a pairing scan rather than scanning/connecting on
#                 its own. This is also the only state allowed to drop that
#                 channel's config.json bindings (see _clear_no_list_channel_bindings).
#   NO_ANSWER  -- board never answered PING (unplugged, wedged, or a busy
#                 port after 3 retries). Must NEVER be treated like NO_LIST:
#                 doing so could wipe a venue's config.json bindings just
#                 because a board failed to respond in time.
BOOT_HAS_LIST = "has_list"
BOOT_NO_LIST = "no_list"
BOOT_NO_ANSWER = "no_answer"

# CONNECT is destructive on the nRF52832 antenna board: firmware always does
# a full disconnect-all, then scans and reconnects the given MAC list from
# scratch -- it is a "reset target list and reconnect everything" command,
# never an incremental add. Each connection takes ~10-20s to establish, so
# reissuing CONNECT while a previous push is still converging tears down
# every in-progress link and restarts the whole process. This cooldown gives
# the board a full window to finish before the watchdog is allowed to push
# the same list again.
CONNECT_COOLDOWN_SEC = 90.0

# Cross-process UART lock (shared with AntennaCommandRunner, the setup-page
# web service). Startup command sequences (PING, SCAN, CONNECT, ...) run
# once and can afford to wait a while for a web command to finish with the
# port.
PORT_LOCK_TIMEOUT_SEC = 10.0
# The telemetry read loop must not stall waiting for the lock -- if it's
# busy, skip this channel for one pass rather than block the whole loop.
READ_LOOP_LOCK_TIMEOUT_SEC = 0.2
# Gap between telemetry read passes, held with NO lock at all. This is what
# actually lets a waiting web command (e.g. a SCAN from the setup page) win
# the cross-process lock -- without it the runtime would re-acquire the
# port continuously and starve the web process.
READ_LOOP_YIELD_SEC = 0.03


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
        command_timeout_sec: float = 5.0,
        report_interval_ms: int = 250,
        rssi_tie_threshold_db: int = 5,
        reconnect_interval_sec: float = 30.0,
        data_timeout_sec: float = 10.0,
        event_log=None,
        clock: Callable[[], float] = time.monotonic,
        config_loader: Callable[[], EdgeNodeConfig] | None = None,
        config_saver: Callable[[EdgeNodeConfig], None] | None = None,
    ):
        if not edge_config.antenna_channels:
            raise ValueError("antenna_channels is required for antenna FTMS manager")
        self._edge_config = edge_config
        self._channels_by_id: dict[str, AntennaChannelConfig] = {
            channel.id: channel for channel in edge_config.antenna_channels
        }
        self._on_telemetry = on_telemetry
        self._serial_factory = serial_factory
        self._scan_duration_sec = scan_duration_sec
        self._command_timeout_sec = command_timeout_sec
        self._report_interval_ms = report_interval_ms
        self._rssi_tie_threshold_db = rssi_tie_threshold_db
        self._reconnect_interval_sec = reconnect_interval_sec
        self._data_timeout_sec = data_timeout_sec
        self._clock = clock
        self._config_loader = config_loader
        self._config_saver = config_saver
        self._last_data_by_mac: dict[str, float] = {}
        self._last_raw_distance_by_mac: dict[str, float] = {}
        self._last_raw_energy_by_mac: dict[str, float] = {}
        self._assigned_macs_by_channel: dict[str, set[str]] = {}
        # Per channel: the MAC list (normalized, order-insensitive) most
        # recently pushed via CONNECT, and when -- lets the watchdog tell
        # "board is still converging on what we already sent" apart from
        # "we need to send something new/different".
        self._last_connect_macs_by_channel: dict[str, frozenset[str]] = {}
        self._last_connect_at_by_channel: dict[str, float] = {}
        self._saved_list_channels: set[str] = set()
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
            boot_status = self._ping_channels()
            self._saved_list_channels = {
                channel_id
                for channel_id, status in boot_status.items()
                if status == BOOT_HAS_LIST
            }
            no_list_channels = {
                channel_id
                for channel_id, status in boot_status.items()
                if status == BOOT_NO_LIST
            }
            no_answer_channels = {
                channel_id
                for channel_id, status in boot_status.items()
                if status == BOOT_NO_ANSWER
            }
            if not self._edge_config.equipment_bindings:
                # nothing configured for a scan to ever match, regardless of
                # what boards report -- stay idle until the operator starts
                # a pairing scan themselves
                logger.info(
                    "No equipment bindings configured; staying idle until an "
                    "operator starts a pairing scan"
                )
            else:
                # NVS on the board is the source of truth: a HAS_LIST board
                # auto-reconnects its saved targets on its own, so startup
                # never scans or connects. NO_LIST means either an operator
                # just ran DISCONNECT:ALL or the board is fresh/unconfigured
                # -- either way a human is already present and must start
                # the pairing scan themselves. NO_ANSWER boards are left
                # completely alone; unlike NO_LIST they must never have
                # their config.json bindings cleared (see
                # _clear_no_list_channel_bindings).
                for channel_id in sorted(no_list_channels):
                    logger.info(
                        "[%s] antenna board has no saved target list; "
                        "waiting for an operator to start a pairing scan",
                        channel_id,
                    )
                for channel_id in sorted(no_answer_channels):
                    logger.warning(
                        "[%s] antenna board did not answer PING; leaving "
                        "its configuration untouched",
                        channel_id,
                    )
            if self._saved_list_channels:
                self._set_report_interval_all(self._saved_list_channels)
            self._clear_no_list_channel_bindings(no_list_channels)
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

    def _await_response(
        self,
        serial_port,
        duration_sec: float,
        channel_id: str | None = None,
        ok_command: str | None = None,
        wanted_types: frozenset[str] = frozenset(),
    ) -> dict[str, Any] | None:
        """Read until the response for a specific command arrives; forward
        telemetry, skip stale/unrelated lines so desynced responses can't be
        misread. Per spec, OK lines carry their command prefix and only that
        prefix counts as this command's ack."""
        deadline = time.monotonic() + max(0.1, duration_sec)
        while time.monotonic() < deadline and not self._stop_event.is_set():
            line = self._read_line(serial_port, channel_id)
            if not line:
                continue
            parsed = protocol.parse_line(line)
            kind = parsed.get("type")
            if kind == "telemetry":
                if channel_id:
                    self._dispatch_telemetry(channel_id, parsed)
                continue
            if kind in wanted_types:
                return parsed
            if ok_command and (
                kind == "error"
                or (kind == "ok" and parsed.get("command") == ok_command)
            ):
                return parsed
        return None

    def _ping_channels(self) -> dict[str, str]:
        """PING every channel and classify the BOOT reply.

        Returns one of BOOT_HAS_LIST / BOOT_NO_LIST / BOOT_NO_ANSWER per
        channel in self._serials -- never collapsed into a bool. An
        unplugged or wedged board (no answer after 3 retries, or a busy
        port) must be distinguishable from a board that explicitly replied
        NO_LIST, because only an explicit NO_LIST may ever clear that
        channel's config.json bindings.
        """
        boot_status: dict[str, str] = {}
        for channel_id, serial_port in self._serials.items():
            parsed = None
            try:
                with port_lock(
                    self._channels_by_id[channel_id].port,
                    timeout_sec=PORT_LOCK_TIMEOUT_SEC,
                ):
                    # drop stale lines a previous process left in the UART buffer
                    if hasattr(serial_port, "reset_input_buffer"):
                        serial_port.reset_input_buffer()
                    for _ in range(3):  # spec: resend PING until the board answers BOOT
                        self._write(serial_port, protocol.build_ping(), channel_id)
                        parsed = self._await_response(
                            serial_port,
                            self._command_timeout_sec,
                            channel_id,
                            wanted_types=frozenset({"boot"}),
                        )
                        if parsed or self._stop_event.is_set():
                            break
            except PortBusyError as exc:
                logger.warning(
                    "[%s] antenna PING skipped, port busy: %s", channel_id, exc
                )
            logger.info(
                "[%s] antenna boot response: %s",
                channel_id,
                parsed and parsed.get("raw"),
            )
            if parsed is None:
                boot_status[channel_id] = BOOT_NO_ANSWER
            elif parsed.get("has_list"):
                boot_status[channel_id] = BOOT_HAS_LIST
            else:
                boot_status[channel_id] = BOOT_NO_LIST
        return boot_status

    def _scan_channels(
        self, channel_ids: set[str] | None = None
    ) -> dict[str, list[ScannedDevice]]:
        scanned = {
            channel_id: serial_port
            for channel_id, serial_port in self._serials.items()
            if channel_ids is None or channel_id in channel_ids
        }
        for channel_id, serial_port in scanned.items():
            self._run_locked_channel_op(
                channel_id,
                "scan start",
                lambda serial_port=serial_port, channel_id=channel_id: self._write(
                    serial_port, protocol.build_scan_start(), channel_id
                ),
            )

        scan_results = {channel_id: [] for channel_id in scanned}
        deadline = time.monotonic() + max(0.1, self._scan_duration_sec)
        while time.monotonic() < deadline and not self._stop_event.is_set():
            # read every channel so non-scanned channels keep streaming
            for channel_id, serial_port in self._serials.items():
                line = self._read_line_yielding(serial_port, channel_id)
                if not line:
                    continue
                parsed = protocol.parse_line(line)
                if parsed.get("type") == "telemetry":
                    # keep live streams flowing while a reconnect rescan runs
                    self._dispatch_telemetry(channel_id, parsed)
                    continue
                if (
                    channel_id in scan_results
                    and parsed.get("type") == "device"
                    and parsed.get("rssi") is not None
                ):
                    scan_results[channel_id].append(
                        ScannedDevice(
                            address=parsed["address"],
                            rssi=int(parsed["rssi"]),
                            name=parsed.get("name") or "",
                            device_type=parsed.get("device_type") or "UNKNOWN",
                        )
                    )
        for channel_id, devices in scan_results.items():
            logger.info(
                "[%s] antenna scan found %s device(s)", channel_id, len(devices)
            )

        for channel_id, serial_port in scanned.items():
            self._run_locked_channel_op(
                channel_id,
                "scan stop",
                lambda serial_port=serial_port, channel_id=channel_id: self._write(
                    serial_port, protocol.build_scan_stop(), channel_id
                ),
            )
        for channel_id, serial_port in scanned.items():
            self._run_locked_channel_op(
                channel_id,
                "scan drain",
                lambda serial_port=serial_port, channel_id=channel_id: self._read_lines(
                    serial_port, self._command_timeout_sec, channel_id=channel_id
                ),
            )
        return scan_results

    def _set_report_interval_all(self, channel_ids: set[str] | None = None):
        for channel_id, serial_port in self._serials.items():
            if channel_ids is not None and channel_id not in channel_ids:
                continue
            parsed = None
            try:
                with port_lock(
                    self._channels_by_id[channel_id].port,
                    timeout_sec=PORT_LOCK_TIMEOUT_SEC,
                ):
                    self._write(
                        serial_port,
                        protocol.build_report_interval(self._report_interval_ms),
                        channel_id,
                    )
                    parsed = self._await_response(
                        serial_port,
                        self._command_timeout_sec,
                        channel_id,
                        ok_command="REPORT",
                    )
            except PortBusyError as exc:
                logger.warning(
                    "[%s] antenna REPORT skipped, port busy: %s", channel_id, exc
                )
            logger.info(
                "[%s] antenna report interval response: %s",
                channel_id,
                parsed and parsed.get("raw"),
            )

    def _connect_assignments(self, assignments: dict[str, list[str]]):
        for channel_id, macs in assignments.items():
            if not macs:
                logger.warning(
                    "[%s] no antenna devices assigned after scan", channel_id
                )
                continue
            if len(macs) > MAX_MACS_PER_CHANNEL:
                # keep this channel's configured targets; firmware silently
                # ignores MACs beyond its limit, so a long list loses devices
                configured = {
                    _normalize_device_id(binding.ble_target)
                    for binding in self._edge_config.equipment_bindings
                    if binding.antenna_channel == channel_id and binding.ble_target
                }
                macs = sorted(
                    macs,
                    key=lambda mac: _normalize_device_id(mac) not in configured,
                )
                dropped = macs[MAX_MACS_PER_CHANNEL:]
                macs = macs[:MAX_MACS_PER_CHANNEL]
                logger.warning(
                    "[%s] antenna connect list exceeds board limit %s, dropping %s",
                    channel_id,
                    MAX_MACS_PER_CHANNEL,
                    dropped,
                )
            serial_port = self._serials[channel_id]
            self._assigned_macs_by_channel[channel_id] = {
                _normalize_device_id(mac) for mac in macs
            }
            try:
                with port_lock(
                    self._channels_by_id[channel_id].port,
                    timeout_sec=PORT_LOCK_TIMEOUT_SEC,
                ):
                    self._write(serial_port, protocol.build_connect(macs), channel_id)
                    # Record what was actually pushed only once the write
                    # itself has gone out (not just been attempted): this is
                    # what the watchdog compares against next time, and what
                    # starts its cooldown window.
                    self._last_connect_macs_by_channel[channel_id] = frozenset(
                        _normalize_device_id(mac) for mac in macs
                    )
                    self._last_connect_at_by_channel[channel_id] = self._clock()
                    parsed = self._await_response(
                        serial_port,
                        self._command_timeout_sec,
                        channel_id,
                        ok_command="CONNECT",
                    )
                    logger.info(
                        "[%s] antenna connect %s -> %s",
                        channel_id,
                        macs,
                        parsed and parsed.get("raw"),
                    )
                    self._write(
                        serial_port,
                        protocol.build_report_interval(self._report_interval_ms),
                        channel_id,
                    )
                    self._await_response(
                        serial_port,
                        self._command_timeout_sec,
                        channel_id,
                        ok_command="REPORT",
                    )
            except PortBusyError as exc:
                logger.warning(
                    "[%s] antenna CONNECT skipped, port busy: %s", channel_id, exc
                )

    def _read_telemetry_loop(self):
        next_retry = time.monotonic() + self._reconnect_interval_sec
        while not self._stop_event.is_set():
            for channel_id, serial_port in self._serials.items():
                line = self._read_line_yielding(serial_port, channel_id)
                if not line:
                    continue
                parsed = protocol.parse_line(line)
                if parsed.get("type") != "telemetry":
                    continue
                self._dispatch_telemetry(channel_id, parsed)
            if time.monotonic() >= next_retry:
                self._reconnect_missing_targets()
                next_retry = time.monotonic() + self._reconnect_interval_sec
            # Release the port for a beat, with NO lock held at all, so a
            # waiting web command (e.g. a SCAN from the setup page) can win
            # the cross-process lock. Without this gap the runtime would
            # re-acquire the port continuously and starve the web process.
            # ponytail: flock has no fairness guarantee -- this yield window
            # makes starvation unlikely, not impossible.
            self._stop_event.wait(READ_LOOP_YIELD_SEC)

    def _read_line_yielding(self, serial_port, channel_id: str) -> str | None:
        """Read one line for `channel_id`, holding the cross-process port
        lock only for the read itself. If the web command runner is
        currently mid-command on this same port, back off for this pass
        instead of blocking the whole telemetry loop."""
        try:
            with port_lock(
                self._channels_by_id[channel_id].port,
                timeout_sec=READ_LOOP_LOCK_TIMEOUT_SEC,
            ):
                return self._read_line(serial_port, channel_id)
        except PortBusyError:
            return None

    def _run_locked_channel_op(self, channel_id: str, description: str, fn):
        """Run `fn` (a no-arg callable doing one write/read on `channel_id`'s
        port) while holding that channel's cross-process port lock. Used for
        the per-channel steps of a command sequence (e.g. SCAN start/stop)
        so the lock is released between channels instead of held once
        around the whole multi-channel loop."""
        try:
            with port_lock(
                self._channels_by_id[channel_id].port, timeout_sec=PORT_LOCK_TIMEOUT_SEC
            ):
                return fn()
        except PortBusyError as exc:
            logger.warning(
                "[%s] antenna %s skipped, port busy: %s", channel_id, description, exc
            )
            return None

    def _dispatch_telemetry(self, channel_id: str, parsed: dict[str, Any]):
        mac = _normalize_device_id(parsed.get("address"))
        if mac:
            self._last_data_by_mac[mac] = time.monotonic()
            # a device holds one BLE link; keep channel target lists disjoint
            # so two boards never fight over the same machine
            for other_id, macs in self._assigned_macs_by_channel.items():
                if other_id != channel_id:
                    macs.discard(mac)
            self._assigned_macs_by_channel.setdefault(channel_id, set()).add(mac)
        telemetry = self._to_telemetry(channel_id, parsed)
        if not self._loop:
            return
        asyncio.run_coroutine_threadsafe(self._on_telemetry(telemetry), self._loop)

    def _reconnect_missing_targets(self):
        """Spec-compliant recovery: STATUS decides, not data silence. Idle
        machines produce no FTMS rows while staying connected, so the only
        reliable disconnect signal is connected < target.

        CONNECT is DESTRUCTIVE on the nRF52832 board: firmware always does a
        full disconnect-all, then scans and reconnects the given MAC list
        from scratch (~10-20s per device) -- it is a "reset target list and
        reconnect everything" command, never an incremental add. So this is
        NOT a plain "resend if short" loop: reissuing CONNECT while the board
        is still converging on the list we already gave it would tear down
        every in-progress link and restart the whole process, looping
        forever. Recovery only pushes CONNECT when there is good reason to
        believe the board doesn't already have the right list in flight:
        the expected list itself changed since our last push (STATUS can't
        reveal a same-count MAC swap), the board's own target count doesn't
        match what we expect, and the cooldown since our last push has
        elapsed. Otherwise we wait and let the board's own auto-reconnect
        converge."""
        # A pairing session (see usecases/pairing_session.py) temp-CONNECTs a
        # deliberately different MAC list per channel while it observes
        # candidates. Without this check that difference looks exactly like
        # "the expected list changed" below and triggers an immediate
        # destructive CONNECT that wrecks the session. The flag file uses the
        # same shared-cwd convention as data/uart-locks (port_lock.py): the
        # edge runtime and the web process are both launched from the same
        # working directory, so this relative path resolves to one shared
        # file for both processes with no extra configuration. Its mtime
        # must be fresh (< pairing_session.FLAG_STALE_AFTER_SEC old) so a
        # crashed/killed web process can't wedge this watchdog forever behind
        # a flag nobody will ever delete.
        if is_pairing_active():
            logger.info(
                "Pairing session flag is active; skipping reconnect watchdog "
                "pass so its temp CONNECT list is left alone"
            )
            return
        if self._refresh_configured_bindings() is None:
            logger.warning(
                "Configured bindings are unavailable; skipping reconnect watchdog "
                "pass to avoid using stale MACs"
            )
            return
        expected_by_channel: dict[str, list[str]] = {}
        for binding in self._edge_config.equipment_bindings:
            if not binding.ble_target or not MAC_ADDRESS_PATTERN.match(
                binding.ble_target
            ):
                continue
            if binding.antenna_channel not in self._serials:
                continue  # ponytail: MACs without a configured channel are not recovered
            expected_by_channel.setdefault(binding.antenna_channel, []).append(
                _normalize_device_id(binding.ble_target)
            )
        for channel_id, expected in expected_by_channel.items():
            serial_port = self._serials[channel_id]
            status = None
            try:
                with port_lock(
                    self._channels_by_id[channel_id].port,
                    timeout_sec=PORT_LOCK_TIMEOUT_SEC,
                ):
                    self._write(serial_port, protocol.build_status(), channel_id)
                    status = self._await_response(
                        serial_port,
                        self._command_timeout_sec,
                        channel_id,
                        wanted_types=frozenset({"status"}),
                    )
            except PortBusyError as exc:
                logger.warning(
                    "[%s] antenna STATUS skipped, port busy: %s", channel_id, exc
                )
            if status is None:
                logger.warning("[%s] antenna STATUS not answered", channel_id)
                continue
            connected = status.get("connected")
            expected_set = frozenset(expected)
            last_macs = self._last_connect_macs_by_channel.get(channel_id)
            board_target = status.get("target")

            if (
                last_macs is None
                and channel_id in self._saved_list_channels
                and board_target == len(expected)
            ):
                # A HAS_LIST board survives runtime restarts with its target
                # list intact, while this manager's in-memory "last pushed"
                # record does not. When STATUS confirms the expected target
                # count, adopt the configured set as our baseline instead of
                # destructively sending CONNECT just because memory is empty.
                self._last_connect_macs_by_channel[channel_id] = expected_set
                if connected is not None and connected < len(expected):
                    logger.info(
                        "[%s] board target list matches (%s/%s connected); "
                        "waiting for board auto-reconnect",
                        channel_id,
                        connected,
                        len(expected),
                    )
                continue

            if last_macs is not None and last_macs != expected_set:
                # The list itself changed.
                # STATUS only reports a count, so it cannot tell us a
                # same-count MAC swap happened -- push regardless of
                # cooldown or what the board's own target count says.
                refresh_result = self._refresh_configured_bindings()
                if refresh_result is not False:
                    if refresh_result:
                        logger.info(
                            "Configured bindings changed during watchdog pass; "
                            "discarding the stale reconnect decision"
                        )
                    else:
                        logger.warning(
                            "Configured bindings became unavailable during watchdog "
                            "pass; discarding the stale reconnect decision"
                        )
                    return
                logger.warning(
                    "[%s] antenna target list changed, reissuing CONNECT %s",
                    channel_id,
                    sorted(expected),
                )
                self._connect_assignments({channel_id: sorted(expected)})
                continue

            if connected is None or connected >= len(expected):
                continue

            if (
                board_target == len(expected)
                and self._last_connect_macs_by_channel.get(channel_id) == expected_set
            ):
                # The board already holds the right list and its firmware
                # auto-reconnect is presumably still in progress -- resending
                # CONNECT here would tear down that progress and restart it.
                logger.info(
                    "[%s] board target list matches (%s/%s connected); "
                    "waiting for board auto-reconnect",
                    channel_id,
                    connected,
                    len(expected),
                )
                continue

            last_push_at = self._last_connect_at_by_channel.get(channel_id)
            elapsed = self._clock() - last_push_at if last_push_at is not None else None
            if elapsed is not None and elapsed < CONNECT_COOLDOWN_SEC:
                logger.info(
                    "[%s] antenna connect on cooldown, %.1fs remaining before resend",
                    channel_id,
                    CONNECT_COOLDOWN_SEC - elapsed,
                )
                continue

            # Genuine "board lost its list" case (e.g. STATUS target=0 after
            # an NVS wipe) and the cooldown has elapsed -- safe to resend.
            logger.warning(
                "[%s] antenna connected %s/%s targets, reissuing CONNECT %s",
                channel_id,
                connected,
                len(expected),
                sorted(expected),
            )
            refresh_result = self._refresh_configured_bindings()
            if refresh_result is not False:
                if refresh_result:
                    logger.info(
                        "Configured bindings changed during watchdog pass; "
                        "discarding the stale reconnect decision"
                    )
                else:
                    logger.warning(
                        "Configured bindings became unavailable during watchdog "
                        "pass; discarding the stale reconnect decision"
                    )
                return
            self._connect_assignments({channel_id: sorted(expected)})

    def _refresh_configured_bindings(self) -> bool | None:
        """Reload bindings before watchdog decisions.

        The web setup service writes config.json in a separate process. Without
        this refresh, a successful DISCONNECT after removing a binding can be
        immediately undone by this long-running runtime using its stale
        in-memory MAC list. Returns True when the binding set changed so a
        caller holding an old reconciliation decision can safely abandon it.
        Returns None when the latest config cannot be loaded; callers must
        fail closed rather than reconnecting a stale MAC list.
        """
        if self._config_loader is None:
            return False
        try:
            fresh_config = self._config_loader()
        except Exception as exc:
            logger.warning("Unable to refresh Edge config for watchdog: %s", exc)
            return None

        current_signature = tuple(
            (
                binding.node_id,
                binding.equipment_id,
                binding.equipment_type,
                _normalize_device_id(binding.ble_target),
                binding.antenna_channel,
            )
            for binding in self._edge_config.equipment_bindings
        )
        fresh_signature = tuple(
            (
                binding.node_id,
                binding.equipment_id,
                binding.equipment_type,
                _normalize_device_id(binding.ble_target),
                binding.antenna_channel,
            )
            for binding in fresh_config.equipment_bindings
        )
        if fresh_signature == current_signature:
            return False

        self._edge_config.equipment_bindings = [
            binding.model_copy(deep=True) for binding in fresh_config.equipment_bindings
        ]
        self._edge_config.max_ftms_connections = fresh_config.max_ftms_connections
        self._bindings_by_mac.clear()
        self._next_binding_index_by_channel.clear()
        logger.info(
            "Reloaded %s configured antenna binding(s)",
            len(self._edge_config.equipment_bindings),
        )
        return True

    def _clear_no_list_channel_bindings(self, no_list_channels: set[str]):
        """Drop config.json bindings for channels that explicitly answered
        BOOT:NO_LIST -- the board's NVS target list is the source of truth,
        so if it says it holds nothing, config.json must not disagree.

        Hard safety rules (this is destructive to a venue's setup, get these
        exactly right):
        - Only ever runs for channels that gave an EXPLICIT NO_LIST reply.
          A channel that gave no answer at all (unplugged/wedged board, or a
          busy port after 3 retries) must never be treated as NO_LIST -- see
          _ping_channels/_run, which keep that state distinct for exactly
          this reason.
        - Only clears bindings on the channels that actually said NO_LIST;
          a HAS_LIST channel's bindings in the same config are left intact.
        - Skipped entirely while a pairing session is active, since it may
          be writing config.json concurrently.
        - Skipped when no config_saver is configured -- nothing to persist
          through, so nothing is changed.
        - Re-reads the freshest config.json via the existing watchdog
          refresh helper (fail-closed: if the freshest config cannot be
          loaded, nothing is cleared) rather than trusting the possibly
          stale self._edge_config that was built before this runtime even
          opened the serial ports -- the web setup process may have written
          config.json since then.
        """
        if not no_list_channels:
            return
        if self._config_saver is None:
            logger.info(
                "No config_saver configured; leaving config.json untouched "
                "for NO_LIST channel(s) %s",
                sorted(no_list_channels),
            )
            return
        if is_pairing_active():
            logger.info(
                "Pairing session flag is active; skipping board-authoritative "
                "config clearing for NO_LIST channel(s) %s so its writes "
                "are not raced",
                sorted(no_list_channels),
            )
            return
        if self._refresh_configured_bindings() is None:
            logger.warning(
                "Unable to refresh Edge config; skipping board-authoritative "
                "config clearing to avoid acting on stale data"
            )
            return

        removed_bindings = [
            binding
            for binding in self._edge_config.equipment_bindings
            if binding.antenna_channel in no_list_channels
        ]
        if not removed_bindings:
            return

        kept_bindings = [
            binding
            for binding in self._edge_config.equipment_bindings
            if binding.antenna_channel not in no_list_channels
        ]
        for binding in removed_bindings:
            logger.warning(
                "[%s] antenna board reports no saved target list; dropping "
                "configured binding node_id=%s equipment_id=%s mac=%s to "
                "match the board",
                binding.antenna_channel,
                binding.node_id,
                binding.equipment_id,
                binding.ble_target,
            )

        self._edge_config.equipment_bindings = kept_bindings
        self._config_saver(self._edge_config)

        removed_macs = {
            _normalize_device_id(binding.ble_target) for binding in removed_bindings
        }
        for mac in [
            mac
            for mac in self._bindings_by_mac
            if _normalize_device_id(mac) in removed_macs
        ]:
            del self._bindings_by_mac[mac]

    def _to_telemetry(self, channel_id: str, parsed: dict[str, Any]) -> TelemetryData:
        mac = parsed["address"]
        binding = self._binding_for_mac(channel_id, mac)
        equipment_type = parsed.get("equipment_type") or "unknown"
        normalized_mac = _normalize_device_id(mac)
        raw_distance_m = float(parsed.get("distance_m") or 0.0)
        raw_energy_kcal = parsed.get("total_energy_kcal")
        raw_energy_value = (
            float(raw_energy_kcal) if raw_energy_kcal is not None else None
        )
        delta_distance_m = self._delta_from_previous(
            self._last_raw_distance_by_mac,
            normalized_mac,
            raw_distance_m,
        )
        delta_energy_kcal = (
            self._delta_from_previous(
                self._last_raw_energy_by_mac,
                normalized_mac,
                raw_energy_value,
            )
            if raw_energy_value is not None
            else None
        )
        return TelemetryData(
            node_id=(
                binding.node_id
                if binding
                else f"{self._edge_config.node_id}-{mac.replace(':', '').lower()}"
            ),
            edge_node_id=self._edge_config.node_id,
            mac_address=mac,
            antenna_channel=channel_id,
            equipment_id=binding.equipment_id if binding else mac,
            equipment_type=binding.equipment_type if binding else equipment_type,
            ftms_type=parsed.get("ftms_type") or parsed.get("device_type"),
            rssi=parsed.get("rssi"),
            instantaneous_speed_kph=float(parsed.get("instantaneous_speed_kph") or 0.0),
            cadence_rpm=int(round(float(parsed.get("cadence_rpm") or 0))),
            pace_sec_per_500m=parsed.get("pace_sec_per_500m"),
            power_watts=int(parsed.get("power_watts") or 0),
            heart_rate_bpm=0,
            distance_m=raw_distance_m,
            raw_total_distance_m=raw_distance_m,
            delta_distance_m=delta_distance_m,
            total_energy_kcal=parsed.get("total_energy_kcal"),
            calories=parsed.get("total_energy_kcal"),
            raw_total_energy_kcal=raw_energy_value,
            delta_energy_kcal=delta_energy_kcal,
            elapsed_time_ms=0,
            timestamp_epoch_ms=int(time.time() * 1000),
            ftms_payload=parsed.get("ftms_payload"),
            raw_payload=parsed.get("raw_payload") or parsed.get("payload"),
        )

    def _delta_from_previous(
        self,
        previous_by_mac: dict[str, float],
        mac: str,
        current_value: float | None,
    ) -> float:
        if current_value is None:
            return 0.0
        previous_value = previous_by_mac.get(mac)
        previous_by_mac[mac] = current_value
        if previous_value is None or current_value < previous_value:
            return 0.0
        return current_value - previous_value

    def _binding_for_mac(self, channel_id: str, mac: str) -> EquipmentBinding | None:
        binding = self._bindings_by_mac.get(mac)
        if binding:
            return binding

        # exact MAC match wins regardless of which channel delivered the data,
        # otherwise a device heard on the "wrong" antenna gets someone else's stream
        for candidate in self._edge_config.equipment_bindings:
            if _normalize_device_id(candidate.ble_target) == _normalize_device_id(mac):
                self._bindings_by_mac[mac] = candidate
                logger.info(
                    "[%s] matched antenna target %s to %s",
                    channel_id,
                    mac,
                    candidate.node_id,
                )
                return candidate

        channel_bindings = [
            binding
            for binding in self._edge_config.equipment_bindings
            if binding.antenna_channel == channel_id
        ]

        used_node_ids = {binding.node_id for binding in self._bindings_by_mac.values()}
        start_index = self._next_binding_index_by_channel.get(channel_id, 0)
        for index in range(start_index, len(channel_bindings)):
            candidate = channel_bindings[index]
            if candidate.node_id in used_node_ids:
                continue
            self._bindings_by_mac[mac] = candidate
            self._next_binding_index_by_channel[channel_id] = index + 1
            logger.info(
                "[%s] assigned saved antenna target %s to %s",
                channel_id,
                mac,
                candidate.node_id,
            )
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
            if not line:
                continue
            parsed = protocol.parse_line(line)
            # telemetry interleaves with command responses on a live link;
            # forward it instead of dropping it while waiting for an ack
            if parsed.get("type") == "telemetry":
                if channel_id:
                    self._dispatch_telemetry(channel_id, parsed)
                continue
            if parsed.get("type") == "device":
                continue
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
    max_per_channel = max(
        1, math.ceil(_unique_device_count(scan_results) / len(channel_ids))
    )
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
        visible_channels = [
            channel_id for channel_id in channel_ids if channel_id in readings
        ]
        if not visible_channels:
            continue
        available = [
            channel_id
            for channel_id in visible_channels
            if len(assignments[channel_id]) < max_per_channel
        ] or visible_channels
        best_rssi = max(readings[channel_id] for channel_id in available)
        close = [
            channel_id
            for channel_id in available
            if abs(readings[channel_id] - best_rssi) < tie_threshold_db
        ]
        winner = min(
            close,
            key=lambda channel_id: (
                len(assignments[channel_id]),
                channel_ids.index(channel_id),
            ),
        )
        assignments[winner].append(mac)
    return assignments


def pin_assignments_to_configured_channels(
    assignments: dict[str, list[str]],
    bindings: list[EquipmentBinding],
    valid_channels: set[str],
) -> dict[str, list[str]]:
    """Move each MAC with a configured antenna_channel onto that channel."""
    configured = {
        _normalize_device_id(binding.ble_target): binding.antenna_channel
        for binding in bindings
        if binding.ble_target and binding.antenna_channel in valid_channels
    }
    result: dict[str, list[str]] = {channel_id: [] for channel_id in assignments}
    for channel_id, macs in assignments.items():
        for mac in macs:
            target = configured.get(_normalize_device_id(mac), channel_id)
            result.setdefault(target, []).append(mac)
    return result


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
    return len(
        {device.address for devices in scan_results.values() for device in devices}
    )


def _normalize_device_id(value: str | None) -> str:
    return (value or "").strip().upper()
