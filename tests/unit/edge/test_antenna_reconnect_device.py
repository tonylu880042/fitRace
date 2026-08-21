"""Reconnecting ONE device must not disturb the others on its channel.

The operator card used to offer only "remove", so a machine sitting at
"waiting" could be re-linked only by unbinding and pairing it again. The
runtime deliberately leaves such a board alone -- resending CONNECT would
tear down the firmware's in-progress auto-reconnect (see
antenna_ftms_manager's watchdog) -- so the manual override needs a command
pair that targets a single MAC: DISCONNECT <mac> then CONNECT_ADD <mac>.

The whole-list commands are what must never appear here. CONNECT replaces a
channel's target list and DISCONNECT:ALL clears it, so either one would drop
the other machines on the same antenna board -- the exact accident the
operator is trying to avoid by not removing the binding.
"""

import pytest

from edge_node.domain.models import EdgeNodeConfig
from edge_node.usecases.antenna_reconnect import reconnect_single_device


class FakeRunner:
    def __init__(self):
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return {
            "port": request.port,
            "command": request.command,
            "parsed": [{"type": "ok", "command": request.command.upper()}],
        }

    @property
    def commands(self):
        return [request.command for request in self.requests]


def _config():
    return EdgeNodeConfig.model_validate(
        {
            "node_id": "fitrace-edge-01",
            "antenna_channels": [
                {"id": "uart-1", "port": "/dev/ttyAMA0", "baudrate": 115200},
                {"id": "uart-2", "port": "/dev/ttyAMA4", "baudrate": 115200},
            ],
            "equipment_bindings": [
                {
                    "node_id": "fitrace-edge-01-01",
                    "equipment_id": "Vmax21112",
                    "equipment_type": "fan_bike",
                    "ble_target": "D6:34:A6:DF:35:B0",
                    "antenna_channel": "uart-1",
                },
                {
                    "node_id": "fitrace-edge-01-02",
                    "equipment_id": "Vmax190B2",
                    "equipment_type": "spin_bike",
                    "ble_target": "CA:1B:69:AB:E5:51",
                    "antenna_channel": "uart-1",
                },
                {
                    "node_id": "fitrace-edge-01-03",
                    "equipment_id": "Vmax26_5E5",
                    "equipment_type": "spin_bike",
                    "ble_target": "D5:8C:35:FA:5E:56",
                    "antenna_channel": "uart-2",
                },
            ],
        }
    )


def test_reconnect_targets_only_the_requested_device():
    runner = FakeRunner()

    result = reconnect_single_device(_config(), runner, "fitrace-edge-01-01")

    assert runner.commands == ["disconnect", "connect_add", "report"]
    assert all(request.port == "/dev/ttyAMA0" for request in runner.requests)
    assert runner.requests[0].macs == ["D6:34:A6:DF:35:B0"]
    assert runner.requests[1].macs == ["D6:34:A6:DF:35:B0"]
    assert result["node_id"] == "fitrace-edge-01-01"
    assert result["channel_id"] == "uart-1"


def test_reconnect_never_uses_the_whole_list_commands():
    """CONNECT and DISCONNECT:ALL would knock the channel's other machines
    off the board -- the failure the operator is trying to avoid."""
    runner = FakeRunner()

    reconnect_single_device(_config(), runner, "fitrace-edge-01-02")

    assert "connect" not in runner.commands
    assert "disconnect_all" not in runner.commands


def test_reconnect_leaves_the_other_channel_untouched():
    runner = FakeRunner()

    reconnect_single_device(_config(), runner, "fitrace-edge-01-02")

    assert {request.port for request in runner.requests} == {"/dev/ttyAMA0"}


def test_reconnect_uses_the_requested_report_interval():
    runner = FakeRunner()

    reconnect_single_device(
        _config(), runner, "fitrace-edge-01-03", report_interval_ms=500
    )

    report = runner.requests[-1]
    assert report.command == "report"
    assert report.port == "/dev/ttyAMA4"
    assert report.report_interval_ms == 500


def test_unknown_node_id_is_rejected_before_any_uart_traffic():
    runner = FakeRunner()

    with pytest.raises(ValueError):
        reconnect_single_device(_config(), runner, "fitrace-edge-01-99")

    assert runner.requests == []


def test_a_binding_with_no_channel_is_rejected_before_any_uart_traffic():
    config = _config()
    config.equipment_bindings[0].antenna_channel = None
    runner = FakeRunner()

    with pytest.raises(ValueError):
        reconnect_single_device(config, runner, "fitrace-edge-01-01")

    assert runner.requests == []


def test_a_binding_pointing_at_a_missing_channel_is_rejected():
    config = _config()
    config.equipment_bindings[0].antenna_channel = "uart-9"
    runner = FakeRunner()

    with pytest.raises(ValueError):
        reconnect_single_device(config, runner, "fitrace-edge-01-01")

    assert runner.requests == []
