"""Unit tests for PairingSession (edge_node/usecases/pairing_session.py).

All dependencies (antenna command runner, event log, config load/save,
restore-configured-devices, restart-service, clock) are faked so the whole
state machine is exercised without any hardware or real filesystem sharing.
"""

import json

import pytest

from edge_node.domain.models import (
    AntennaChannelConfig,
    EdgeNodeConfig,
    EquipmentBinding,
)
from edge_node.usecases.pairing_session import PairingSession, PairingSessionError


class FakeCommandRunner:
    """Records every AntennaCommandRequest and answers "scan" with canned
    parsed device sightings per port; "connect"/"report"/"disconnect_all"/
    "disconnect" just ack.

    "connect_add" replies are queued per-port via `connect_add_replies_by_port`
    (a dict of port -> list of canned `parsed` lists, consumed in call order
    for that port). Once a port's queue is exhausted -- or if the port was
    never given one -- every further connect_add on it defaults to a canned
    `CONNECT_ADD:OK;` success, so tests that don't care about the ok/full/
    already-exists/unknown-cmd branches can ignore this entirely.
    """

    def __init__(self, scan_results_by_port=None, connect_add_replies_by_port=None):
        self.scan_results_by_port = scan_results_by_port or {}
        self.connect_add_replies_by_port = connect_add_replies_by_port or {}
        self._connect_add_call_index: dict[str, int] = {}
        self.calls = []

    def run(self, request):
        self.calls.append(request)
        if request.command == "scan":
            parsed = self.scan_results_by_port.get(request.port, [])
            return {"port": request.port, "command": "scan", "parsed": parsed}
        if request.command == "connect_add":
            queue = self.connect_add_replies_by_port.get(request.port)
            index = self._connect_add_call_index.get(request.port, 0)
            self._connect_add_call_index[request.port] = index + 1
            if queue is not None and index < len(queue):
                parsed = queue[index]
            else:
                parsed = [{"type": "ok", "command": "CONNECT_ADD"}]
            return {"port": request.port, "command": "connect_add", "parsed": parsed}
        return {
            "port": request.port,
            "command": request.command,
            "parsed": [{"type": "ok", "command": request.command.upper()}],
        }


class FakeEventLog:
    def __init__(self, events=None):
        self.events = events or []

    def list_events(self, limit=100):
        return self.events[-limit:]


class Harness:
    def __init__(
        self, session, runner, event_log, restore_calls, restart_calls, config_holder
    ):
        self.session = session
        self.runner = runner
        self.event_log = event_log
        self.restore_calls = restore_calls
        self.restart_calls = restart_calls
        self.config_holder = config_holder

    def connect_calls(self, port=None):
        return [
            c
            for c in self.runner.calls
            if c.command == "connect" and (port is None or c.port == port)
        ]

    def connect_add_calls(self, port=None):
        return [
            c
            for c in self.runner.calls
            if c.command == "connect_add" and (port is None or c.port == port)
        ]

    def disconnect_calls(self, port=None):
        return [
            c
            for c in self.runner.calls
            if c.command == "disconnect" and (port is None or c.port == port)
        ]

    def disconnect_all_calls(self, port=None):
        return [
            c
            for c in self.runner.calls
            if c.command == "disconnect_all" and (port is None or c.port == port)
        ]


def make_harness(
    config,
    *,
    scan_results_by_port=None,
    connect_add_replies_by_port=None,
    events=None,
    clock=None,
    flag_path=None,
    restore_result=None,
    restart_result=None,
):
    runner = FakeCommandRunner(scan_results_by_port, connect_add_replies_by_port)
    event_log = FakeEventLog(events)
    config_holder = {"config": config}
    restore_calls = []
    restart_calls = []

    def load_config():
        return config_holder["config"]

    def save_config(new_config):
        config_holder["config"] = new_config

    def restore(cfg):
        restore_calls.append(cfg)
        return restore_result or {"status": "reconnected", "channels": []}

    def restart():
        restart_calls.append(True)
        return restart_result or {"dry_run": True, "executed": False}

    session = PairingSession(
        command_runner=runner,
        event_log=event_log,
        load_config=load_config,
        save_config=save_config,
        restore_configured_devices=restore,
        restart_service=restart,
        clock=clock or (lambda: 1_700_000_000.0),
        flag_path=flag_path,
    )
    return Harness(
        session, runner, event_log, restore_calls, restart_calls, config_holder
    )


def make_config(bindings=None):
    return EdgeNodeConfig(
        node_id="fitrace-edge-01",
        antenna_channels=[
            AntennaChannelConfig(id="uart-1", port="/dev/ttyAMA0"),
            AntennaChannelConfig(id="uart-2", port="/dev/ttyAMA4"),
        ],
        equipment_bindings=bindings or [],
        max_ftms_connections=10,
    )


def device(address, rssi, name=""):
    return {"type": "device", "address": address, "rssi": rssi, "name": name}


# --------------------------------------------------------------------------
# start()
# --------------------------------------------------------------------------


