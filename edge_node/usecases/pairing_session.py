"""Backend state machine for the "Add device" pairing flow.

Operator story: tap "Add device" -> backend scans every configured antenna
channel -> temp-CONNECTs the strongest unbound candidates it heard
(respecting the nRF52832's 3-links-per-board limit) -> operator pedals the
physical machine so the frontend can tell candidates apart by motion ->
operator picks the one that moved and chooses its equipment type -> backend
writes the binding and restores/reapplies the real configured target lists.

CONNECT is DESTRUCTIVE on the nRF52832 antenna board (see
antenna_ftms_manager.py's module docstring): it always disconnect-alls, then
reconnects the given MAC list from scratch. That means while a pairing
session is temp-connected to candidate MACs, any *other* equipment already
bound on that same channel is bumped offline for the duration of the
session -- this is accepted as a setup-time cost, not a live-race concern.
confirm()/cancel() both restore the real configured lists afterwards.

Honest scope note: motion detection here is FTMS-telemetry-only (speed/
distance deltas from REPORT frames on an already-CONNECTed device). Detecting
motion from BLE manufacturer-data advertisements (i.e. for a candidate that
never got a temp CONNECT because its channel was full) is NOT implemented --
it is pending hardware verification of what the boards actually put in
BLE_DEVICE manufacturer-data frames. Candidates that were not temp-connected
always report moving=None (see status()).
"""

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from edge_node.domain.models import (
    EQUIPMENT_TYPES,
    MAX_CONNECTIONS_PER_CHANNEL,
    EdgeNodeConfig,
    EquipmentBinding,
)
from edge_node.infrastructure.antenna.command_runner import AntennaCommandRequest

logger = logging.getLogger("edge_node.pairing_session")

# Overrides the whole flag file path (same convention as
# EdgeEventLog.from_env's FITRACE_EDGE_MONITOR_PATH), not just a containing
# directory. Left as a relative path by default: the edge runtime
# (AntennaFtmsManager) and the web process (this module, via app.py) are
# started from the same working directory, exactly like the existing
# data/uart-locks convention in port_lock.py, so a relative default already
# points both processes at the same file with no extra configuration.
FLAG_PATH_ENV_VAR = "FITRACE_PAIRING_FLAG_PATH"
DEFAULT_FLAG_PATH = "data/pairing.flag"

# How old a pairing flag's mtime may be before the watchdog treats the
# session as dead and resumes normal reconnect behavior. This is what lets a
# crashed/killed web process self-heal instead of wedging the runtime's
# watchdog forever behind a flag nobody will ever delete.
FLAG_STALE_AFTER_SEC = 120.0

# A candidate must have reported telemetry within this window to be
# considered for "moving" at all; older data is treated as stale (not moving)
# even if the last known speed was high.
LIVE_TELEMETRY_WINDOW_MS = 3000

MOVING_SPEED_THRESHOLD_KPH = 0.5


class PairingSessionError(ValueError):
    """Raised for invalid pairing operations: unknown mac, full channel,
    unknown equipment_type, or an operation attempted with no active
    session."""


def pairing_flag_path() -> Path:
    return Path(os.environ.get(FLAG_PATH_ENV_VAR, DEFAULT_FLAG_PATH))


def is_pairing_active(path: Path | None = None, now: float | None = None) -> bool:
    """True if a pairing session's flag file exists and its mtime is fresh
    (< FLAG_STALE_AFTER_SEC old). Used by AntennaFtmsManager's watchdog to
    skip `_reconnect_missing_targets` while a pairing session is observing.
    """
    check_path = path or pairing_flag_path()
    try:
        mtime = check_path.stat().st_mtime
    except FileNotFoundError:
        return False
    current = now if now is not None else time.time()
    return (current - mtime) < FLAG_STALE_AFTER_SEC


@dataclass
class PairingCandidate:
    mac: str
    name: str
    rssi: int | None
    channel_id: str
    channel_accepts_new: bool
    # True if this candidate was among the top MAX_CONNECTIONS_PER_CHANNEL
    # strongest sightings on its channel and therefore actually got a temp
    # CONNECT issued for it. Candidates beyond that cutoff are still listed
    # (by RSSI) but were never connected, so they can never show motion.
    connected: bool


