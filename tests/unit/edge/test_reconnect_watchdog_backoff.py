"""Watchdog behavior for `_reconnect_missing_targets`.

CONNECT is destructive on the nRF52832 antenna board: it always performs a
full disconnect-all, then scans and reconnects the given list from scratch
(~10-20s per device). It is a "reset target list and reconnect everything"
command, not incremental. If the watchdog reissues CONNECT every time STATUS
shows `connected < expected`, it destroys an in-progress reconnection every
reconnect-interval and the board can loop forever after a power-cycle. These
tests pin down the cooldown/backoff logic that prevents that.
"""

import logging
import os
import time

from edge_node.domain.models import (
    AntennaChannelConfig,
    EdgeNodeConfig,
    EquipmentBinding,
)
from edge_node.usecases.antenna_ftms_manager import (
    CONNECT_COOLDOWN_SEC,
    AntennaFtmsManager,
)
from edge_node.usecases.pairing_session import FLAG_STALE_AFTER_SEC


class FakeSerial:
    """Same shape as the FakeSerial in test_antenna_ftms_manager.py: a
    command string maps to canned response lines, replayed on every write of
    that exact command (the dict entry is never consumed)."""

    def __init__(self, responses):
        self.responses = {
            command: [line.encode("ascii") for line in lines]
            for command, lines in responses.items()
        }
        self.lines = []
        self.writes = []
        self.closed = False

    def write(self, data):
        command = data.decode("ascii")
        self.writes.append(command)
        self.lines.extend(self.responses.get(command, []))

    def readline(self):
        if not self.lines:
            return b""
        return self.lines.pop(0)

    def close(self):
        self.closed = True


class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float):
        self._now += seconds


def make_manager(
    serial,
    clock,
    ble_target="AA:BB:CC:DD:EE:01",
    config_loader=None,
):
    channels = [AntennaChannelConfig(id="uart-1", port="/dev/ttyAMA0")]
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        antenna_channels=channels,
        equipment_bindings=[
            EquipmentBinding(
                node_id="fitrace-edge-01-tread-01",
                equipment_id="TREAD_01",
                equipment_type="treadmill",
                ble_target=ble_target,
                antenna_channel="uart-1",
            )
        ],
    )

    async def on_telemetry(_telemetry):
        pass

    manager_kwargs = {}
    if config_loader is not None:
        manager_kwargs["config_loader"] = config_loader
    manager = AntennaFtmsManager(
        edge_config=config,
        on_telemetry=on_telemetry,
        serial_factory=lambda channel: serial,
        command_timeout_sec=0.05,
        clock=clock,
        **manager_kwargs,
    )
    manager._serials = {"uart-1": serial}
    return manager


def connect_writes(serial):
    return [w for w in serial.writes if w.startswith("CONNECT:")]


def test_matching_board_target_does_not_resend_connect(caplog):
    serial = FakeSerial(
        {
            "CONNECT:AA:BB:CC:DD:EE:01;\r\n": ["CONNECT:OK;\r\n"],
            "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
            "STATUS;\r\n": ["STATUS:IDLE,0/1;\r\n"],
        }
    )
    clock = FakeClock()
    manager = make_manager(serial, clock)

    # Prime as if a prior CONNECT already pushed this exact list (startup or
    # an earlier watchdog pass), so the board's target list is up to date.
    manager._connect_assignments({"uart-1": ["AA:BB:CC:DD:EE:01"]})
    serial.writes.clear()

    with caplog.at_level(logging.INFO, logger="edge_node.antenna_ftms_manager"):
        manager._reconnect_missing_targets()

    # target=1 == len(expected)=1: the board already holds the right list and
    # is auto-reconnecting on its own; resending CONNECT would tear it down.
    assert connect_writes(serial) == []
    assert any(
        "waiting for board auto-reconnect" in record.message
        for record in caplog.records
    )