def test_start_excludes_bound_macs_merges_sightings_and_flags_full_channel(tmp_path):
    config = make_config(
        bindings=[
            EquipmentBinding(
                node_id="fitrace-edge-01-01",
                equipment_id="BOUND_BIKE",
                equipment_type="fan_bike",
                ble_target="AA:BB:CC:DD:EE:01",
                antenna_channel="uart-1",
            ),
            EquipmentBinding(
                node_id="fitrace-edge-01-02",
                equipment_id="ROW_A",
                equipment_type="rowing_machine",
                ble_target="AA:BB:CC:DD:EE:10",
                antenna_channel="uart-2",
            ),
            EquipmentBinding(
                node_id="fitrace-edge-01-03",
                equipment_id="ROW_B",
                equipment_type="rowing_machine",
                ble_target="AA:BB:CC:DD:EE:11",
                antenna_channel="uart-2",
            ),
            EquipmentBinding(
                node_id="fitrace-edge-01-04",
                equipment_id="ROW_C",
                equipment_type="rowing_machine",
                ble_target="AA:BB:CC:DD:EE:12",
                antenna_channel="uart-2",
            ),
        ]
    )
    scan_results_by_port = {
        "/dev/ttyAMA0": [
            device("AA:BB:CC:DD:EE:01", -30, "Bound Bike"),  # already bound: excluded
            device("AA:BB:CC:DD:EE:02", -60, ""),  # weaker sighting, no name
            device("AA:BB:CC:DD:EE:02", -50, "Bike A"),  # stronger + a name
            device("AA:BB:CC:DD:EE:03", -55, "Bike B"),
            device(
                "AA:BB:CC:DD:EE:04", -70, "Bike C"
            ),  # beyond the 2 free slots: not temp-connected
            device(
                "AA:BB:CC:DD:EE:05", -80, "Bike D"
            ),  # beyond the 2 free slots: not temp-connected
        ],
        "/dev/ttyAMA4": [
            device(
                "AA:BB:CC:DD:EE:06", -40, "Rower X"
            ),  # channel is full (3/3 configured)
        ],
    }
    flag_path = tmp_path / "pairing.flag"
    harness = make_harness(
        config, scan_results_by_port=scan_results_by_port, flag_path=flag_path
    )

    result = harness.session.start()

    assert result["capacity"]["per_channel"] == {"uart-1": 2, "uart-2": 0}
    assert result["capacity"]["total_free"] == 2

    candidates_by_mac = {c["mac"]: c for c in result["candidates"]}
    assert "AA:BB:CC:DD:EE:01" not in candidates_by_mac  # already bound
    assert set(candidates_by_mac) == {
        "AA:BB:CC:DD:EE:02",
        "AA:BB:CC:DD:EE:03",
        "AA:BB:CC:DD:EE:04",
        "AA:BB:CC:DD:EE:05",
        "AA:BB:CC:DD:EE:06",
    }
    # merged sighting: best RSSI wins, name filled from the sighting that had one
    assert candidates_by_mac["AA:BB:CC:DD:EE:02"]["rssi"] == -50
    assert candidates_by_mac["AA:BB:CC:DD:EE:02"]["name"] == "Bike A"
    assert candidates_by_mac["AA:BB:CC:DD:EE:02"]["channel_accepts_new"] is True
    # channel is already at the 3-connection limit -> no free configured slot
    assert candidates_by_mac["AA:BB:CC:DD:EE:06"]["channel_accepts_new"] is False

    # only the 2 free slots on uart-1 actually got an incremental CONNECT_ADD;
    # uart-2 has zero free slots, so its one candidate never gets a command at
    # all. Nothing here is destructive: no batch "connect" and no
    # "disconnect_all" is ever issued, and the already-bound MACs (uart-1's
    # AA:...:01 and uart-2's ...:10/:11/:12) never appear in any command.
    uart1_connect_add = harness.connect_add_calls("/dev/ttyAMA0")
    assert [c.macs for c in uart1_connect_add] == [
        ["AA:BB:CC:DD:EE:02"],
        ["AA:BB:CC:DD:EE:03"],
    ]
    assert harness.connect_add_calls("/dev/ttyAMA4") == []
    assert harness.connect_calls() == []
    assert harness.disconnect_all_calls() == []
    bound_macs = {
        "AA:BB:CC:DD:EE:01",
        "AA:BB:CC:DD:EE:10",
        "AA:BB:CC:DD:EE:11",
        "AA:BB:CC:DD:EE:12",
    }
    for call in harness.runner.calls:
        assert not (bound_macs & set(call.macs))

    status_by_mac = {c["mac"]: c for c in harness.session.status()["candidates"]}
    assert status_by_mac["AA:BB:CC:DD:EE:02"]["connected"] is True
    assert status_by_mac["AA:BB:CC:DD:EE:03"]["connected"] is True
    assert status_by_mac["AA:BB:CC:DD:EE:04"]["connected"] is False
    assert status_by_mac["AA:BB:CC:DD:EE:05"]["connected"] is False
    assert status_by_mac["AA:BB:CC:DD:EE:06"]["connected"] is False

    # flag file written with enough state to report staleness after a restart
    flag_payload = json.loads(flag_path.read_text(encoding="utf-8"))
    assert flag_payload["session_id"] == result["session_id"]
    assert {c["mac"] for c in flag_payload["candidates"]} == set(candidates_by_mac)


def test_start_called_again_while_active_returns_existing_session(tmp_path):
    config = make_config()
    scan_results_by_port = {
        "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:01", -40, "Bike A")],
        "/dev/ttyAMA4": [],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        flag_path=tmp_path / "pairing.flag",
    )

    first = harness.session.start()
    calls_after_first = len(harness.runner.calls)
    second = harness.session.start()

    assert second == first
    # no additional scan/connect was issued -- SCAN/CONNECT are destructive
    assert len(harness.runner.calls) == calls_after_first


# --------------------------------------------------------------------------
# start() -- CONNECT_ADD reply branches
# --------------------------------------------------------------------------


def test_start_error_full_stops_further_adds_on_that_channel(tmp_path):
    config = make_config()  # no bindings -> uart-1 has all 3 slots free
    scan_results_by_port = {
        "/dev/ttyAMA0": [
            device("AA:BB:CC:DD:EE:01", -30, "Bike A"),
            device("AA:BB:CC:DD:EE:02", -40, "Bike B"),
            device("AA:BB:CC:DD:EE:03", -50, "Bike C"),
        ],
        "/dev/ttyAMA4": [],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        connect_add_replies_by_port={
            "/dev/ttyAMA0": [
                [{"type": "ok", "command": "CONNECT_ADD"}],
                [{"type": "error", "message": "CONNECT_ADD:ERROR:FULL"}],
            ],
        },
        flag_path=tmp_path / "pairing.flag",
    )

    harness.session.start()

    # only the first two candidates were ever attempted -- the loop stopped
    # on FULL instead of trying the third
    uart1_connect_add = harness.connect_add_calls("/dev/ttyAMA0")
    assert [c.macs for c in uart1_connect_add] == [
        ["AA:BB:CC:DD:EE:01"],
        ["AA:BB:CC:DD:EE:02"],
    ]
    status_by_mac = {c["mac"]: c for c in harness.session.status()["candidates"]}
    assert status_by_mac["AA:BB:CC:DD:EE:01"]["connected"] is True
    assert status_by_mac["AA:BB:CC:DD:EE:02"]["connected"] is False
    assert status_by_mac["AA:BB:CC:DD:EE:03"]["connected"] is False
    assert harness.connect_calls() == []


def test_start_error_already_exists_counts_as_connected(tmp_path):
    config = make_config()
    scan_results_by_port = {
        "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:01", -30, "Bike A")],
        "/dev/ttyAMA4": [],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        connect_add_replies_by_port={
            "/dev/ttyAMA0": [
                [{"type": "error", "message": "CONNECT_ADD:ERROR:ALREADY_EXISTS"}],
            ],
        },
        flag_path=tmp_path / "pairing.flag",
    )

    harness.session.start()

    status_by_mac = {c["mac"]: c for c in harness.session.status()["candidates"]}
    assert status_by_mac["AA:BB:CC:DD:EE:01"]["connected"] is True
    assert harness.connect_calls() == []


def test_start_error_unknown_cmd_falls_back_to_destructive_connect(tmp_path):
    config = make_config()
    scan_results_by_port = {
        "/dev/ttyAMA0": [
            device("AA:BB:CC:DD:EE:01", -30, "Bike A"),
            device("AA:BB:CC:DD:EE:02", -40, "Bike B"),
        ],
        "/dev/ttyAMA4": [],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        connect_add_replies_by_port={
            "/dev/ttyAMA0": [
                [{"type": "error", "message": "ERROR:UNKNOWN_CMD:CONNECT_ADD"}],
            ],
        },
        flag_path=tmp_path / "pairing.flag",
    )

    harness.session.start()

    # exactly one CONNECT_ADD was tried (that's how the firmware version is
    # discovered), then the channel fell back to a single destructive batch
    # CONNECT with the top candidates, followed by REPORT
    assert len(harness.connect_add_calls("/dev/ttyAMA0")) == 1
    uart1_connect = harness.connect_calls("/dev/ttyAMA0")
    assert len(uart1_connect) == 1
    assert sorted(uart1_connect[0].macs) == [
        "AA:BB:CC:DD:EE:01",
        "AA:BB:CC:DD:EE:02",
    ]
    report_calls = [c for c in harness.runner.calls if c.command == "report"]
    assert len(report_calls) == 1

    status_by_mac = {c["mac"]: c for c in harness.session.status()["candidates"]}
    assert status_by_mac["AA:BB:CC:DD:EE:01"]["connected"] is True
    assert status_by_mac["AA:BB:CC:DD:EE:02"]["connected"] is True

    # cancel() must restore-configured-devices for this channel, since the
    # destructive CONNECT already disconnected its real bound equipment
    result = harness.session.cancel()

    assert len(harness.restore_calls) == 1
    assert result["reconnect"]["status"] == "restored"


