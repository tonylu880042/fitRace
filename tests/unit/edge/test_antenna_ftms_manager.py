import asyncio

import pytest

from edge_node.domain.models import AntennaChannelConfig, EdgeNodeConfig, EquipmentBinding
from edge_node.usecases.event_log import EdgeEventLog
from edge_node.usecases.antenna_ftms_manager import (
    AntennaFtmsManager,
    ScannedDevice,
    assign_devices_by_rssi,
    bind_assignments_to_streams,
    filter_assignments_to_configured_macs,
)


def make_channels():
    return [
        AntennaChannelConfig(id="uart-1", port="/dev/ttyAMA0"),
        AntennaChannelConfig(id="uart-2", port="/dev/ttyAMA4"),
    ]


def test_assign_devices_by_rssi_balances_close_readings():
    channels = make_channels()
    scan_results = {
        "uart-1": [
            ScannedDevice("AA:BB:CC:DD:EE:01", -55, "Bike 1", "BIKE"),
            ScannedDevice("AA:BB:CC:DD:EE:02", -61, "Bike 2", "BIKE"),
            ScannedDevice("AA:BB:CC:DD:EE:03", -73, "Bike 3", "BIKE"),
        ],
        "uart-2": [
            ScannedDevice("AA:BB:CC:DD:EE:01", -57, "Bike 1", "BIKE"),
            ScannedDevice("AA:BB:CC:DD:EE:02", -60, "Bike 2", "BIKE"),
            ScannedDevice("AA:BB:CC:DD:EE:03", -74, "Bike 3", "BIKE"),
        ],
    }

    assignments = assign_devices_by_rssi(scan_results, channels, tie_threshold_db=5)

    assert assignments == {
        "uart-1": ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:03"],
        "uart-2": ["AA:BB:CC:DD:EE:02"],
    }


def test_antenna_manager_emits_raw_totals_and_deltas_by_mac():
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        antenna_channels=[AntennaChannelConfig(id="uart-1", port="/dev/ttyAMA0")],
        equipment_bindings=[
            EquipmentBinding(
                node_id="fitrace-edge-01-01",
                equipment_id="TREAD_01",
                equipment_type="treadmill",
                ble_target="AA:BB:CC:DD:EE:01",
                antenna_channel="uart-1",
            )
        ],
    )

    async def on_telemetry(_telemetry):
        pass

    manager = AntennaFtmsManager(edge_config=config, on_telemetry=on_telemetry)
    first = manager._to_telemetry(
        "uart-1",
        {
            "address": "AA:BB:CC:DD:EE:01",
            "equipment_type": "treadmill",
            "ftms_type": "TMILL",
            "distance_m": 1000.0,
            "total_energy_kcal": 50,
        },
    )
    second = manager._to_telemetry(
        "uart-1",
        {
            "address": "AA:BB:CC:DD:EE:01",
            "equipment_type": "treadmill",
            "ftms_type": "TMILL",
            "distance_m": 1003.5,
            "total_energy_kcal": 52,
        },
    )

    assert first.raw_total_distance_m == 1000.0
    assert first.delta_distance_m == 0.0
    assert first.raw_total_energy_kcal == 50.0
    assert first.delta_energy_kcal == 0.0
    assert second.raw_total_distance_m == 1003.5
    assert second.delta_distance_m == 3.5
    assert second.raw_total_energy_kcal == 52.0
    assert second.delta_energy_kcal == 2.0


def test_bind_assignments_prefers_configured_mac_targets():
    assignments = {
        "uart-1": ["AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:01"],
    }
    bindings = [
        EquipmentBinding(
            node_id="fitrace-edge-01-01",
            equipment_id="BIKE_01",
            equipment_type="fan_bike",
            ble_target="AA:BB:CC:DD:EE:01",
            antenna_channel="uart-1",
        ),
        EquipmentBinding(
            node_id="fitrace-edge-01-02",
            equipment_id="BIKE_02",
            equipment_type="fan_bike",
            ble_target="AA:BB:CC:DD:EE:02",
            antenna_channel="uart-1",
        ),
    ]

    bindings_by_mac = bind_assignments_to_streams(
        assignments,
        bindings,
        "fitrace-edge-01",
    )

    assert bindings_by_mac["AA:BB:CC:DD:EE:01"].node_id == "fitrace-edge-01-01"
    assert bindings_by_mac["AA:BB:CC:DD:EE:02"].node_id == "fitrace-edge-01-02"