def test_cold_start_trusts_matching_board_target_without_prior_connect(caplog):
    serial = FakeSerial(
        {
            "STATUS;\r\n": ["STATUS:IDLE,0/1;\r\n"],
        }
    )
    clock = FakeClock()
    manager = make_manager(serial, clock)
    manager._saved_list_channels = {"uart-1"}

    with caplog.at_level(logging.INFO, logger="edge_node.antenna_ftms_manager"):
        manager._reconnect_missing_targets()

    assert connect_writes(serial) == []
    assert any(
        "waiting for board auto-reconnect" in record.message
        for record in caplog.records
    )


def test_config_change_during_watchdog_prevents_reconnecting_removed_mac():
    serial = FakeSerial(
        {
            "STATUS;\r\n": ["STATUS:IDLE,0/0;\r\n"],
            "CONNECT:AA:BB:CC:DD:EE:01;\r\n": ["CONNECT:OK;\r\n"],
            "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
        }
    )
    clock = FakeClock()
    configs = []

    def load_config():
        return configs.pop(0) if len(configs) > 1 else configs[0]

    manager = make_manager(serial, clock, config_loader=load_config)
    old_config = manager._edge_config.model_copy(deep=True)
    cleared_config = manager._edge_config.model_copy(deep=True)
    cleared_config.equipment_bindings = []
    configs.extend([old_config, cleared_config])

    manager._reconnect_missing_targets()

    assert connect_writes(serial) == []
    assert manager._edge_config.equipment_bindings == []


def test_config_reload_failure_skips_stale_reconnect(caplog):
    serial = FakeSerial(
        {
            "STATUS;\r\n": ["STATUS:IDLE,0/0;\r\n"],
            "CONNECT:AA:BB:CC:DD:EE:01;\r\n": ["CONNECT:OK;\r\n"],
            "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
        }
    )
    clock = FakeClock()

    def load_config():
        raise OSError("config temporarily unavailable")

    manager = make_manager(serial, clock, config_loader=load_config)

    with caplog.at_level(logging.WARNING, logger="edge_node.antenna_ftms_manager"):
        manager._reconnect_missing_targets()

    assert serial.writes == []
    assert any(
        "skipping reconnect watchdog" in record.message for record in caplog.records
    )


def test_target_mismatch_sends_connect_once_then_cools_down():
    serial = FakeSerial(
        {
            "CONNECT:AA:BB:CC:DD:EE:01;\r\n": ["CONNECT:OK;\r\n"],
            "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
            "STATUS;\r\n": ["STATUS:IDLE,0/0;\r\n"],
        }
    )
    clock = FakeClock()
    manager = make_manager(serial, clock)

    # First pass: board reports target=0 (e.g. after NVS wipe) -- genuine
    # "board lost its list" case, must resend.
    manager._reconnect_missing_targets()
    assert len(connect_writes(serial)) == 1

    # Second pass immediately after: same expected list, board still shows
    # target=0, cooldown has not elapsed -> must NOT resend.
    manager._reconnect_missing_targets()
    assert len(connect_writes(serial)) == 1


def test_connect_resumes_once_cooldown_elapses():
    serial = FakeSerial(
        {
            "CONNECT:AA:BB:CC:DD:EE:01;\r\n": ["CONNECT:OK;\r\n"],
            "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
            "STATUS;\r\n": ["STATUS:IDLE,0/0;\r\n"],
        }
    )
    clock = FakeClock()
    manager = make_manager(serial, clock)

    manager._reconnect_missing_targets()
    assert len(connect_writes(serial)) == 1

    # Still within cooldown: no second CONNECT.
    clock.advance(CONNECT_COOLDOWN_SEC - 1)
    manager._reconnect_missing_targets()
    assert len(connect_writes(serial)) == 1

    # Cooldown fully elapsed: CONNECT may be sent again.
    clock.advance(2)
    manager._reconnect_missing_targets()
    assert len(connect_writes(serial)) == 2