# --------------------------------------------------------------------------
# status()
# --------------------------------------------------------------------------


def _single_channel_config():
    return make_config()


def _start_with_four_candidates(harness):
    scan_results_by_port = {
        "/dev/ttyAMA0": [
            device("AA:BB:CC:DD:EE:AA", -40, "A"),
            device("AA:BB:CC:DD:EE:BB", -50, "B"),
            device("AA:BB:CC:DD:EE:CC", -60, "C"),
            device("AA:BB:CC:DD:EE:DD", -70, "D"),
        ],
        "/dev/ttyAMA4": [],
    }
    harness.runner.scan_results_by_port = scan_results_by_port
    return harness.session.start()


def test_status_reports_idle_when_no_session_and_no_flag(tmp_path):
    harness = make_harness(
        _single_channel_config(), flag_path=tmp_path / "pairing.flag"
    )

    assert harness.session.status() == {"state": "idle"}


def test_status_moving_true_on_speed_over_threshold(tmp_path):
    harness = make_harness(
        _single_channel_config(),
        clock=lambda: 1.5,
        flag_path=tmp_path / "pairing.flag",
    )
    _start_with_four_candidates(harness)
    harness.event_log.events = [
        {
            "source": "uart",
            "direction": "rx",
            "timestamp_epoch_ms": 1000,
            "parsed": {
                "type": "telemetry",
                "address": "AA:BB:CC:DD:EE:AA",
                "instantaneous_speed_kph": 5.0,
                "distance_m": 10.0,
                "power_watts": 120,
                "rssi": -40,
            },
        }
    ]

    result = harness.session.status()

    candidate = next(c for c in result["candidates"] if c["mac"] == "AA:BB:CC:DD:EE:AA")
    assert candidate["connected"] is True
    assert candidate["moving"] is True
    assert candidate["latest"]["speed_kph"] == 5.0
    assert candidate["latest"]["age_ms"] == 500


def test_status_moving_true_on_distance_increase(tmp_path):
    harness = make_harness(
        _single_channel_config(),
        clock=lambda: 2.5,
        flag_path=tmp_path / "pairing.flag",
    )
    _start_with_four_candidates(harness)
    harness.event_log.events = [
        {
            "source": "uart",
            "direction": "rx",
            "timestamp_epoch_ms": 1000,
            "parsed": {
                "type": "telemetry",
                "address": "AA:BB:CC:DD:EE:BB",
                "instantaneous_speed_kph": 0.0,
                "distance_m": 10.0,
                "power_watts": 0,
                "rssi": -45,
            },
        },
        {
            "source": "uart",
            "direction": "rx",
            "timestamp_epoch_ms": 2000,
            "parsed": {
                "type": "telemetry",
                "address": "AA:BB:CC:DD:EE:BB",
                "instantaneous_speed_kph": 0.0,
                "distance_m": 12.0,
                "power_watts": 0,
                "rssi": -45,
            },
        },
    ]

    result = harness.session.status()

    candidate = next(c for c in result["candidates"] if c["mac"] == "AA:BB:CC:DD:EE:BB")
    assert candidate["moving"] is True


def test_status_moving_false_when_telemetry_is_stale(tmp_path):
    harness = make_harness(
        _single_channel_config(),
        clock=lambda: 5.0,
        flag_path=tmp_path / "pairing.flag",
    )
    _start_with_four_candidates(harness)
    harness.event_log.events = [
        {
            "source": "uart",
            "direction": "rx",
            "timestamp_epoch_ms": 1000,  # 4000ms old at clock()=5.0s -> stale (>3s)
            "parsed": {
                "type": "telemetry",
                "address": "AA:BB:CC:DD:EE:CC",
                "instantaneous_speed_kph": 8.0,
                "distance_m": 50.0,
                "power_watts": 200,
                "rssi": -60,
            },
        }
    ]

    result = harness.session.status()

    candidate = next(c for c in result["candidates"] if c["mac"] == "AA:BB:CC:DD:EE:CC")
    assert candidate["moving"] is False


def test_status_unconnected_candidate_beyond_capacity_reports_moving_none(tmp_path):
    harness = make_harness(
        _single_channel_config(),
        clock=lambda: 1.0,
        flag_path=tmp_path / "pairing.flag",
    )
    _start_with_four_candidates(harness)

    result = harness.session.status()

    candidate = next(c for c in result["candidates"] if c["mac"] == "AA:BB:CC:DD:EE:DD")
    assert candidate["connected"] is False
    assert candidate["moving"] is None
    assert candidate["latest"] is None


def test_status_sorts_moving_candidates_first_then_by_rssi(tmp_path):
    harness = make_harness(
        _single_channel_config(),
        clock=lambda: 1.5,
        flag_path=tmp_path / "pairing.flag",
    )
    _start_with_four_candidates(harness)
    # BB is the weakest RSSI of the three connected candidates but the only one moving
    harness.event_log.events = [
        {
            "source": "uart",
            "direction": "rx",
            "timestamp_epoch_ms": 1000,
            "parsed": {
                "type": "telemetry",
                "address": "AA:BB:CC:DD:EE:CC",
                "instantaneous_speed_kph": 6.0,
                "distance_m": 1.0,
                "power_watts": 50,
                "rssi": -60,
            },
        }
    ]

    result = harness.session.status()

    macs_in_order = [c["mac"] for c in result["candidates"]]
    # CC is moving (rank 0); AA and BB are connected-but-idle (rank 1, AA has
    # better RSSI); DD was never connected (rank 2, moving=None)
    assert macs_in_order == [
        "AA:BB:CC:DD:EE:CC",
        "AA:BB:CC:DD:EE:AA",
        "AA:BB:CC:DD:EE:BB",
        "AA:BB:CC:DD:EE:DD",
    ]


def test_status_refreshes_flag_mtime(tmp_path):
    flag_path = tmp_path / "pairing.flag"
    harness = make_harness(_single_channel_config(), flag_path=flag_path)
    _start_with_four_candidates(harness)
    mtime_after_start = flag_path.stat().st_mtime

    # advance the file's mtime backwards to prove status() bumps it forward
    import os

    os.utime(flag_path, (mtime_after_start - 500, mtime_after_start - 500))
    harness.session.status()

    assert flag_path.stat().st_mtime > mtime_after_start - 500


# --------------------------------------------------------------------------
# confirm()
# --------------------------------------------------------------------------