def test_filter_assignments_to_configured_macs_removes_stale_saved_targets():
    assignments = {
        "uart-1": ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:99"],
        "uart-2": ["AA:BB:CC:DD:EE:02"],
    }
    bindings = [
        EquipmentBinding(
            node_id="fitrace-edge-01-bike-01",
            equipment_id="BIKE_01",
            equipment_type="fan_bike",
            ble_target="AA:BB:CC:DD:EE:01",
            antenna_channel="uart-1",
        ),
        EquipmentBinding(
            node_id="fitrace-edge-01-bike-02",
            equipment_id="BIKE_02",
            equipment_type="fan_bike",
            ble_target="AA:BB:CC:DD:EE:02",
            antenna_channel="uart-2",
        ),
    ]

    filtered = filter_assignments_to_configured_macs(assignments, bindings)

    assert filtered == {
        "uart-1": ["AA:BB:CC:DD:EE:01"],
        "uart-2": ["AA:BB:CC:DD:EE:02"],
    }


class FakeSerial:
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


@pytest.mark.asyncio
async def test_antenna_manager_scans_connects_and_emits_telemetry():
    channels = make_channels()
    serials = {
        "uart-1": FakeSerial(
            {
                "PING;\r\n": ["BOOT:NO_LIST;\r\n"],
                "SCAN:START;\r\n": [
                    "SCAN:OK;\r\n",
                    "DEVICE:AA:BB:CC:DD:EE:01,-55,Bike 1,BIKE;\r\n",
                ],
                "SCAN:STOP;\r\n": ["SCAN:OK;\r\n"],
                "CONNECT:AA:BB:CC:DD:EE:01;\r\n": ["CONNECT:OK;\r\n"],
                "REPORT:250;\r\n": [
                    "REPORT:OK;\r\n",
                    'FTMS:AA:BB:CC:DD:EE:01,BIKE,{"rssi":-55,"instantaneous_speed":24.5,"total_distance":10,"instantaneous_power":120,"total_energy":3};\r\n',
                ],
            }
        ),
        "uart-2": FakeSerial(
            {
                "PING;\r\n": ["BOOT:NO_LIST;\r\n"],
                "SCAN:START;\r\n": ["SCAN:OK;\r\n"],
                "SCAN:STOP;\r\n": ["SCAN:OK;\r\n"],
            }
        ),
    }
    received = []
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        antenna_channels=channels,
        equipment_bindings=[
            EquipmentBinding(
                node_id="fitrace-edge-01-bike-01",
                equipment_id="BIKE_01",
                equipment_type="fan_bike",
                ble_target="AA:BB:CC:DD:EE:01",
                antenna_channel="uart-1",
            )
        ],
    )

    async def on_telemetry(telemetry):
        received.append(telemetry)

    manager = AntennaFtmsManager(
        edge_config=config,
        on_telemetry=on_telemetry,
        serial_factory=lambda channel: serials[channel.id],
        scan_duration_sec=0.1,
        command_timeout_sec=0.1,
    )

    await manager.start()
    for _ in range(20):
        if received:
            break
        await asyncio.sleep(0.05)
    await manager.stop()

    assert any(write == "SCAN:START;\r\n" for write in serials["uart-1"].writes)
    assert any(write == "CONNECT:AA:BB:CC:DD:EE:01;\r\n" for write in serials["uart-1"].writes)
    assert received[0].node_id == "fitrace-edge-01-bike-01"
    assert received[0].mac_address == "AA:BB:CC:DD:EE:01"
    assert received[0].ftms_type == "BIKE"
    assert received[0].rssi == -55
    assert received[0].instantaneous_speed_kph == 24.5
    assert received[0].power_watts == 120
    assert received[0].total_energy_kcal == 3
    assert received[0].calories == 3
    assert received[0].ftms_payload.kind == "speed"
    assert received[0].raw_payload["total_energy"] == 3


