"""Per-device reconnect: the operator card's way out of "waiting".

A machine that drops its BLE link sits at "waiting" on the home cards, and
the only control there used to be "remove" -- which frees the antenna slot
and forces a re-pair for what is really a one-command problem. This endpoint
re-links exactly that machine, and must never reach for the whole-list
commands that would drop its channel-mates.
"""

import json

from fastapi.testclient import TestClient

from edge_node.infrastructure.fastapi import app as edge_app_module


class FakeAntennaCommandRunner:
    def __init__(self):
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        return {
            "port": request.port,
            "command": request.command,
            "rx": [f"{request.command}:OK"],
        }


def _install_config(monkeypatch, tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "node_id": "fitrace-edge-test",
                "mqtt_host": "192.168.0.130",
                "max_ftms_connections": 3,
                "antenna_channels": [
                    {"id": "uart-1", "port": "/dev/ttyAMA0", "baudrate": 115200},
                    {
                        "id": "uart-2",
                        "port": "/dev/ttyAMA4",
                        "baudrate": 57600,
                        "rtscts": True,
                    },
                ],
                "equipment_bindings": [
                    {
                        "node_id": "fitrace-edge-test-01",
                        "equipment_id": "Vmax21112",
                        "equipment_type": "fan_bike",
                        "ble_target": "AA:BB:CC:DD:EE:01",
                        "antenna_channel": "uart-1",
                    },
                    {
                        "node_id": "fitrace-edge-test-02",
                        "equipment_id": "Vmax190B2",
                        "equipment_type": "spin_bike",
                        "ble_target": "AA:BB:CC:DD:EE:02",
                        "antenna_channel": "uart-1",
                    },
                    {
                        "node_id": "fitrace-edge-test-03",
                        "equipment_id": "Vmax26_5E5",
                        "equipment_type": "spin_bike",
                        "ble_target": "AA:BB:CC:DD:EE:03",
                        "antenna_channel": "uart-2",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    runner = FakeAntennaCommandRunner()
    monkeypatch.setattr(edge_app_module, "CONFIG_PATH", config_path)
    monkeypatch.setattr(edge_app_module, "antenna_command_runner", runner)
    return TestClient(edge_app_module.app), runner


def test_reconnect_device_relinks_only_that_machine(monkeypatch, tmp_path):
    client, runner = _install_config(monkeypatch, tmp_path)

    response = client.post(
        "/api/antenna/reconnect-device",
        json={"node_id": "fitrace-edge-test-02", "report_interval_ms": 500},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["node_id"] == "fitrace-edge-test-02"
    assert payload["channel_id"] == "uart-1"
    assert payload["mac"] == "AA:BB:CC:DD:EE:02"

    assert [request.command for request in runner.requests] == [
        "disconnect",
        "connect_add",
        "report",
    ]
    assert runner.requests[0].macs == ["AA:BB:CC:DD:EE:02"]
    assert runner.requests[1].macs == ["AA:BB:CC:DD:EE:02"]
    assert runner.requests[-1].report_interval_ms == 500
    assert {request.port for request in runner.requests} == {"/dev/ttyAMA0"}


def test_reconnect_device_uses_the_channels_own_uart_settings(monkeypatch, tmp_path):
    client, runner = _install_config(monkeypatch, tmp_path)

    client.post(
        "/api/antenna/reconnect-device", json={"node_id": "fitrace-edge-test-03"}
    )

    assert {request.port for request in runner.requests} == {"/dev/ttyAMA4"}
    assert runner.requests[0].baudrate == 57600
    assert runner.requests[0].rtscts is True


def test_reconnect_device_rejects_an_unknown_node_without_touching_the_uart(
    monkeypatch, tmp_path
):
    client, runner = _install_config(monkeypatch, tmp_path)

    response = client.post(
        "/api/antenna/reconnect-device", json={"node_id": "fitrace-edge-test-99"}
    )

    assert response.status_code == 400
    assert runner.requests == []


def test_reconnect_device_requires_the_admin_token_when_one_is_configured(
    monkeypatch, tmp_path
):
    client, runner = _install_config(monkeypatch, tmp_path)
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "s3cret")

    unauthorized = client.post(
        "/api/antenna/reconnect-device", json={"node_id": "fitrace-edge-test-01"}
    )
    assert unauthorized.status_code == 401
    assert runner.requests == []

    authorized = client.post(
        "/api/antenna/reconnect-device",
        json={"node_id": "fitrace-edge-test-01"},
        headers={"X-FitRace-Admin-Token": "s3cret"},
    )
    assert authorized.status_code == 200
    assert [request.command for request in runner.requests] == [
        "disconnect",
        "connect_add",
        "report",
    ]