def _harness_ready_to_confirm(
    tmp_path, bindings=None, connect_add_replies_by_port=None
):
    config = make_config(bindings=bindings)
    scan_results_by_port = {
        "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:05", -40, "Bike A")],
        "/dev/ttyAMA4": [device("AA:BB:CC:DD:EE:06", -40, "Rower X")],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        connect_add_replies_by_port=connect_add_replies_by_port,
        flag_path=tmp_path / "pairing.flag",
    )
    harness.session.start()
    return harness


def test_confirm_rejects_unknown_mac(tmp_path):
    harness = _harness_ready_to_confirm(tmp_path)

    with pytest.raises(PairingSessionError):
        harness.session.confirm("AA:BB:CC:DD:EE:99", "fan_bike")


def test_confirm_rejects_unknown_equipment_type(tmp_path):
    harness = _harness_ready_to_confirm(tmp_path)

    with pytest.raises(PairingSessionError):
        harness.session.confirm("AA:BB:CC:DD:EE:05", "jet_ski")


def test_confirm_rejects_mac_on_a_full_channel(tmp_path):
    full_bindings = [
        EquipmentBinding(
            node_id=f"fitrace-edge-01-0{i}",
            equipment_id=f"ROW_{i}",
            equipment_type="rowing_machine",
            ble_target=f"AA:BB:CC:DD:EE:1{i}",
            antenna_channel="uart-2",
        )
        for i in range(3)
    ]
    harness = _harness_ready_to_confirm(tmp_path, bindings=full_bindings)

    with pytest.raises(PairingSessionError):
        harness.session.confirm("AA:BB:CC:DD:EE:06", "rowing_machine")  # uart-2 is full

    assert harness.restore_calls == []


def test_confirm_name_collision_appends_last_four_mac_hex_chars(tmp_path):
    existing = [
        EquipmentBinding(
            node_id="fitrace-edge-01-01",
            equipment_id="Bike A",
            equipment_type="fan_bike",
            ble_target="AA:BB:CC:DD:EE:99",
            antenna_channel="uart-1",
        )
    ]
    harness = _harness_ready_to_confirm(tmp_path, bindings=existing)

    result = harness.session.confirm("AA:BB:CC:DD:EE:05", "fan_bike")

    assert result["binding"]["equipment_id"] == "Bike A-EE05"


def test_confirm_node_id_gets_next_free_numeric_suffix(tmp_path):
    existing = [
        EquipmentBinding(
            node_id="fitrace-edge-01-01",
            equipment_id="Bike Existing",
            equipment_type="fan_bike",
            ble_target="AA:BB:CC:DD:EE:99",
            antenna_channel="uart-1",
        ),
        EquipmentBinding(
            node_id="fitrace-edge-01-02",
            equipment_id="Rower Existing",
            equipment_type="rowing_machine",
            ble_target="AA:BB:CC:DD:EE:98",
            antenna_channel="uart-2",
        ),
    ]
    harness = _harness_ready_to_confirm(tmp_path, bindings=existing)

    result = harness.session.confirm("AA:BB:CC:DD:EE:05", "fan_bike")

    assert result["binding"]["node_id"] == "fitrace-edge-01-03"


def test_confirm_persists_config_restores_configured_devices_restarts_and_clears_flag(
    tmp_path,
):
    flag_path = tmp_path / "pairing.flag"
    harness = _harness_ready_to_confirm(tmp_path)
    # _harness_ready_to_confirm builds its own flag_path fixture already, so
    # rebuild with an explicit one we can assert against post-confirm
    config = make_config()
    scan_results_by_port = {
        "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:05", -40, "Bike A")],
        "/dev/ttyAMA4": [],
    }
    harness = make_harness(
        config, scan_results_by_port=scan_results_by_port, flag_path=flag_path
    )
    harness.session.start()
    assert flag_path.exists()

    result = harness.session.confirm(
        "AA:BB:CC:DD:EE:05", "fan_bike", display_name="My Bike"
    )

    assert result["binding"]["equipment_id"] == "My Bike"
    assert result["binding"]["ble_target"] == "AA:BB:CC:DD:EE:05"
    assert result["binding"]["antenna_channel"] == "uart-1"
    assert result["restart"] == {"dry_run": True, "executed": False}
    # confirm() must always make the board's NVS target list authoritative
    # again by restoring the full configured device list -- even though this
    # channel only ever used the non-destructive CONNECT_ADD path -- rather
    # than issuing a per-MAC disconnect of the losing candidates.
    assert result["reconnect"] == {"status": "reconnected", "channels": []}
    assert harness.restore_calls == [harness.config_holder["config"]]

    # config was persisted through save_config with the new binding present
    saved_config = harness.config_holder["config"]
    assert any(b.equipment_id == "My Bike" for b in saved_config.equipment_bindings)
    assert len(harness.restore_calls) == 1
    assert len(harness.restart_calls) == 1

    # no per-MAC disconnect command was issued for the losing candidate --
    # restore_configured_devices's own batch CONNECT is what clears it
    assert harness.disconnect_calls() == []

    assert not flag_path.exists()
    assert harness.session.state == PairingSession.STATE_IDLE


def test_confirm_calls_restore_configured_devices_once_and_issues_no_disconnects(
    tmp_path,
):
    config = make_config()  # no bindings -> uart-1 has all 3 slots free
    scan_results_by_port = {
        "/dev/ttyAMA0": [
            device("AA:BB:CC:DD:EE:01", -30, "Bike A"),
            device("AA:BB:CC:DD:EE:02", -40, "Bike B"),
            device("AA:BB:CC:DD:EE:03", -50, "Bike C"),
        ],
        "/dev/ttyAMA4": [],
    }
    flag_path = tmp_path / "pairing.flag"
    harness = make_harness(
        config, scan_results_by_port=scan_results_by_port, flag_path=flag_path
    )
    harness.session.start()

    result = harness.session.confirm("AA:BB:CC:DD:EE:02", "fan_bike")

    assert result["binding"]["ble_target"] == "AA:BB:CC:DD:EE:02"
    # restore_configured_devices was called exactly once, with the new
    # config (i.e. the one including the just-confirmed binding) -- and no
    # per-MAC disconnect commands were issued at all, on either channel.
    assert len(harness.restore_calls) == 1
    restored_config = harness.restore_calls[0]
    assert any(
        b.ble_target == "AA:BB:CC:DD:EE:02" for b in restored_config.equipment_bindings
    )
    assert harness.disconnect_calls() == []


def test_confirm_restores_the_newly_bound_mac_into_the_configured_device_list(
    tmp_path,
):
    # Regression lock: the batch CONNECT that restore_configured_devices
    # issues must be driven by a config whose MAC list now includes the
    # newly bound candidate -- that's what rewrites the antenna board's NVS
    # target list to include it. Our fake restore_configured_devices is a
    # stub that just records the config it was called with rather than
    # driving a real command runner, so this test asserts on that recorded
    # config's bindings rather than on an actual "connect" command's macs.
    config = make_config()
    scan_results_by_port = {
        "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:07", -40, "Bike A")],
        "/dev/ttyAMA4": [],
    }
    flag_path = tmp_path / "pairing.flag"
    harness = make_harness(
        config, scan_results_by_port=scan_results_by_port, flag_path=flag_path
    )
    harness.session.start()

    harness.session.confirm("AA:BB:CC:DD:EE:07", "fan_bike")

    assert len(harness.restore_calls) == 1
    restored_macs = {b.ble_target for b in harness.restore_calls[0].equipment_bindings}
    assert "AA:BB:CC:DD:EE:07" in restored_macs