class SequencedScanSerial(FakeSerial):
    """FakeSerial whose SCAN:START responses differ per call."""

    def __init__(self, responses, scan_batches):
        super().__init__(responses)
        self.scan_batches = [
            [line.encode("ascii") for line in batch] for batch in scan_batches
        ]

    def write(self, data):
        command = data.decode("ascii")
        if command == "SCAN:START;\r\n":
            self.writes.append(command)
            if self.scan_batches:
                self.lines.extend(self.scan_batches.pop(0))
            return
        super().write(data)


@pytest.mark.asyncio
async def test_antenna_manager_reconnects_when_status_reports_missing_links():
    channels = make_channels()
    serials = {
        "uart-1": SequencedScanSerial(
            {
                "PING;\r\n": ["BOOT:NO_LIST;\r\n"],
                "SCAN:STOP;\r\n": ["SCAN:OK;\r\n"],
                "STATUS;\r\n": ["STATUS:IDLE,0/1;\r\n"],
                "CONNECT:AA:BB:CC:DD:EE:01;\r\n": ["CONNECT:OK;\r\n"],
                "REPORT:250;\r\n": [
                    "REPORT:OK;\r\n",
                    'FTMS:AA:BB:CC:DD:EE:01,TMILL,{"rssi":-60,"instantaneous_speed":12.0,"total_distance":5,"instantaneous_power":90,"total_energy":1};\r\n',
                ],
            },
            # startup scan misses the device entirely
            scan_batches=[["SCAN:OK;\r\n"]],
        ),
        "uart-2": SequencedScanSerial(
            {
                "PING;\r\n": ["BOOT:NO_LIST;\r\n"],
                "SCAN:STOP;\r\n": ["SCAN:OK;\r\n"],
            },
            scan_batches=[["SCAN:OK;\r\n"]],
        ),
    }
    received = []
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        antenna_channels=channels,
        equipment_bindings=[
            EquipmentBinding(
                node_id="fitrace-edge-01-tread-01",
                equipment_id="TREAD_01",
                equipment_type="treadmill",
                ble_target="AA:BB:CC:DD:EE:01",
                antenna_channel="uart-1",
            )
        ],
    )

    async def on_telemetry(telemetry):
        received.append(telemetry)

    manager = AntennaFtmsManager(
        edge_config=config,
        on_telemetry=on_telemetry,
        serial_factory=lambda channel: serials[channel.id],
        scan_duration_sec=0.05,
        command_timeout_sec=0.05,
        reconnect_interval_sec=0.2,
        data_timeout_sec=0.1,
    )

    await manager.start()
    for _ in range(40):
        if received:
            break
        await asyncio.sleep(0.05)
    await manager.stop()

    # STATUS said 0/1, so the configured target was reconnected on its channel
    assert any(write == "STATUS;\r\n" for write in serials["uart-1"].writes)
    assert any(
        write == "CONNECT:AA:BB:CC:DD:EE:01;\r\n" for write in serials["uart-1"].writes
    )
    # recovery must not disturb the channel with no configured targets
    assert not any(
        write.startswith("CONNECT:") for write in serials["uart-2"].writes
    )
    assert serials["uart-2"].writes.count("SCAN:START;\r\n") == 1
    assert received[0].node_id == "fitrace-edge-01-tread-01"
    assert received[0].mac_address == "AA:BB:CC:DD:EE:01"


