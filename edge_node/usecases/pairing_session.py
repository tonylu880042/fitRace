"""Backend state machine for the "Add device" pairing flow.

Operator story: tap "Add device" -> backend scans every configured antenna
channel -> temp-CONNECTs the strongest unbound candidates it heard, up to
whatever free capacity each channel has left (respecting the nRF52832's
3-links-per-board limit) -> operator pedals the physical machine so the
frontend can tell candidates apart by motion -> operator picks the one that
moved and chooses its equipment type -> backend writes the binding and tears
down the temp connections it made.

Temp-connecting a candidate uses CONNECT_ADD (v1.3.0+ firmware): a single-MAC,
non-destructive incremental add that does not disturb any other GATT session
already open on that channel. That means equipment already bound on the same
channel keeps streaming telemetry uninterrupted for the whole pairing
session -- CONNECT_ADD only touches the one slot it's adding. If a board
answers CONNECT_ADD with `ERROR:UNKNOWN_CMD:CONNECT_ADD;` its firmware
predates v1.3.0; for that one channel only, start() falls back to the old
destructive behaviour (one batch CONNECT with the top
MAX_CONNECTIONS_PER_CHANNEL candidates, which disconnect-alls the channel
first).

confirm() and cancel() do NOT mirror each other. cancel() abandons the
session -- nothing was persisted, so a cheap per-MAC DISCONNECT of whatever
this session temp-added is enough (channels that fell back to destructive
CONNECT still get the old restore-configured-devices treatment, since their
real bound list was already disconnected and needs reconnecting). confirm()
commits a new binding, so it always calls restore_configured_devices with the
full updated config -- see the comment at that call site for why an
authoritative batch CONNECT is required there and a per-MAC DISCONNECT is not
enough, even on channels that only ever used CONNECT_ADD.

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
from dataclasses import dataclass, field
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

# Shorter timeout for CONNECT_ADD ACK only (docs/Dual_Central_Board_Manager_設計文件.md line 125).
# The timeout covers only the command ACK itself (CONNECT_ADD:OK;), not the
# actual BLE link establishment which happens asynchronously with a fixed 3
# second UCM_BLE_CONNECT_GRACE_MS wait. CRITICAL: _read_lines in command_runner.py
# uses a FIXED wait loop (while time.monotonic() < deadline) — no early exit
# when data arrives. This timeout is not a maximum but a full blocking wait:
# if the ACK arrives at 1.2s but timeout is 0.5s, it is never seen. The UART
# carries 250ms-interval FTMS telemetry from up to 3 connected devices on the
# same port during pairing; board busyness requires headroom. 1.5s saves 3.5s
# per device vs the 5s command_timeout_sec, still yielding good throughput.
# If the board is slow to ACK, _classify_connect_add_reply returns "unrecognized",
# surfacing as "added but not confirmed connected" to the operator — an acceptable,
# visible outcome. A scan command genuinely needs the full timeout window, so
# this constant applies only to connect_add ACKs. Future: replace fixed wait
# with early-exit _read_lines variant to improve responsiveness.
CONNECT_ADD_ACK_TIMEOUT_SEC = 1.5


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
    # True if this candidate actually ended up temp-connected: either it was
    # among the strongest sightings within its channel's free capacity and
    # got a successful CONNECT_ADD (or ERROR:ALREADY_EXISTS), or its channel
    # fell back to a destructive batch CONNECT and it was in the top
    # MAX_CONNECTIONS_PER_CHANNEL of that batch. Candidates that were never
    # attempted (beyond capacity) or stopped early (ERROR:FULL) are still
    # listed (by RSSI) but were never connected, so they can never show
    # motion. Also set by the scan-first connect() flow when its one-shot
    # CONNECT_ADD for this candidate succeeds.
    connected: bool
    # True once bind() has persisted a binding for this candidate in this
    # session -- guards against binding the same scanned MAC twice without
    # a fresh scan. Unused by the legacy temp-connect flow (start() with the
    # default temp_connect=True + confirm()), which never sets it.
    bound: bool = False
    # Every channel that heard this candidate during the scan and its
    # strongest RSSI there, e.g. {"uart-1": -60, "uart-2": -75} -- unlike
    # `rssi`/`channel_id` above (which keep only the single strongest
    # sighting), this survives the start() merge so bind() and the operator
    # can tell whether a channel other than the strongest one also heard the
    # device and has room for it.
    rssi_by_channel: dict[str, int] = field(default_factory=dict)
    # Set by bind() only when the operator (or the scan-first flow's
    # optional `channel` argument) picked a channel other than channel_id.
    # connect() must target this, not channel_id, once it is set, or it
    # would CONNECT_ADD the wrong board after an operator-chosen switch.
    bound_channel_id: str | None = None


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


def _classify_connect_add_reply(parsed_entries: list[dict]) -> str:
    """Classify a CONNECT_ADD command_runner reply's `parsed` entries into one
    of: "ok" (added), "already_exists" (treated as added -- the MAC was
    already in the board's target list), "full" (channel is at capacity,
    stop adding more on it), "unknown_cmd" (pre-v1.3.0 firmware doesn't know
    CONNECT_ADD at all -- caller should fall back to destructive CONNECT for
    the whole channel), or "unrecognized" (no matching reply seen; caller
    should stop rather than guess at board state).

    `ERROR:UNKNOWN_CMD:...` is checked first and independently of the other
    branches since it's a protocol-level reply, not a CONNECT_ADD-specific
    one -- see docs/Dual_Central_Board_Manager_設計文件.md.
    """
    for entry in parsed_entries:
        if entry.get("type") == "error" and "UNKNOWN_CMD" in (
            entry.get("message") or ""
        ):
            return "unknown_cmd"
    for entry in parsed_entries:
        if entry.get("type") == "ok" and entry.get("command") == "CONNECT_ADD":
            return "ok"
        if entry.get("type") == "error":
            message = entry.get("message") or ""
            if "ALREADY_EXISTS" in message:
                return "already_exists"
            if "FULL" in message:
                return "full"
    return "unrecognized"


def _channel_capacity(config: EdgeNodeConfig) -> dict[str, int]:
    """Free configured-binding slots per antenna channel: `max(0,
    MAX_CONNECTIONS_PER_CHANNEL - configured_count)`. Shared by start() and
    bind() so both report capacity the same way -- bind()'s call reflects
    the config *after* that bind's save, so a caller polling it sees
    remaining slots shrink live as bindings land."""
    configured_counts: dict[str, int] = {}
    for binding in config.equipment_bindings:
        if binding.antenna_channel:
            configured_counts[binding.antenna_channel] = (
                configured_counts.get(binding.antenna_channel, 0) + 1
            )
    return {
        channel.id: max(
            0, MAX_CONNECTIONS_PER_CHANNEL - configured_counts.get(channel.id, 0)
        )
        for channel in config.antenna_channels
    }


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

    def start(
        self,
        scan_duration_sec: float | None = None,
        *,
        temp_connect: bool = True,
    ) -> dict:
        """Scan every configured antenna channel, exclude already-bound
        MACs, merge repeated sightings per MAC (strongest RSSI wins, first
        useful name kept -- mirrors the setup page's scan wizard merge).

        When `temp_connect` is True (the default -- unchanged legacy
        behaviour), also temp-CONNECT_ADD up to that channel's free capacity
        (MAX_CONNECTIONS_PER_CHANNEL minus its already-bound equipment)
        strongest candidates per channel, one non-destructive CONNECT_ADD at
        a time. A channel whose firmware doesn't understand CONNECT_ADD
        (pre-v1.3.0) falls back to the old destructive batch CONNECT with
        the top MAX_CONNECTIONS_PER_CHANNEL candidates instead.

        When `temp_connect` is False (the scan-first flow: bind()/connect()/
        finish() below), only `scan` commands are issued -- no CONNECT_ADD,
        no batch CONNECT, no REPORT. Every candidate comes back with
        connected=False; the operator connects each one individually via
        connect() only after choosing to bind it.

        Returns (or, if a session is already active, re-returns)
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
                        "rssi_by_channel": (
                            {channel.id: rssi} if rssi is not None else {}
                        ),
                    }
                    continue
                # Keep every channel's own strongest sighting too -- unlike
                # the single "strongest overall" rssi/channel_id below, this
                # is never overwritten, so bind() and the operator can later
                # see whether a channel other than the strongest one also
                # heard this device (see PairingCandidate.rssi_by_channel).
                if rssi is not None:
                    by_channel = existing["rssi_by_channel"]
                    prior = by_channel.get(channel.id)
                    if prior is None or rssi > prior:
                        by_channel[channel.id] = rssi
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

        per_channel_capacity = _channel_capacity(config)
        accepts_new = {
            channel_id: capacity > 0
            for channel_id, capacity in per_channel_capacity.items()
        }

        by_channel: dict[str, list[dict]] = {}
        for candidate in raw_candidates.values():
            by_channel.setdefault(candidate["channel_id"], []).append(candidate)

        self._candidates = []
        self._temp_added_by_channel = {}
        self._fallback_channels = set()
        for channel in config.antenna_channels:
            entries = sorted(
                by_channel.get(channel.id, []),
                key=lambda c: c["rssi"] if c["rssi"] is not None else -999,
                reverse=True,
            )
            free_slots = per_channel_capacity[channel.id]
            incremental_pool = entries[:free_slots] if free_slots > 0 else []

            connected_macs: set[str] = set()

            if temp_connect:
                temp_added: list[str] = []
                used_fallback = False

                for candidate in incremental_pool:
                    mac = candidate["mac"]
                    reply = self._command_runner.run(
                        AntennaCommandRequest(
                            port=channel.port,
                            baudrate=channel.baudrate,
                            rtscts=channel.rtscts,
                            command="connect_add",
                            macs=[mac],
                            timeout_sec=self._command_timeout_sec,
                        )
                    )
                    outcome = _classify_connect_add_reply(reply.get("parsed") or [])
                    if outcome == "unknown_cmd":
                        used_fallback = True
                        break
                    if outcome in ("ok", "already_exists"):
                        connected_macs.add(mac)
                        temp_added.append(mac)
                        continue
                    # "full" (channel just hit capacity) or "unrecognized"
                    # (no reply we understand): stop adding more to this
                    # channel rather than guessing at board state.
                    break

                if used_fallback:
                    # Pre-v1.3.0 firmware doesn't know CONNECT_ADD at all --
                    # fall back to the old destructive batch CONNECT for this
                    # channel, using the same top-N selection start() used
                    # before this change (independent of free_slots, exactly
                    # like today).
                    top = entries[:MAX_CONNECTIONS_PER_CHANNEL]
                    top_macs = {c["mac"] for c in top}
                    connected_macs = top_macs
                    temp_added = []
                    self._fallback_channels.add(channel.id)
                    if top_macs:
                        self._command_runner.run(
                            AntennaCommandRequest(
                                port=channel.port,
                                baudrate=channel.baudrate,
                                rtscts=channel.rtscts,
                                command="connect",
                                macs=[c["mac"] for c in top],
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
                elif temp_added:
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

                self._temp_added_by_channel[channel.id] = temp_added

            # temp_connect=False (scan-first flow): no CONNECT_ADD/CONNECT/
            # REPORT at all -- connected_macs stays empty and
            # self._temp_added_by_channel is never touched for this channel,
            # so every candidate below comes back connected=False.

            for candidate in entries:
                self._candidates.append(
                    PairingCandidate(
                        mac=candidate["mac"],
                        name=candidate["name"],
                        rssi=candidate["rssi"],
                        channel_id=channel.id,
                        channel_accepts_new=accepts_new[channel.id],
                        connected=candidate["mac"] in connected_macs,
                        rssi_by_channel=dict(candidate.get("rssi_by_channel") or {}),
                    )
                )

        self._session_id = uuid.uuid4().hex
        self._started_at = int(self._clock() * 1000)
        self._write_flag()
        self._state = self.STATE_OBSERVING

        self._start_result = {
            "session_id": self._session_id,
            "candidates": [
                {
                    "mac": c.mac,
                    "name": c.name,
                    "rssi": c.rssi,
                    "channel_id": c.channel_id,
                    "channel_accepts_new": c.channel_accepts_new,
                    "rssi_by_channel": c.rssi_by_channel,
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
                    "rssi_by_channel": candidate.rssi_by_channel,
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
        configured target list (making the antenna board's NVS-persisted
        target list authoritative again -- see the comment at the restore
        call below), restart the runtime service, and clear the session/
        flag. Returns `{binding, reconnect, restart}`."""
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
            binding, new_config = self._build_and_save_binding(
                candidate, equipment_type, display_name
            )
            # Persistence succeeded, but UART restoration and service restart
            # below may still fail. Keep this exact result so an operator retry
            # finalizes the same binding instead of appending the MAC again.
            self._pending_binding = binding
            self._pending_config = new_config
        # Must restore, not just per-MAC-disconnect the other temp candidates:
        # the antenna board's target list is only durable in NVS via a batch
        # CONNECT (DISCONNECT:ALL clears it, CONNECT:<list> rewrites it --
        # docs/Dual_Central_Board_Manager_設計文件.md). Whether CONNECT_ADD
        # itself persists to NVS is unverified, so treat it as RAM-only: if
        # confirm() only disconnected the losing candidates and never issued
        # a batch CONNECT, the newly bound MAC would still be missing from
        # NVS. After a board power-cycle it would boot with the old target
        # count (BOOT:HAS_LIST,count=<old>), the runtime would see STATUS
        # connected == target and consider the channel fully healthy, and the
        # new equipment would silently vanish from the race with no self-heal
        # path. restore_configured_devices's batch CONNECT rewrites NVS from
        # `new_config` (which already includes this binding), so it is both
        # the authoritative fix and -- being destructive -- also clears every
        # unchosen temp CONNECT_ADD candidate; no separate per-MAC DISCONNECT
        # is needed here.
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
        """Abandon the session: tear down whatever this session temp-
        connected, delete the flag, and reset to idle."""
        if self._state == self.STATE_IDLE:
            raise PairingSessionError("No active pairing session")

        config = self._load_config()
        reconnect_result = self._teardown_temp_connections(config)
        self._delete_flag()
        self._reset()
        return {"status": "cancelled", "reconnect": reconnect_result}

    # -- scan-first pairing flow (start(temp_connect=False) + these) --------
    #
    # CONNECT_ADD persists the MAC into the board's NVS target list on its
    # own (confirmed with the hardware owner), so unlike confirm() above,
    # nothing here ever needs an authoritative batch CONNECT to make the
    # binding durable -- and CONNECT/DISCONNECT:ALL are never issued by this
    # flow at all, so equipment already bound and streaming is never
    # disturbed while the operator keeps adding more.

    def bind(
        self,
        mac: str,
        equipment_type: str,
        display_name: str | None = None,
        channel: str | None = None,
    ) -> dict:
        """Persist one binding and keep the session alive: state stays
        OBSERVING (no reset, no restart, no UART command of any kind) so the
        operator can bind more candidates from the same scan. Raises
        PairingSessionError if `mac` isn't a candidate in this session, was
        already bound in this session, `equipment_type` is unknown, or the
        MAC is already bound in the persisted config. Returns
        `{binding, capacity}`, with capacity recomputed from the config as
        it stands right after this save.

        `channel`, if given, overrides which antenna channel the binding is
        made on. It only has to exist in the current config and have a free
        configured slot -- it does NOT have to be one of the channels that
        actually heard this candidate during the scan (candidate.rssi_by_channel).
        An 8-second scan is a weak signal about what a board can physically
        reach; the operator standing in the room knows better, and refusing
        an "unheard" channel would just relocate the dead end this method
        exists to remove (a full strongest-channel with a free slot
        elsewhere the operator can see but not use). If the chosen channel
        genuinely cannot reach the device, CONNECT_ADD still succeeds (it
        only edits the board's target list) but the link will never
        establish -- that must surface as a visible "added, not connected"
        state to the operator, not silently look like success (see
        connect()'s caller in app.py / the worklist UI).

        When `channel` is omitted, falls back to today's behaviour: bind to
        the candidate's scan-time strongest channel (channel_id), rejecting
        it only via the same free-slot check.
        """
        if self._state not in (self.STATE_SCANNING, self.STATE_OBSERVING):
            raise PairingSessionError("No active pairing session")

        normalized_mac = _normalize_mac(mac)
        candidate = next((c for c in self._candidates if c.mac == normalized_mac), None)
        if candidate is None:
            raise PairingSessionError(
                f"{mac} is not a candidate in the current pairing session"
            )
        if candidate.bound:
            raise PairingSessionError(
                f"{candidate.mac} was already bound in this pairing session"
            )

        if channel is not None:
            config = self._load_config()
            channel_ids = {ch.id for ch in config.antenna_channels}
            if channel not in channel_ids:
                raise PairingSessionError(f"channel {channel!r} is not configured")
            per_channel_capacity = _channel_capacity(config)
            if per_channel_capacity.get(channel, 0) <= 0:
                raise PairingSessionError(
                    f"channel {channel!r} has no free configured binding slot"
                )
            target_channel_id = channel
        else:
            if not candidate.channel_accepts_new:
                raise PairingSessionError(
                    f"channel {candidate.channel_id!r} has no free configured "
                    "binding slot"
                )
            target_channel_id = candidate.channel_id

        if equipment_type not in EQUIPMENT_TYPES:
            raise PairingSessionError(f"unknown equipment_type: {equipment_type!r}")

        binding, new_config = self._build_and_save_binding(
            candidate, equipment_type, display_name, channel_id=target_channel_id
        )
        candidate.bound = True
        # connect() must target wherever this binding actually landed, not
        # candidate.channel_id, once the two can diverge.
        candidate.bound_channel_id = target_channel_id
        self._touch_flag()

        per_channel_capacity = _channel_capacity(new_config)
        return {
            "binding": binding.model_dump(),
            "capacity": {
                "per_channel": per_channel_capacity,
                "total_free": sum(per_channel_capacity.values()),
            },
        }

    def connect(self, mac: str) -> dict:
        """Issue exactly one CONNECT_ADD for `mac` on the channel its
        binding actually used and classify the reply. Never falls back to a
        batch CONNECT: on `unknown_cmd` this channel's firmware simply
        cannot support the scan-first flow, and the caller (operator page)
        is expected to surface that rather than this method guessing at a
        destructive workaround. Sends REPORT for the channel only the first
        time a connect() on it succeeds this session, not for every device.
        Returns `{mac, channel_id, outcome, connected}`."""
        if self._state not in (self.STATE_SCANNING, self.STATE_OBSERVING):
            raise PairingSessionError("No active pairing session")

        normalized_mac = _normalize_mac(mac)
        candidate = next((c for c in self._candidates if c.mac == normalized_mac), None)
        if candidate is None:
            raise PairingSessionError(
                f"{mac} is not a candidate in the current pairing session"
            )

        # bind() may have persisted this candidate on a different channel
        # than the one start()'s strongest-RSSI merge originally picked (its
        # optional `channel` argument) -- target wherever the binding
        # actually landed, not candidate.channel_id, or this would
        # CONNECT_ADD the wrong board entirely.
        target_channel_id = candidate.bound_channel_id or candidate.channel_id

        config = self._load_config()
        channel = next(
            (ch for ch in config.antenna_channels if ch.id == target_channel_id),
            None,
        )
        if channel is None:
            raise PairingSessionError(
                f"channel {target_channel_id!r} is not configured"
            )

        reply = self._command_runner.run(
            AntennaCommandRequest(
                port=channel.port,
                baudrate=channel.baudrate,
                rtscts=channel.rtscts,
                command="connect_add",
                macs=[normalized_mac],
                timeout_sec=CONNECT_ADD_ACK_TIMEOUT_SEC,
            )
        )
        outcome = _classify_connect_add_reply(reply.get("parsed") or [])
        connected = outcome in ("ok", "already_exists")
        if connected:
            candidate.connected = True
            if target_channel_id not in self._reported_channels:
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
                self._reported_channels.add(target_channel_id)

        return {
            "mac": normalized_mac,
            "channel_id": target_channel_id,
            "outcome": outcome,
            "connected": connected,
        }

    def finish(self, restart: bool = True) -> dict:
        """End a scan-first session: every binding this session made was
        already persisted to the board's NVS by its own connect() call, so
        there is nothing left to reconnect or restore -- just delete the
        flag, reset to idle, and (when `restart` is True) restart the
        runtime service so it re-reads config.json and the new equipment
        gets its configured name instead of the MAC fallback. Issues no
        UART commands. Returns `{status, restart}`."""
        if self._state == self.STATE_IDLE:
            raise PairingSessionError("No active pairing session")

        self._delete_flag()
        self._reset()
        restart_result = self._restart_service() if restart else None
        return {"status": "finished", "restart": restart_result}

    # -- internals ------------------------------------------------------

    def _build_and_save_binding(
        self,
        candidate: PairingCandidate,
        equipment_type: str,
        display_name: str | None,
        channel_id: str | None = None,
    ) -> tuple[EquipmentBinding, EdgeNodeConfig]:
        """Shared by confirm() and bind(): load the current config, reject a
        MAC already bound in it, compute the binding's name (existing
        name-collision suffix included) and node_id, build+validate the new
        config through EdgeNodeConfig.model_validate -- its per-channel
        MAX_CONNECTIONS_PER_CHANNEL validator is the real enforcement point,
        not the caller's cheap channel_accepts_new pre-check -- persist it,
        and return (binding, new_config). Callers own their own
        session-state bookkeeping afterwards (confirm()'s pending-binding
        retry fields; bind()'s per-candidate `bound` flag).

        `channel_id` lets bind() persist the binding on an operator-chosen
        channel instead of the candidate's scan-time strongest channel;
        confirm() never passes it and keeps using candidate.channel_id."""
        config = self._load_config()
        normalized_mac = _normalize_mac(candidate.mac)
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
            antenna_channel=channel_id or candidate.channel_id,
        )

        payload = config.model_dump()
        payload["equipment_bindings"] = [
            *payload["equipment_bindings"],
            binding.model_dump(),
        ]
        if len(payload["equipment_bindings"]) > payload["max_ftms_connections"]:
            payload["max_ftms_connections"] = len(payload["equipment_bindings"])
        try:
            new_config = EdgeNodeConfig.model_validate(payload)
        except ValidationError as exc:
            raise PairingSessionError(str(exc)) from exc

        self._save_config(new_config)
        return binding, new_config

    def _teardown_temp_connections(self, config: EdgeNodeConfig) -> dict:
        """Undo this session's temp connections. Only called from cancel() --
        confirm() restores via `_restore_configured_devices` instead (see the
        comment at that call site), since it needs an authoritative NVS
        rewrite rather than a cheap per-MAC teardown.

        Channels that used incremental CONNECT_ADD during start() never had
        their real bound equipment disturbed, so they just get a per-MAC
        DISCONNECT for each temp-added candidate. Channels that fell back to
        the destructive batch CONNECT path (see start()) get the old
        restore-configured-devices treatment instead, since the real bound
        list on that channel was already disconnected and needs reconnecting.

        ponytail: if one channel used CONNECT_ADD and another fell back to
        the destructive CONNECT path, the branch below still calls
        restore_configured_devices(config) once for the whole config, which
        issues DISCONNECT:ALL + CONNECT even for the channel that never
        needed it -- momentarily bouncing an otherwise-untouched good
        channel. This is a limitation of reconnect_configured_devices being
        a whole-config operation, not a per-channel one; not fixing it here.
        """
        channels_by_id = {channel.id: channel for channel in config.antenna_channels}
        disconnected: list[dict] = []
        for channel_id, macs in self._temp_added_by_channel.items():
            if channel_id in self._fallback_channels:
                continue
            channel = channels_by_id.get(channel_id)
            if channel is None:
                continue
            for mac in macs:
                result = self._command_runner.run(
                    AntennaCommandRequest(
                        port=channel.port,
                        baudrate=channel.baudrate,
                        rtscts=channel.rtscts,
                        command="disconnect",
                        macs=[mac],
                        timeout_sec=self._command_timeout_sec,
                    )
                )
                disconnected.append(
                    {"channel_id": channel_id, "mac": mac, "result": result}
                )

        if not self._fallback_channels:
            return {"status": "disconnected", "channels": disconnected}

        restore_result = self._restore_configured_devices(config)
        return {
            "status": "restored",
            "disconnected": disconnected,
            "restore": restore_result,
        }

    def _reset(self):
        self._state = self.STATE_IDLE
        self._session_id: str | None = None
        self._started_at: int | None = None
        self._candidates: list[PairingCandidate] = []
        self._start_result: dict | None = None
        self._pending_binding: EquipmentBinding | None = None
        self._pending_config: EdgeNodeConfig | None = None
        self._temp_added_by_channel: dict[str, list[str]] = {}
        self._fallback_channels: set[str] = set()
        # Channels that connect() has already sent a REPORT for this
        # session (scan-first flow only) -- keeps REPORT to once per
        # channel across many connect() calls instead of once per device.
        self._reported_channels: set[str] = set()

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