def test_confirm_retry_after_restore_failure_does_not_duplicate_binding(tmp_path):
    # confirm() now always calls restore_configured_devices directly (see
    # the comment at that call site in confirm()), so no fallback-path
    # rigging is needed to exercise it here -- the plain incremental
    # CONNECT_ADD path calls it too.
    harness = _harness_ready_to_confirm(tmp_path)
    restore_attempts = 0

    def flaky_restore(_config):
        nonlocal restore_attempts
        restore_attempts += 1
        if restore_attempts == 1:
            raise RuntimeError("transient UART restore failure")
        return {"status": "reconnected", "channels": []}

    harness.session._restore_configured_devices = flaky_restore

    with pytest.raises(RuntimeError, match="transient UART restore failure"):
        harness.session.confirm("AA:BB:CC:DD:EE:05", "fan_bike")

    assert len(harness.config_holder["config"].equipment_bindings) == 1

    result = harness.session.confirm("AA:BB:CC:DD:EE:05", "fan_bike")

    assert result["binding"]["ble_target"] == "AA:BB:CC:DD:EE:05"
    assert len(harness.config_holder["config"].equipment_bindings) == 1
    assert restore_attempts == 2


def test_confirm_config_validation_is_the_real_enforcement_point(tmp_path):
    # Precondition at scan time: uart-1 has 2 configured bindings, so the
    # candidate's cached channel_accepts_new is True. Simulate the config
    # changing (e.g. someone else edited bindings) to 3 *before* confirm is
    # called, so the cheap cached pre-check would wrongly allow it -- only
    # EdgeNodeConfig's own per-channel validator can catch this.
    two_bindings = [
        EquipmentBinding(
            node_id=f"fitrace-edge-01-0{i}",
            equipment_id=f"BIKE_{i}",
            equipment_type="fan_bike",
            ble_target=f"AA:BB:CC:DD:EE:2{i}",
            antenna_channel="uart-1",
        )
        for i in range(2)
    ]
    harness = _harness_ready_to_confirm(tmp_path, bindings=two_bindings)
    candidate = next(
        c for c in harness.session._candidates if c.mac == "AA:BB:CC:DD:EE:05"
    )
    assert candidate.channel_accepts_new is True  # stale cache says "fine"

    three_bindings = two_bindings + [
        EquipmentBinding(
            node_id="fitrace-edge-01-02",
            equipment_id="BIKE_2",
            equipment_type="fan_bike",
            ble_target="AA:BB:CC:DD:EE:22",
            antenna_channel="uart-1",
        )
    ]
    harness.config_holder["config"] = make_config(bindings=three_bindings)

    with pytest.raises(PairingSessionError):
        harness.session.confirm("AA:BB:CC:DD:EE:05", "fan_bike")

    # validation failed before persistence -- nothing was saved or restored
    assert harness.config_holder["config"].equipment_bindings == three_bindings
    assert harness.restore_calls == []


# --------------------------------------------------------------------------
# cancel()
# --------------------------------------------------------------------------


def test_cancel_on_happy_path_disconnects_temp_macs_without_restoring(tmp_path):
    # Two bound devices already occupy uart-1 (leaving 1 free slot); the
    # scan turns up two more candidates than that channel has room for, plus
    # one candidate on uart-2 (2 free slots there). cancel() should tear down
    # exactly the MACs this session temp-added -- one per DISCONNECT call --
    # and never touch restore_configured_devices, since neither channel's
    # real bound equipment was ever disturbed.
    bindings = [
        EquipmentBinding(
            node_id="fitrace-edge-01-01",
            equipment_id="BOUND_A",
            equipment_type="fan_bike",
            ble_target="AA:BB:CC:DD:EE:01",
            antenna_channel="uart-1",
        ),
        EquipmentBinding(
            node_id="fitrace-edge-01-02",
            equipment_id="BOUND_B",
            equipment_type="fan_bike",
            ble_target="AA:BB:CC:DD:EE:02",
            antenna_channel="uart-1",
        ),
    ]
    config = make_config(bindings=bindings)
    flag_path = tmp_path / "pairing.flag"
    harness = make_harness(
        config,
        scan_results_by_port={
            "/dev/ttyAMA0": [
                device("AA:BB:CC:DD:EE:10", -40, "Bike C"),
                device("AA:BB:CC:DD:EE:11", -50, "Bike D"),
            ],
            "/dev/ttyAMA4": [
                device("AA:BB:CC:DD:EE:20", -40, "Rower X"),
            ],
        },
        flag_path=flag_path,
    )
    harness.session.start()
    assert flag_path.exists()

    result = harness.session.cancel()

    assert result["status"] == "cancelled"
    assert harness.restore_calls == []
    uart1_disconnects = harness.disconnect_calls("/dev/ttyAMA0")
    assert [c.macs for c in uart1_disconnects] == [["AA:BB:CC:DD:EE:10"]]
    uart2_disconnects = harness.disconnect_calls("/dev/ttyAMA4")
    assert [c.macs for c in uart2_disconnects] == [["AA:BB:CC:DD:EE:20"]]
    assert harness.disconnect_all_calls() == []
    assert not flag_path.exists()
    assert harness.session.state == PairingSession.STATE_IDLE
    assert harness.session.status() == {"state": "idle"}


def test_cancel_with_no_active_session_raises():
    harness = make_harness(_single_channel_config())

    with pytest.raises(PairingSessionError):
        harness.session.cancel()


# --------------------------------------------------------------------------
# start(temp_connect=False) -- scan-first flow
# --------------------------------------------------------------------------


def test_start_temp_connect_false_issues_only_scan_commands(tmp_path):
    config = make_config()
    scan_results_by_port = {
        "/dev/ttyAMA0": [
            device("AA:BB:CC:DD:EE:01", -30, "Bike A"),
            device("AA:BB:CC:DD:EE:02", -40, "Bike B"),
        ],
        "/dev/ttyAMA4": [device("AA:BB:CC:DD:EE:03", -50, "Rower X")],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        flag_path=tmp_path / "pairing.flag",
    )

    result = harness.session.start(temp_connect=False)

    commands = {c.command for c in harness.runner.calls}
    assert commands == {"scan"}
    assert harness.connect_add_calls() == []
    assert harness.connect_calls() == []
    assert harness.disconnect_all_calls() == []
    assert [c for c in harness.runner.calls if c.command == "report"] == []

    macs = {c["mac"] for c in result["candidates"]}
    assert macs == {
        "AA:BB:CC:DD:EE:01",
        "AA:BB:CC:DD:EE:02",
        "AA:BB:CC:DD:EE:03",
    }
    status_by_mac = {c["mac"]: c for c in harness.session.status()["candidates"]}
    for mac in macs:
        assert status_by_mac[mac]["connected"] is False
    assert harness.session._temp_added_by_channel == {}