def _normalize_mac(value: str | None) -> str:
    return (value or "").strip().upper()


def _useful_name(value: str | None) -> str:
    trimmed = (value or "").strip()
    return trimmed if trimmed and trimmed != "UNKNOWN" else ""


def _status_sort_key(candidate: dict):
    moving = candidate["moving"]
    moving_rank = 0 if moving is True else (1 if moving is False else 2)
    rssi = candidate["rssi"]
    return (moving_rank, -(rssi if rssi is not None else -999))


def _next_node_id(config: EdgeNodeConfig) -> str:
    """Mirror the setup page's nextNodeId() JS: find the last binding whose
    node_id ends in digits, and bump that numeric suffix by one (padded to
    the same width). Falls back to "<edge node_id>-01" style when there are
    no bindings yet."""
    pattern = re.compile(r"^(.*?)([0-9]+)$")
    matches = [
        m
        for m in (pattern.match(b.node_id or "") for b in config.equipment_bindings)
        if m
    ]
    if matches:
        last = matches[-1]
        prefix = last.group(1)
        same_prefix = [m for m in matches if m.group(1) == prefix]
        next_num = max(int(m.group(2)) for m in same_prefix) + 1
        width = len(last.group(2))
        return f"{prefix}{str(next_num).zfill(width)}"
    return f"{config.node_id}-{str(len(config.equipment_bindings) + 1).zfill(2)}"