def test_expected_list_change_bypasses_cooldown_and_matching_target():
    serial = FakeSerial(
        {
            "CONNECT:AA:BB:CC:DD:EE:01;\r\n": ["CONNECT:OK;\r\n"],
            "CONNECT:AA:BB:CC:DD:EE:02;\r\n": ["CONNECT:OK;\r\n"],
            "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
            # target now matches the NEW expected count (1), which would
            # normally mean "don't resend" -- but the MAC itself changed.
            "STATUS;\r\n": ["STATUS:IDLE,0/1;\r\n"],
        }
    )
    clock = FakeClock()
    # Manager is configured for MAC ...:02, but we prime the "last pushed"
    # state as if ...:01 had been pushed a moment ago (simulating a config
    # change since the last push): same count, different MAC.
    manager = make_manager(serial, clock, ble_target="AA:BB:CC:DD:EE:02")
    manager._connect_assignments({"uart-1": ["AA:BB:CC:DD:EE:01"]})
    serial.writes.clear()

    # No time has passed at all (well within cooldown) and the board already
    # reports a target count equal to the new expected count -- neither of
    # which may suppress the resend, because STATUS can't reveal a MAC swap.
    manager._reconnect_missing_targets()

    sent = connect_writes(serial)
    assert len(sent) == 1
    assert "AA:BB:CC:DD:EE:02" in sent[0]


def test_fresh_pairing_flag_pauses_the_whole_watchdog_pass(
    monkeypatch, tmp_path, caplog
):
    # A pairing session's temp CONNECT list looks exactly like "the expected
    # list changed" to the logic below (same scenario as the test above),
    # which would normally bypass cooldown and reissue CONNECT immediately.
    # A fresh pairing flag must suppress the entire pass -- not even STATUS
    # should be sent -- so the session's temp-connected candidates are left
    # alone.
    flag_path = tmp_path / "pairing.flag"
    flag_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FITRACE_PAIRING_FLAG_PATH", str(flag_path))

    serial = FakeSerial(
        {
            "CONNECT:AA:BB:CC:DD:EE:01;\r\n": ["CONNECT:OK;\r\n"],
            "CONNECT:AA:BB:CC:DD:EE:02;\r\n": ["CONNECT:OK;\r\n"],
            "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
            "STATUS;\r\n": ["STATUS:IDLE,0/1;\r\n"],
        }
    )
    clock = FakeClock()
    manager = make_manager(serial, clock, ble_target="AA:BB:CC:DD:EE:02")
    manager._connect_assignments({"uart-1": ["AA:BB:CC:DD:EE:01"]})
    serial.writes.clear()

    with caplog.at_level(logging.INFO, logger="edge_node.antenna_ftms_manager"):
        manager._reconnect_missing_targets()

    assert connect_writes(serial) == []
    assert not any(w == "STATUS;\r\n" for w in serial.writes)
    assert any(
        "skipping reconnect watchdog" in record.message for record in caplog.records
    )


def test_stale_pairing_flag_lets_the_watchdog_run_normally(monkeypatch, tmp_path):
    flag_path = tmp_path / "pairing.flag"
    flag_path.write_text("{}", encoding="utf-8")
    stale_time = time.time() - FLAG_STALE_AFTER_SEC - 1
    os.utime(flag_path, (stale_time, stale_time))
    monkeypatch.setenv("FITRACE_PAIRING_FLAG_PATH", str(flag_path))

    serial = FakeSerial(
        {
            "CONNECT:AA:BB:CC:DD:EE:01;\r\n": ["CONNECT:OK;\r\n"],
            "CONNECT:AA:BB:CC:DD:EE:02;\r\n": ["CONNECT:OK;\r\n"],
            "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
            "STATUS;\r\n": ["STATUS:IDLE,0/1;\r\n"],
        }
    )
    clock = FakeClock()
    manager = make_manager(serial, clock, ble_target="AA:BB:CC:DD:EE:02")
    manager._connect_assignments({"uart-1": ["AA:BB:CC:DD:EE:01"]})
    serial.writes.clear()

    manager._reconnect_missing_targets()

    assert connect_writes(serial) == ["CONNECT:AA:BB:CC:DD:EE:02;\r\n"]