def test_start_tracks_rssi_per_channel_for_a_candidate_heard_on_more_than_one(
    tmp_path,
):
    # start()'s merge used to keep only the single strongest channel per
    # candidate, discarding every other channel that also heard it -- so
    # nothing downstream could tell whether a channel other than the
    # strongest one (e.g. one with a free slot) could reach the device too.
    config = make_config()
    scan_results_by_port = {
        # uart-1 hears it strongest (-40); uart-2 hears it too, weaker (-70).
        "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:09", -40, "Vmax")],
        "/dev/ttyAMA4": [device("AA:BB:CC:DD:EE:09", -70, "Vmax")],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        flag_path=tmp_path / "pairing.flag",
    )

    result = harness.session.start(temp_connect=False)

    candidate = next(c for c in result["candidates"] if c["mac"] == "AA:BB:CC:DD:EE:09")
    # the existing single-strongest fields are unchanged...
    assert candidate["channel_id"] == "uart-1"
    assert candidate["rssi"] == -40
    # ...and rssi_by_channel now carries every channel that heard it.
    assert candidate["rssi_by_channel"] == {"uart-1": -40, "uart-2": -70}

    status_candidate = next(
        c
        for c in harness.session.status()["candidates"]
        if c["mac"] == "AA:BB:CC:DD:EE:09"
    )
    assert status_candidate["rssi_by_channel"] == {"uart-1": -40, "uart-2": -70}


# --------------------------------------------------------------------------
# bind()
# --------------------------------------------------------------------------


def _harness_ready_to_bind(tmp_path, bindings=None):
    config = make_config(bindings=bindings)
    scan_results_by_port = {
        "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:05", -40, "Bike A")],
        "/dev/ttyAMA4": [device("AA:BB:CC:DD:EE:06", -40, "Rower X")],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        flag_path=tmp_path / "pairing.flag",
    )
    harness.session.start(temp_connect=False)
    return harness


def test_bind_persists_binding_without_uart_or_restart(tmp_path):
    harness = _harness_ready_to_bind(tmp_path)
    calls_after_start = len(harness.runner.calls)

    result = harness.session.bind(
        "AA:BB:CC:DD:EE:05", "fan_bike", display_name="My Bike"
    )

    assert result["binding"]["equipment_id"] == "My Bike"
    assert result["binding"]["ble_target"] == "AA:BB:CC:DD:EE:05"
    assert result["binding"]["antenna_channel"] == "uart-1"
    assert result["binding"]["node_id"] == "fitrace-edge-01-01"
    assert "capacity" in result
    assert harness.session.state == PairingSession.STATE_OBSERVING
    # bind() issues no UART command at all
    assert len(harness.runner.calls) == calls_after_start
    assert harness.restart_calls == []
    assert harness.restore_calls == []
    saved_config = harness.config_holder["config"]
    assert any(b.equipment_id == "My Bike" for b in saved_config.equipment_bindings)


def test_bind_twice_in_one_session_persists_two_bindings_without_rescan(tmp_path):
    config = make_config()
    scan_results_by_port = {
        "/dev/ttyAMA0": [
            device("AA:BB:CC:DD:EE:01", -30, "Bike A"),
            device("AA:BB:CC:DD:EE:02", -40, "Bike B"),
        ],
        "/dev/ttyAMA4": [],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        flag_path=tmp_path / "pairing.flag",
    )
    harness.session.start(temp_connect=False)
    scan_call_count = len([c for c in harness.runner.calls if c.command == "scan"])

    first = harness.session.bind("AA:BB:CC:DD:EE:01", "fan_bike")
    second = harness.session.bind("AA:BB:CC:DD:EE:02", "rowing_machine")

    assert (
        len([c for c in harness.runner.calls if c.command == "scan"]) == scan_call_count
    )
    assert first["binding"]["node_id"] == "fitrace-edge-01-01"
    assert second["binding"]["node_id"] == "fitrace-edge-01-02"
    saved_config = harness.config_holder["config"]
    assert {b.ble_target for b in saved_config.equipment_bindings} == {
        "AA:BB:CC:DD:EE:01",
        "AA:BB:CC:DD:EE:02",
    }


def test_bind_rejects_unknown_mac(tmp_path):
    harness = _harness_ready_to_bind(tmp_path)

    with pytest.raises(PairingSessionError):
        harness.session.bind("AA:BB:CC:DD:EE:99", "fan_bike")


def test_bind_rejects_unknown_equipment_type(tmp_path):
    harness = _harness_ready_to_bind(tmp_path)

    with pytest.raises(PairingSessionError):
        harness.session.bind("AA:BB:CC:DD:EE:05", "jet_ski")


def test_bind_rejects_mac_already_bound_in_config(tmp_path):
    existing = [
        EquipmentBinding(
            node_id="fitrace-edge-01-01",
            equipment_id="Existing",
            equipment_type="fan_bike",
            ble_target="AA:BB:CC:DD:EE:05",
            antenna_channel="uart-1",
        )
    ]
    harness = _harness_ready_to_bind(tmp_path, bindings=existing)

    with pytest.raises(PairingSessionError):
        harness.session.bind("AA:BB:CC:DD:EE:05", "fan_bike")


def test_bind_rejects_rebinding_same_mac_twice_in_a_session(tmp_path):
    harness = _harness_ready_to_bind(tmp_path)
    harness.session.bind("AA:BB:CC:DD:EE:05", "fan_bike")

    with pytest.raises(PairingSessionError):
        harness.session.bind("AA:BB:CC:DD:EE:05", "rowing_machine")


def test_bind_rejects_channel_already_at_max_connections(tmp_path):
    full_bindings = [
        EquipmentBinding(
            node_id=f"fitrace-edge-01-0{i}",
            equipment_id=f"ROW_{i}",
            equipment_type="rowing_machine",
            ble_target=f"AA:BB:CC:DD:EE:1{i}",
            antenna_channel="uart-2",
        )
        for i in range(3)
    ]
    harness = _harness_ready_to_bind(tmp_path, bindings=full_bindings)

    with pytest.raises(PairingSessionError):
        harness.session.bind("AA:BB:CC:DD:EE:06", "rowing_machine")  # uart-2 is full


def test_bind_with_no_active_session_raises():
    harness = make_harness(_single_channel_config())

    with pytest.raises(PairingSessionError):
        harness.session.bind("AA:BB:CC:DD:EE:05", "fan_bike")