def test_connect_assignments_caps_at_board_limit():
    channels = [AntennaChannelConfig(id="uart-2", port="/dev/ttyAMA4")]
    serial = FakeSerial({"CONNECT:AA:BB:CC:DD:EE:03,AA:BB:CC:DD:EE:04,AA:BB:CC:DD:EE:05;\r\n": ["CONNECT:OK;\r\n"]})
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        antenna_channels=channels,
        equipment_bindings=[
            EquipmentBinding(
                node_id=f"fitrace-edge-01-0{i}",
                equipment_id=f"TREAD_0{i}",
                equipment_type="treadmill",
                ble_target=f"AA:BB:CC:DD:EE:0{i}",
                antenna_channel="uart-2",
            )
            for i in (3, 4, 5)
        ],
    )

    async def on_telemetry(_telemetry):
        pass

    manager = AntennaFtmsManager(
        edge_config=config,
        on_telemetry=on_telemetry,
        serial_factory=lambda channel: serial,
        command_timeout_sec=0.05,
    )
    manager._serials = {"uart-2": serial}

    # five macs requested; board limit is 3 and configured targets must win
    manager._connect_assignments(
        {
            "uart-2": [
                "AA:BB:CC:DD:EE:01",
                "AA:BB:CC:DD:EE:02",
                "AA:BB:CC:DD:EE:03",
                "AA:BB:CC:DD:EE:04",
                "AA:BB:CC:DD:EE:05",
            ]
        }
    )

    connect_writes = [w for w in serial.writes if w.startswith("CONNECT:")]
    assert len(connect_writes) == 1
    sent_macs = connect_writes[0][len("CONNECT:"):].rstrip(";\r\n").split(",")
    assert sorted(sent_macs) == [
        "AA:BB:CC:DD:EE:03",
        "AA:BB:CC:DD:EE:04",
        "AA:BB:CC:DD:EE:05",
    ]


@pytest.mark.asyncio
async def test_antenna_manager_records_uart_monitor_events(tmp_path):
    channels = [AntennaChannelConfig(id="uart-1", port="/dev/ttyAMA0")]
    serials = {
        "uart-1": FakeSerial(
            {
                "PING;\r\n": ["BOOT:HAS_LIST,count=1;\r\n"],
                "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
            }
        ),
    }
    event_log = EdgeEventLog(tmp_path / "edge_monitor.jsonl")
    config = EdgeNodeConfig(node_id="fitrace-edge-01", antenna_channels=channels)

    async def on_telemetry(_telemetry):
        pass

    manager = AntennaFtmsManager(
        edge_config=config,
        on_telemetry=on_telemetry,
        serial_factory=lambda channel: serials[channel.id],
        command_timeout_sec=0.1,
        event_log=event_log,
    )

    await manager.start()
    await asyncio.sleep(0.1)
    await manager.stop()

    events = event_log.list_events(limit=10)
    assert any(event["direction"] == "tx" and event["message"] == "PING;" for event in events)
    assert any(event["direction"] == "rx" and event["message"].startswith("BOOT:") for event in events)


@pytest.mark.asyncio
async def test_antenna_manager_assigns_saved_targets_to_channel_bindings():
    channels = make_channels()
    serials = {
        "uart-1": FakeSerial(
            {
                "PING;\r\n": ["BOOT:HAS_LIST,count=1;\r\n"],
                "REPORT:250;\r\n": [
                    "REPORT:OK;\r\n",
                    'FTMS:AA:BB:CC:DD:EE:09,BIKE,{"instantaneous_speed":31.2,"total_distance":12,"instantaneous_power":155};\r\n',
                ],
            }
        ),
        "uart-2": FakeSerial(
            {
                "PING;\r\n": ["BOOT:HAS_LIST,count=1;\r\n"],
                "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
            }
        ),
    }
    received = []
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        antenna_channels=channels,
        equipment_bindings=[
            EquipmentBinding(
                node_id="fitrace-edge-01-bike-01",
                equipment_id="BIKE_01",
                equipment_type="fan_bike",
                ble_target="BIKE_01_TARGET",
                antenna_channel="uart-1",
            )
        ],
    )

    async def on_telemetry(telemetry):
        received.append(telemetry)

    manager = AntennaFtmsManager(
        edge_config=config,
        on_telemetry=on_telemetry,
        serial_factory=lambda channel: serials[channel.id],
        scan_duration_sec=0.1,
        command_timeout_sec=0.1,
    )

    await manager.start()
    for _ in range(20):
        if received:
            break
        await asyncio.sleep(0.05)
    await manager.stop()

    # both boards reported HAS_LIST: no startup scan, saved lists stay intact
    assert not any(write == "SCAN:START;\r\n" for write in serials["uart-1"].writes)
    assert not any(write.startswith("DISCONNECT:") for write in serials["uart-1"].writes)
    assert received[0].node_id == "fitrace-edge-01-bike-01"
    assert received[0].equipment_id == "BIKE_01"