class PairingSession:
    """Stateful service backing the "Add device" pairing flow.

    Only one pairing session is ever active at a time -- this class *is*
    the module-level singleton (app.py constructs exactly one instance).
    Calling start() again while a session is scanning/observing does NOT
    start a second session or re-scan/re-CONNECT (SCAN and CONNECT are
    destructive to the boards; re-triggering them mid-session would wreck
    the session already in progress). Instead it returns the same response
    the original start() call produced, so a UI that double-submits or
    reloads mid-flow behaves idempotently instead of erroring.

    Dependency-injected on purpose so every branch is unit-testable without
    hardware: `command_runner` and `event_log` mirror AntennaCommandRunner /
    EdgeEventLog's interfaces, `load_config`/`save_config` mirror
    load_edge_config/save_edge_config, `restore_configured_devices` mirrors
    calling reconnect_configured_devices(..., disconnect_first=True), and
    `restart_service` mirrors PowerManager.restart_service() (already
    converted to a plain dict by the caller, e.g. via dataclasses.asdict, so
    this module never needs to import PowerManager's types).

    State machine: idle -> scanning -> observing -> done/cancelled. "done"
    and "cancelled" are momentary: confirm()/cancel() both leave the session
    reset to idle before returning, so the same PairingSession instance is
    immediately ready for the next start().
    """

    STATE_IDLE = "idle"
    STATE_SCANNING = "scanning"
    STATE_OBSERVING = "observing"
    STATE_DONE = "done"
    STATE_CANCELLED = "cancelled"

    def __init__(
        self,
        *,
        command_runner,
        event_log,
        load_config: Callable[[], EdgeNodeConfig],
        save_config: Callable[[EdgeNodeConfig], None],
        restore_configured_devices: Callable[[EdgeNodeConfig], dict],
        restart_service: Callable[[], dict],
        clock: Callable[[], float] = time.time,
        flag_path: Path | None = None,
        default_scan_duration_sec: float = 5.0,
        command_timeout_sec: float = 5.0,
        report_interval_ms: int = 250,
    ):
        self._command_runner = command_runner
        self._event_log = event_log
        self._load_config = load_config
        self._save_config = save_config
        self._restore_configured_devices = restore_configured_devices
        self._restart_service = restart_service
        self._clock = clock
        self._explicit_flag_path = flag_path
        self._default_scan_duration_sec = default_scan_duration_sec
        self._command_timeout_sec = command_timeout_sec
        self._report_interval_ms = report_interval_ms
        self._reset()

    @property
    def state(self) -> str:
        return self._state

    # -- public API ---------------------------------------------------

    def start(self, scan_duration_sec: float | None = None) -> dict:
        """Scan every configured antenna channel, exclude already-bound
        MACs, merge repeated sightings per MAC (strongest RSSI wins, first
        useful name kept -- mirrors the setup page's scan wizard merge), and
        temp-CONNECT up to MAX_CONNECTIONS_PER_CHANNEL strongest candidates
        per channel. Returns (or, if a session is already active, re-returns)
        `{session_id, candidates, capacity}`.
        """
        if self._state in (self.STATE_SCANNING, self.STATE_OBSERVING):
            return self._start_result

        self._state = self.STATE_SCANNING
        config = self._load_config()
        duration = scan_duration_sec or self._default_scan_duration_sec

        bound_macs = {
            _normalize_mac(binding.ble_target)
            for binding in config.equipment_bindings
            if binding.ble_target
        }
        configured_counts: dict[str, int] = {}
        for binding in config.equipment_bindings:
            if binding.antenna_channel:
                configured_counts[binding.antenna_channel] = (
                    configured_counts.get(binding.antenna_channel, 0) + 1
                )

        raw_candidates: dict[str, dict] = {}
        for channel in config.antenna_channels:
            result = self._command_runner.run(
                AntennaCommandRequest(
                    port=channel.port,
                    baudrate=channel.baudrate,
                    rtscts=channel.rtscts,
                    command="scan",
                    scan_duration_sec=duration,
                    timeout_sec=self._command_timeout_sec,
                )
            )
            for entry in result.get("parsed") or []:
                if entry.get("type") != "device":
                    continue
                mac = _normalize_mac(entry.get("address"))
                if not mac or mac in bound_macs:
                    continue
                rssi = entry.get("rssi")
                name = _useful_name(entry.get("name"))
                existing = raw_candidates.get(mac)
                if existing is None:
                    raw_candidates[mac] = {
                        "mac": mac,
                        "name": name,
                        "rssi": rssi,
                        "channel_id": channel.id,
                    }
                    continue
                # strongest RSSI wins -- including which channel "owns" the
                # candidate, since only one board can actually hold the link
                if rssi is not None and (
                    existing["rssi"] is None or rssi > existing["rssi"]
                ):
                    existing["rssi"] = rssi
                    existing["channel_id"] = channel.id
                # a nameless-but-stronger sighting must never discard a name
                # seen earlier (mirrors the wizard's usefulName() merge)
                if not existing["name"] and name:
                    existing["name"] = name

        accepts_new = {
            channel.id: configured_counts.get(channel.id, 0)
            < MAX_CONNECTIONS_PER_CHANNEL
            for channel in config.antenna_channels
        }

        by_channel: dict[str, list[dict]] = {}
        for candidate in raw_candidates.values():
            by_channel.setdefault(candidate["channel_id"], []).append(candidate)

        self._candidates = []
        macs_to_connect: dict[str, list[str]] = {}
        channels_by_id = {channel.id: channel for channel in config.antenna_channels}
        for channel in config.antenna_channels:
            entries = sorted(
                by_channel.get(channel.id, []),
                key=lambda c: c["rssi"] if c["rssi"] is not None else -999,
                reverse=True,
            )
            top = entries[:MAX_CONNECTIONS_PER_CHANNEL]
            top_macs = {c["mac"] for c in top}
            if top_macs:
                macs_to_connect[channel.id] = [c["mac"] for c in top]
            for candidate in entries:
                self._candidates.append(
                    PairingCandidate(
                        mac=candidate["mac"],
                        name=candidate["name"],
                        rssi=candidate["rssi"],
                        channel_id=channel.id,
                        channel_accepts_new=accepts_new[channel.id],
                        connected=candidate["mac"] in top_macs,
                    )
                )

        for channel_id, macs in macs_to_connect.items():
            channel = channels_by_id[channel_id]
            self._command_runner.run(
                AntennaCommandRequest(
                    port=channel.port,
                    baudrate=channel.baudrate,
                    rtscts=channel.rtscts,
                    command="connect",
                    macs=macs,
                    timeout_sec=self._command_timeout_sec,
                )
            )
            self._command_runner.run(
                AntennaCommandRequest(
                    port=channel.port,
                    baudrate=channel.baudrate,
                    rtscts=channel.rtscts,
                    command="report",
                    report_interval_ms=self._report_interval_ms,
                    timeout_sec=self._command_timeout_sec,
                )
            )

        self._session_id = uuid.uuid4().hex
        self._started_at = int(self._clock() * 1000)
        self._write_flag()
        self._state = self.STATE_OBSERVING

        per_channel_capacity = {
            channel.id: max(
                0, MAX_CONNECTIONS_PER_CHANNEL - configured_counts.get(channel.id, 0)
            )
            for channel in config.antenna_channels
        }
        self._start_result = {
            "session_id": self._session_id,
            "candidates": [
                {
                    "mac": c.mac,
                    "name": c.name,
                    "rssi": c.rssi,
                    "channel_id": c.channel_id,
                    "channel_accepts_new": c.channel_accepts_new,
                }
                for c in self._candidates
            ],
            "capacity": {
                "per_channel": per_channel_capacity,
                "total_free": sum(per_channel_capacity.values()),
            },
        }
        return self._start_result

    def status(self) -> dict:
        """Refresh the flag file's mtime (keeps the watchdog paused) and
        report each candidate's latest telemetry + motion. Candidates that
        were never temp-CONNECTed (beyond the per-channel capacity) always
        report moving=None/latest=None -- see module docstring."""
        if self._state == self.STATE_IDLE:
            if self._current_flag_path().exists():
                return {"state": "stale", **self._read_flag()}
            return {"state": "idle"}

        self._touch_flag()
        now_ms = int(self._clock() * 1000)
        telemetry_by_mac = self._telemetry_by_mac()

        candidates_out = []
        for candidate in self._candidates:
            latest = None
            moving = None
            if candidate.connected:
                sightings = telemetry_by_mac.get(candidate.mac, [])
                moving = False
                if sightings:
                    timestamp_ms, parsed = sightings[-1]
                    age_ms = now_ms - timestamp_ms
                    speed = parsed.get("instantaneous_speed_kph") or 0.0
                    distance = parsed.get("distance_m")
                    previous_distance = (
                        sightings[-2][1].get("distance_m")
                        if len(sightings) >= 2
                        else None
                    )
                    fresh = age_ms <= LIVE_TELEMETRY_WINDOW_MS
                    distance_increased = (
                        distance is not None
                        and previous_distance is not None
                        and distance > previous_distance
                    )
                    moving = bool(
                        fresh
                        and (speed > MOVING_SPEED_THRESHOLD_KPH or distance_increased)
                    )
                    latest = {
                        "speed_kph": speed,
                        "power_watts": parsed.get("power_watts"),
                        "rssi": parsed.get("rssi"),
                        "age_ms": age_ms,
                    }
            candidates_out.append(
                {
                    "mac": candidate.mac,
                    "name": candidate.name,
                    "rssi": candidate.rssi,
                    "channel_id": candidate.channel_id,
                    "channel_accepts_new": candidate.channel_accepts_new,
                    "connected": candidate.connected,
                    "moving": moving,
                    "latest": latest,
                }
            )

        candidates_out.sort(key=_status_sort_key)
        return {
            "state": self._state,
            "session_id": self._session_id,
            "candidates": candidates_out,
        }

    def confirm(
        self, mac: str, equipment_type: str, display_name: str | None = None
    ) -> dict:
        """Bind `mac` (must be a candidate on a channel with a free
        configured slot) as `equipment_type`, persist it through the same
        config path POST /api/config uses, restore every channel's real
        configured target list, restart the runtime service, and clear the
        session/flag. Returns `{binding, reconnect, restart}`."""
        if self._state not in (self.STATE_SCANNING, self.STATE_OBSERVING):
            raise PairingSessionError("No active pairing session")

        normalized_mac = _normalize_mac(mac)
        candidate = next((c for c in self._candidates if c.mac == normalized_mac), None)
        if candidate is None:
            raise PairingSessionError(
                f"{mac} is not a candidate in the current pairing session"
            )
        if not candidate.channel_accepts_new:
            raise PairingSessionError(
                f"channel {candidate.channel_id!r} has no free configured "
                "binding slot"
            )
        if equipment_type not in EQUIPMENT_TYPES:
            raise PairingSessionError(f"unknown equipment_type: {equipment_type!r}")

        if self._pending_binding is not None:
            binding = self._pending_binding
            if (
                _normalize_mac(binding.ble_target) != normalized_mac
                or binding.equipment_type != equipment_type
            ):
                raise PairingSessionError(
                    "A different binding confirmation is already being finalized"
                )
            new_config = self._pending_config or self._load_config()
        else:
            config = self._load_config()
            duplicate = next(
                (
                    binding
                    for binding in config.equipment_bindings
                    if _normalize_mac(binding.ble_target) == normalized_mac
                ),
                None,
            )
            if duplicate is not None:
                raise PairingSessionError(
                    f"{candidate.mac} is already bound to {duplicate.equipment_id!r}"
                )

            name = display_name or candidate.name or candidate.mac
            existing_equipment_ids = {
                binding.equipment_id for binding in config.equipment_bindings
            }
            if name in existing_equipment_ids:
                mac_suffix = candidate.mac.replace(":", "")[-4:]
                name = f"{name}-{mac_suffix}"

            node_id = _next_node_id(config)
            binding = EquipmentBinding(
                node_id=node_id,
                equipment_id=name,
                equipment_type=equipment_type,
                ble_target=candidate.mac,
                antenna_channel=candidate.channel_id,
            )

            payload = config.model_dump()
            payload["equipment_bindings"] = [
                *payload["equipment_bindings"],
                binding.model_dump(),
            ]
            if len(payload["equipment_bindings"]) > payload["max_ftms_connections"]:
                payload["max_ftms_connections"] = len(payload["equipment_bindings"])
            try:
                # EdgeNodeConfig's own validators (in particular the per-channel
                # <=3 connections limit) are the real enforcement point here --
                # our accepts_new check above is only a cheap pre-check for a
                # friendlier error message.
                new_config = EdgeNodeConfig.model_validate(payload)
            except ValidationError as exc:
                raise PairingSessionError(str(exc)) from exc

            self._save_config(new_config)
            # Persistence succeeded, but UART restoration and service restart
            # below may still fail. Keep this exact result so an operator retry
            # finalizes the same binding instead of appending the MAC again.
            self._pending_binding = binding
            self._pending_config = new_config
        reconnect_result = self._restore_configured_devices(new_config)
        restart_result = self._restart_service()
        self._delete_flag()
        self._reset()

        return {
            "binding": binding.model_dump(),
            "reconnect": reconnect_result,
            "restart": restart_result,
        }

    def cancel(self) -> dict:
        """Abandon the session: restore every channel's real configured
        target list, delete the flag, and reset to idle."""
        if self._state == self.STATE_IDLE:
            raise PairingSessionError("No active pairing session")

        config = self._load_config()
        reconnect_result = self._restore_configured_devices(config)
        self._delete_flag()
        self._reset()
        return {"status": "cancelled", "reconnect": reconnect_result}

    # -- internals ------------------------------------------------------

    def _reset(self):
        self._state = self.STATE_IDLE
        self._session_id: str | None = None
        self._started_at: int | None = None
        self._candidates: list[PairingCandidate] = []
        self._start_result: dict | None = None
        self._pending_binding: EquipmentBinding | None = None
        self._pending_config: EdgeNodeConfig | None = None

    def _current_flag_path(self) -> Path:
        return self._explicit_flag_path or pairing_flag_path()

    def _write_flag(self):
        path = self._current_flag_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": self._session_id,
            "started_at": self._started_at,
            "candidates": [
                {"mac": c.mac, "channel_id": c.channel_id, "connected": c.connected}
                for c in self._candidates
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _touch_flag(self):
        path = self._current_flag_path()
        try:
            os.utime(path, None)
        except FileNotFoundError:
            self._write_flag()

    def _delete_flag(self):
        try:
            self._current_flag_path().unlink()
        except FileNotFoundError:
            pass

    def _read_flag(self) -> dict:
        try:
            return json.loads(self._current_flag_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _telemetry_by_mac(self) -> dict[str, list[tuple[int, dict]]]:
        grouped: dict[str, list[tuple[int, dict]]] = {}
        for event in self._event_log.list_events(limit=500):
            if event.get("source") != "uart" or event.get("direction") != "rx":
                continue
            parsed = event.get("parsed") or {}
            if parsed.get("type") != "telemetry":
                continue
            mac = _normalize_mac(parsed.get("address"))
            if not mac:
                continue
            grouped.setdefault(mac, []).append(
                (event.get("timestamp_epoch_ms", 0), parsed)
            )
        return grouped