def test_bind_with_explicit_channel_persists_that_channel_not_the_strongest(
    tmp_path,
):
    # The live-device scenario this exists to fix: a candidate's strongest
    # (scan-time) channel is full, a different channel has room, and the
    # operator -- not just start()'s RSSI merge -- gets to choose.
    full_bindings = [
        EquipmentBinding(
            node_id=f"fitrace-edge-01-0{i}",
            equipment_id=f"ROW_{i}",
            equipment_type="rowing_machine",
            ble_target=f"AA:BB:CC:DD:EE:1{i}",
            antenna_channel="uart-1",
        )
        for i in range(3)
    ]
    config = make_config(bindings=full_bindings)
    scan_results_by_port = {
        "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:05", -40, "Bike A")],
        "/dev/ttyAMA4": [],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        flag_path=tmp_path / "pairing.flag",
    )
    harness.session.start(temp_connect=False)

    result = harness.session.bind("AA:BB:CC:DD:EE:05", "fan_bike", channel="uart-2")

    assert result["binding"]["antenna_channel"] == "uart-2"
    saved_config = harness.config_holder["config"]
    saved_binding = next(
        b
        for b in saved_config.equipment_bindings
        if b.ble_target == "AA:BB:CC:DD:EE:05"
    )
    assert saved_binding.antenna_channel == "uart-2"
    candidate = next(
        c for c in harness.session._candidates if c.mac == "AA:BB:CC:DD:EE:05"
    )
    assert candidate.bound_channel_id == "uart-2"


def test_bind_explicit_channel_does_not_need_to_have_heard_the_device(tmp_path):
    # Amended requirement: the operator may pick ANY configured channel with
    # a free slot, not only one that actually heard the candidate during the
    # (weak, 8-second) scan -- refusing an unheard channel would just
    # relocate the dead end this feature removes.
    config = make_config()
    scan_results_by_port = {
        # only uart-1 ever heard this candidate.
        "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:05", -40, "Bike A")],
        "/dev/ttyAMA4": [],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        flag_path=tmp_path / "pairing.flag",
    )
    harness.session.start(temp_connect=False)
    candidate = next(
        c for c in harness.session._candidates if c.mac == "AA:BB:CC:DD:EE:05"
    )
    assert "uart-2" not in candidate.rssi_by_channel  # confirms the premise

    result = harness.session.bind("AA:BB:CC:DD:EE:05", "fan_bike", channel="uart-2")

    assert result["binding"]["antenna_channel"] == "uart-2"


def test_bind_rejects_a_channel_that_is_not_configured(tmp_path):
    harness = _harness_ready_to_bind(tmp_path)

    with pytest.raises(PairingSessionError, match="not configured"):
        harness.session.bind("AA:BB:CC:DD:EE:05", "fan_bike", channel="uart-99")


def test_bind_rejects_an_explicit_channel_that_is_full(tmp_path):
    full_bindings = [
        EquipmentBinding(
            node_id=f"fitrace-edge-01-0{i}",
            equipment_id=f"ROW_{i}",
            equipment_type="rowing_machine",
            ble_target=f"AA:BB:CC:DD:EE:1{i}",
            antenna_channel="uart-2",
        )
        for i in range(3)
    ]
    harness = _harness_ready_to_bind(tmp_path, bindings=full_bindings)

    with pytest.raises(PairingSessionError, match="no free configured binding slot"):
        harness.session.bind("AA:BB:CC:DD:EE:05", "fan_bike", channel="uart-2")


# --------------------------------------------------------------------------
# connect()
# --------------------------------------------------------------------------


def test_connect_issues_one_connect_add_and_report_once_per_channel(tmp_path):
    config = make_config()
    scan_results_by_port = {
        "/dev/ttyAMA0": [
            device("AA:BB:CC:DD:EE:01", -30, "Bike A"),
            device("AA:BB:CC:DD:EE:02", -40, "Bike B"),
        ],
        "/dev/ttyAMA4": [],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        flag_path=tmp_path / "pairing.flag",
    )
    harness.session.start(temp_connect=False)

    first = harness.session.connect("AA:BB:CC:DD:EE:01")
    assert first == {
        "mac": "AA:BB:CC:DD:EE:01",
        "channel_id": "uart-1",
        "outcome": "ok",
        "connected": True,
    }
    assert len(harness.connect_add_calls("/dev/ttyAMA0")) == 1
    report_calls = [c for c in harness.runner.calls if c.command == "report"]
    assert len(report_calls) == 1

    second = harness.session.connect("AA:BB:CC:DD:EE:02")
    assert second["connected"] is True
    assert len(harness.connect_add_calls("/dev/ttyAMA0")) == 2
    # still just one REPORT for uart-1 -- once per channel, not per device
    report_calls = [c for c in harness.runner.calls if c.command == "report"]
    assert len(report_calls) == 1

    assert harness.connect_calls() == []
    assert harness.disconnect_all_calls() == []

    status_by_mac = {c["mac"]: c for c in harness.session.status()["candidates"]}
    assert status_by_mac["AA:BB:CC:DD:EE:01"]["connected"] is True
    assert status_by_mac["AA:BB:CC:DD:EE:02"]["connected"] is True


def test_connect_maps_replies_to_outcomes(tmp_path):
    config = make_config()
    scan_results_by_port = {
        "/dev/ttyAMA0": [
            device("AA:BB:CC:DD:EE:01", -30, "Bike A"),
            device("AA:BB:CC:DD:EE:02", -40, "Bike B"),
            device("AA:BB:CC:DD:EE:03", -50, "Bike C"),
            device("AA:BB:CC:DD:EE:04", -60, "Bike D"),
        ],
        "/dev/ttyAMA4": [],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        connect_add_replies_by_port={
            "/dev/ttyAMA0": [
                [{"type": "ok", "command": "CONNECT_ADD"}],
                [{"type": "error", "message": "CONNECT_ADD:ERROR:ALREADY_EXISTS"}],
                [{"type": "error", "message": "CONNECT_ADD:ERROR:FULL"}],
                [{"type": "error", "message": "ERROR:UNKNOWN_CMD:CONNECT_ADD"}],
            ],
        },
        flag_path=tmp_path / "pairing.flag",
    )
    harness.session.start(temp_connect=False)

    ok = harness.session.connect("AA:BB:CC:DD:EE:01")
    assert ok["outcome"] == "ok"
    assert ok["connected"] is True

    already = harness.session.connect("AA:BB:CC:DD:EE:02")
    assert already["outcome"] == "already_exists"
    assert already["connected"] is True

    full = harness.session.connect("AA:BB:CC:DD:EE:03")
    assert full["outcome"] == "full"
    assert full["connected"] is False

    unknown = harness.session.connect("AA:BB:CC:DD:EE:04")
    assert unknown["outcome"] == "unknown_cmd"
    assert unknown["connected"] is False

    # never falls back to a batch connect or touches other channels
    assert harness.connect_calls() == []
    assert harness.disconnect_all_calls() == []


def test_connect_rejects_unknown_mac(tmp_path):
    harness = _harness_ready_to_bind(tmp_path)

    with pytest.raises(PairingSessionError):
        harness.session.connect("AA:BB:CC:DD:EE:99")


def test_connect_with_no_active_session_raises():
    harness = make_harness(_single_channel_config())

    with pytest.raises(PairingSessionError):
        harness.session.connect("AA:BB:CC:DD:EE:05")


