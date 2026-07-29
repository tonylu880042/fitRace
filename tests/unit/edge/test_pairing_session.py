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


def test_confirm_persists_config_disconnects_temp_extras_restarts_and_clears_flag(
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
    # the only temp-added MAC is the one just confirmed, so nothing needed
    # disconnecting and restore-configured-devices was never called (its
    # channel used incremental CONNECT_ADD, never CONNECT).
    assert result["reconnect"] == {"status": "disconnected", "channels": []}

    # config was persisted through save_config with the new binding present
    saved_config = harness.config_holder["config"]
    assert any(b.equipment_id == "My Bike" for b in saved_config.equipment_bindings)
    assert harness.restore_calls == []
    assert len(harness.restart_calls) == 1

    assert not flag_path.exists()
    assert harness.session.state == PairingSession.STATE_IDLE


def test_confirm_disconnects_every_temp_mac_except_the_confirmed_one(tmp_path):
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
    disconnects = harness.disconnect_calls("/dev/ttyAMA0")
    assert sorted(c.macs[0] for c in disconnects) == [
        "AA:BB:CC:DD:EE:01",
        "AA:BB:CC:DD:EE:03",
    ]
    assert harness.restore_calls == []


def test_confirm_retry_after_restore_failure_does_not_duplicate_binding(tmp_path):
    # Force uart-1 through the destructive-fallback path (pre-v1.3.0
    # firmware) so confirm()'s teardown actually calls
    # restore_configured_devices and the flaky/retry behaviour below is
    # meaningful -- the plain incremental CONNECT_ADD path never calls it.
    harness = _harness_ready_to_confirm(
        tmp_path,
        connect_add_replies_by_port={
            "/dev/ttyAMA0": [
                [{"type": "error", "message": "ERROR:UNKNOWN_CMD:CONNECT_ADD"}]
            ],
        },
    )
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