@pytest.mark.asyncio
async def test_antenna_manager_scans_only_no_list_channels():
    channels = make_channels()
    serials = {
        "uart-1": FakeSerial(
            {
                "PING;\r\n": ["BOOT:HAS_LIST,count=2;\r\n"],
                "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
            }
        ),
        "uart-2": FakeSerial(
            {
                "PING;\r\n": ["BOOT:NO_LIST;\r\n"],
                "SCAN:START;\r\n": [
                    "SCAN:OK;\r\n",
                    # uart-2 also hears uart-1's configured (but idle) device
                    "DEVICE:AA:BB:CC:DD:EE:01,-50,Tread 1,TREADMILL;\r\n",
                    "DEVICE:AA:BB:CC:DD:EE:02,-60,Tread 2,TREADMILL;\r\n",
                ],
                "SCAN:STOP;\r\n": ["SCAN:OK;\r\n"],
                "DISCONNECT:ALL;\r\n": ["DISCONNECT:OK;\r\n"],
                "CONNECT:AA:BB:CC:DD:EE:02;\r\n": ["CONNECT:OK;\r\n"],
                "REPORT:250;\r\n": ["REPORT:OK;\r\n"],
            }
        ),
    }
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        antenna_channels=channels,
        equipment_bindings=[
            EquipmentBinding(
                node_id="fitrace-edge-01-01",
                equipment_id="TREAD_01",
                equipment_type="treadmill",
                ble_target="AA:BB:CC:DD:EE:01",
                antenna_channel="uart-1",
            ),
            EquipmentBinding(
                node_id="fitrace-edge-01-02",
                equipment_id="TREAD_02",
                equipment_type="treadmill",
                ble_target="AA:BB:CC:DD:EE:02",
                antenna_channel="uart-2",
            ),
        ],
    )

    async def on_telemetry(_telemetry):
        pass

    manager = AntennaFtmsManager(
        edge_config=config,
        on_telemetry=on_telemetry,
        serial_factory=lambda channel: serials[channel.id],
        scan_duration_sec=0.1,
        command_timeout_sec=0.1,
    )

    await manager.start()
    await asyncio.sleep(0.3)
    await manager.stop()

    # HAS_LIST board is left alone: no scan, no disconnect, no connect
    assert not any(write.startswith("SCAN:") for write in serials["uart-1"].writes)
    assert not any(write.startswith("DISCONNECT:") for write in serials["uart-1"].writes)
    assert not any(write.startswith("CONNECT:") for write in serials["uart-1"].writes)
    # but it still gets the report interval applied
    assert any(write == "REPORT:250;\r\n" for write in serials["uart-1"].writes)
    # NO_LIST board scans and connects only its own configured device
    assert any(write == "SCAN:START;\r\n" for write in serials["uart-2"].writes)
    assert any(write == "CONNECT:AA:BB:CC:DD:EE:02;\r\n" for write in serials["uart-2"].writes)
    assert not any("AA:BB:CC:DD:EE:01" in write for write in serials["uart-2"].writes if write.startswith("CONNECT:"))