def test_connect_targets_the_bound_channel_when_it_differs_from_the_strongest(
    tmp_path,
):
    # bind()'s optional channel argument can now persist a binding on a
    # channel other than start()'s strongest-RSSI pick -- connect() must
    # follow the binding, or it would CONNECT_ADD the wrong board (the one
    # that's still full) instead of the one the operator actually chose.
    full_bindings = [
        EquipmentBinding(
            node_id=f"fitrace-edge-01-0{i}",
            equipment_id=f"ROW_{i}",
            equipment_type="rowing_machine",
            ble_target=f"AA:BB:CC:DD:EE:1{i}",
            antenna_channel="uart-1",
        )
        for i in range(3)
    ]
    config = make_config(bindings=full_bindings)
    scan_results_by_port = {
        "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:05", -40, "Bike A")],  # strongest
        "/dev/ttyAMA4": [],
    }
    harness = make_harness(
        config,
        scan_results_by_port=scan_results_by_port,
        flag_path=tmp_path / "pairing.flag",
    )
    harness.session.start(temp_connect=False)
    harness.session.bind("AA:BB:CC:DD:EE:05", "fan_bike", channel="uart-2")

    result = harness.session.connect("AA:BB:CC:DD:EE:05")

    assert result["channel_id"] == "uart-2"
    # exactly one connect_add, on uart-2's port, never on uart-1's (the
    # strongest-RSSI channel candidate.channel_id still points at, and
    # which is full anyway).
    assert len(harness.connect_add_calls("/dev/ttyAMA4")) == 1
    assert harness.connect_add_calls("/dev/ttyAMA0") == []


# --------------------------------------------------------------------------
# finish()
# --------------------------------------------------------------------------


def test_finish_restarts_by_default_and_resets_session(tmp_path):
    flag_path = tmp_path / "pairing.flag"
    harness = make_harness(
        make_config(),
        scan_results_by_port={"/dev/ttyAMA0": [], "/dev/ttyAMA4": []},
        flag_path=flag_path,
    )
    harness.session.start(temp_connect=False)
    assert flag_path.exists()
    calls_before = len(harness.runner.calls)

    result = harness.session.finish()

    assert result["status"] == "finished"
    assert result["restart"] == {"dry_run": True, "executed": False}
    assert len(harness.restart_calls) == 1
    assert not flag_path.exists()
    assert harness.session.state == PairingSession.STATE_IDLE
    # finish() issues no UART commands
    assert len(harness.runner.calls) == calls_before


def test_finish_skips_restart_when_requested(tmp_path):
    flag_path = tmp_path / "pairing.flag"
    harness = make_harness(
        make_config(),
        scan_results_by_port={"/dev/ttyAMA0": [], "/dev/ttyAMA4": []},
        flag_path=flag_path,
    )
    harness.session.start(temp_connect=False)

    result = harness.session.finish(restart=False)

    assert result["restart"] is None
    assert harness.restart_calls == []
    assert harness.session.state == PairingSession.STATE_IDLE
    assert not flag_path.exists()


def test_finish_with_no_active_session_raises():
    harness = make_harness(_single_channel_config())

    with pytest.raises(PairingSessionError):
        harness.session.finish()


# --------------------------------------------------------------------------
# connect_add timeout verification
# --------------------------------------------------------------------------


def test_start_scan_uses_long_command_timeout(tmp_path):
    """Verify that start()'s scan commands use the full command_timeout_sec."""
    harness = make_harness(
        make_config(),
        scan_results_by_port={
            "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:01", -40, "Bike")],
            "/dev/ttyAMA4": [],
        },
        flag_path=tmp_path / "pairing.flag",
    )

    harness.session.start(temp_connect=False)

    scan_calls = [c for c in harness.runner.calls if c.command == "scan"]
    assert len(scan_calls) >= 1
    # All scan calls should use the long command_timeout_sec (5.0 by default)
    for call in scan_calls:
        assert call.timeout_sec == 5.0


def test_connect_uses_short_connect_add_timeout(tmp_path):
    """Verify that connect() issues connect_add with the CONNECT_ADD_ACK_TIMEOUT_SEC timeout."""
    from edge_node.usecases.pairing_session import CONNECT_ADD_ACK_TIMEOUT_SEC

    config = make_config()
    harness = make_harness(
        config,
        scan_results_by_port={
            "/dev/ttyAMA0": [device("AA:BB:CC:DD:EE:01", -40, "Bike")],
            "/dev/ttyAMA4": [],
        },
        flag_path=tmp_path / "pairing.flag",
    )
    harness.session.start(temp_connect=False)
    harness.session.bind("AA:BB:CC:DD:EE:01", "fan_bike")

    harness.session.connect("AA:BB:CC:DD:EE:01")

    connect_add_calls = [c for c in harness.runner.calls if c.command == "connect_add"]
    # The connect() call should issue exactly one connect_add
    assert len(connect_add_calls) >= 1
    # The connect_add should use the shorter CONNECT_ADD_ACK_TIMEOUT_SEC
    latest_connect_add = connect_add_calls[-1]
    assert latest_connect_add.timeout_sec == CONNECT_ADD_ACK_TIMEOUT_SEC
    assert CONNECT_ADD_ACK_TIMEOUT_SEC < 5.0  # verify it's actually shorter


# --------------------------------------------------------------------------
# Regression lock: full scan-first session never goes destructive
# --------------------------------------------------------------------------


def test_full_scan_first_session_never_issues_batch_connect_or_disconnect_all(
    tmp_path,
):
    config = make_config()
    scan_results_by_port = {
        "/dev/ttyAMA0": [
            device("AA:BB:CC:DD:EE:01", -30, "Bike A"),
            device("AA:BB:CC:DD:EE:02", -40, "Bike B"),
        ],
        "/dev/ttyAMA4": [],
    }
    flag_path = tmp_path / "pairing.flag"
    harness = make_harness(
        config, scan_results_by_port=scan_results_by_port, flag_path=flag_path
    )

    harness.session.start(temp_connect=False)
    harness.session.bind("AA:BB:CC:DD:EE:01", "fan_bike")
    harness.session.connect("AA:BB:CC:DD:EE:01")
    harness.session.bind("AA:BB:CC:DD:EE:02", "rowing_machine")
    harness.session.connect("AA:BB:CC:DD:EE:02")
    result = harness.session.finish()

    commands_issued = [c.command for c in harness.runner.calls]
    # the whole point of this flow: never a batch CONNECT, never a
    # DISCONNECT:ALL, on any channel, at any point in the session
    assert "connect" not in commands_issued
    assert "disconnect_all" not in commands_issued
    assert "disconnect" not in commands_issued
    # restore_configured_devices is the OTHER way a destructive
    # DISCONNECT:ALL + batch CONNECT can reach the boards -- it goes through
    # the injected collaborator, not command_runner, so the command-list
    # assertions above cannot see it. This flow must never call it.
    assert harness.restore_calls == []
    assert commands_issued.count("scan") == 2  # one per configured channel
    assert commands_issued.count("connect_add") == 2
    assert commands_issued.count("report") == 1  # both devices share uart-1

    assert result["status"] == "finished"
    assert not flag_path.exists()
    assert harness.session.state == PairingSession.STATE_IDLE
    saved_config = harness.config_holder["config"]
    assert {b.ble_target for b in saved_config.equipment_bindings} == {
        "AA:BB:CC:DD:EE:01",
        "AA:BB:CC:DD:EE:02",
    }
