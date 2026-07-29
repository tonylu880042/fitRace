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


def test_antenna_manager_identifies_the_uart_channel_that_emitted_telemetry():
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        antenna_channels=[
            AntennaChannelConfig(id="uart-1", port="/dev/ttyAMA0"),
            AntennaChannelConfig(id="uart-2", port="/dev/ttyAMA4"),
        ],
    )

    async def on_telemetry(_telemetry):
        pass

    manager = AntennaFtmsManager(edge_config=config, on_telemetry=on_telemetry)

    telemetry = manager._to_telemetry(
        "uart-2",
        {
            "address": "AA:BB:CC:DD:EE:01",
            "equipment_type": "fan_bike",
        },
    )

    assert telemetry.antenna_channel == "uart-2"


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
async def test_antenna_manager_emits_telemetry_from_has_list_channel_with_no_startup_scan():
    # spec: the antenna board's NVS target list is the source of truth. A
    # HAS_LIST board auto-reconnects its saved targets on its own, so
    # startup must only PING and re-apply the report interval -- never
    # SCAN/CONNECT. Telemetry must still resolve to the configured node_id
    # via the exact-MAC match branch in _binding_for_mac, even though
    # bind_assignments_to_streams (which used to seed _bindings_by_mac at
    # startup) is no longer called at all.
    channels = [AntennaChannelConfig(id="uart-1", port="/dev/ttyAMA0")]
    serials = {
        "uart-1": FakeSerial(
            {
                "PING;\r\n": ["BOOT:HAS_LIST,count=1;\r\n"],
                "REPORT:250;\r\n": [
                    "REPORT:OK;\r\n",
                    'FTMS:AA:BB:CC:DD:EE:01,BIKE,{"rssi":-55,"instantaneous_speed":24.5,"total_distance":10,"instantaneous_power":120,"total_energy":3};\r\n',
                ],
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
        command_timeout_sec=0.1,
    )

    await manager.start()
    for _ in range(20):
        if received:
            break
        await asyncio.sleep(0.05)
    await manager.stop()

    # HAS_LIST: PING, then only the report interval -- never SCAN/CONNECT.
    assert serials["uart-1"].writes == ["PING;\r\n", "REPORT:250;\r\n"]
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


@pytest.mark.asyncio
async def test_antenna_manager_stays_idle_at_startup_with_no_bindings():
    channels = make_channels()
    serials = {
        "uart-1": FakeSerial({"PING;\r\n": ["BOOT:NO_LIST;\r\n"]}),
        "uart-2": FakeSerial({"PING;\r\n": ["BOOT:NO_LIST;\r\n"]}),
    }
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        antenna_channels=channels,
        equipment_bindings=[],
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

    # With no configured bindings there is nothing a scan could ever match:
    # only the PING handshake is allowed, no SCAN/CONNECT/REPORT commands.
    assert serials["uart-1"].writes == ["PING;\r\n"]
    assert serials["uart-2"].writes == ["PING;\r\n"]


@pytest.mark.asyncio
async def test_antenna_manager_reconnects_when_status_reports_missing_links():
    # Startup never scans/connects a NO_LIST channel (see
    # test_antenna_manager_startup_never_scans_or_connects_no_list_channel),
    # so the configured target here is only ever reconnected by the
    # background watchdog (_reconnect_missing_targets), once STATUS reports
    # it missing. This test exercises that watchdog path in isolation.
    channels = make_channels()
    serials = {
        "uart-1": FakeSerial(
            {
                "PING;\r\n": ["BOOT:NO_LIST;\r\n"],
                "STATUS;\r\n": ["STATUS:IDLE,0/1;\r\n"],
                "CONNECT:AA:BB:CC:DD:EE:01;\r\n": ["CONNECT:OK;\r\n"],
                "REPORT:250;\r\n": [
                    "REPORT:OK;\r\n",
                    'FTMS:AA:BB:CC:DD:EE:01,TMILL,{"rssi":-60,"instantaneous_speed":12.0,"total_distance":5,"instantaneous_power":90,"total_energy":1};\r\n',
                ],
            }
        ),
        "uart-2": FakeSerial(
            {
                "PING;\r\n": ["BOOT:NO_LIST;\r\n"],
            }
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

    # startup never CONNECTed (NO_LIST); STATUS said 0/1, so the watchdog
    # reconnected the configured target on its channel
    assert any(write == "STATUS;\r\n" for write in serials["uart-1"].writes)
    assert any(
        write == "CONNECT:AA:BB:CC:DD:EE:01;\r\n" for write in serials["uart-1"].writes
    )
    # the channel owning no configured targets is never touched, at startup
    # or by the watchdog
    assert serials["uart-2"].writes == ["PING;\r\n"]
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
async def test_antenna_manager_startup_never_scans_or_connects_no_list_channel():
    # spec: NVS on the board is the source of truth. NO_LIST only ever means
    # an operator just ran DISCONNECT:ALL or the board is fresh/unconfigured
    # -- either way a human is already present, so startup must wait for
    # them to run a pairing scan themselves rather than scanning/connecting
    # on its own (the destructive DISCONNECT:ALL + CONNECT pair this venue
    # cannot tolerate, since equipment auto-powers-off once disconnected).
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

    # HAS_LIST board is left alone except its report interval re-applied.
    assert serials["uart-1"].writes == ["PING;\r\n", "REPORT:250;\r\n"]
    # NO_LIST board -- even though it owns a configured binding -- gets no
    # SCAN, no CONNECT, and no DISCONNECT at all; it waits for an operator.
    assert serials["uart-2"].writes == ["PING;\r\n"]


@pytest.mark.asyncio
async def test_antenna_manager_skips_no_list_channel_that_owns_no_bindings():
    # Live-evidence regression: both configured bindings live on uart-1.
    # uart-1 reports HAS_LIST (board auto-reconnects on its own), uart-2
    # reports NO_LIST and owns nothing -- startup never scans/connects a
    # NO_LIST channel regardless of binding ownership, so it must be left
    # completely alone either way.
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
                antenna_channel="uart-1",
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

    # uart-2 owns nothing: after the PING handshake it must be left
    # completely alone -- no SCAN, and no stray REPORT either.
    assert serials["uart-2"].writes == ["PING;\r\n"]
    # uart-1 (HAS_LIST, owns both bindings) is undisturbed but still gets
    # its report interval re-applied after reboot.
    assert not any(write.startswith("SCAN:") for write in serials["uart-1"].writes)
    assert any(write == "REPORT:250;\r\n" for write in serials["uart-1"].writes)


@pytest.mark.asyncio
async def test_antenna_manager_never_clears_config_when_board_gives_no_answer():
    # Critical safety test. An unplugged or wedged antenna board (no BOOT
    # reply after 3 PING retries) must NEVER be treated like an explicit
    # NO_LIST reply -- only an explicit NO_LIST may ever clear a channel's
    # config.json bindings. FakeSerial has no response mapped for "PING;",
    # so every retry times out and _ping_channels must classify this as
    # "no answer", not "no list".
    channels = [AntennaChannelConfig(id="uart-1", port="/dev/ttyAMA0")]
    serials = {"uart-1": FakeSerial({})}
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
            )
        ],
    )
    saved = []

    async def on_telemetry(_telemetry):
        pass

    manager = AntennaFtmsManager(
        edge_config=config,
        on_telemetry=on_telemetry,
        serial_factory=lambda channel: serials[channel.id],
        command_timeout_sec=0.02,
        config_loader=lambda: config.model_copy(deep=True),
        config_saver=saved.append,
    )

    await manager.start()
    await asyncio.sleep(0.3)
    await manager.stop()

    # PING is retried up to 3 times per spec and nothing else is ever
    # written: no REPORT (channel never joined _saved_list_channels) and,
    # critically, config clearing never even attempted a write.
    assert serials["uart-1"].writes == ["PING;\r\n"] * 3
    assert saved == []
    assert len(manager._edge_config.equipment_bindings) == 1


@pytest.mark.asyncio
async def test_antenna_manager_clears_bindings_only_for_explicit_no_list_channel():
    channels = make_channels()
    serials = {
        "uart-1": FakeSerial({"PING;\r\n": ["BOOT:HAS_LIST,count=1;\r\n"]}),
        "uart-2": FakeSerial({"PING;\r\n": ["BOOT:NO_LIST;\r\n"]}),
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
    saved = []

    async def on_telemetry(_telemetry):
        pass

    manager = AntennaFtmsManager(
        edge_config=config,
        on_telemetry=on_telemetry,
        serial_factory=lambda channel: serials[channel.id],
        command_timeout_sec=0.05,
        config_loader=lambda: config.model_copy(deep=True),
        config_saver=saved.append,
    )

    await manager.start()
    await asyncio.sleep(0.2)
    await manager.stop()

    # exactly one save, containing only the uart-1 (HAS_LIST) binding
    assert len(saved) == 1
    assert [b.antenna_channel for b in saved[0].equipment_bindings] == ["uart-1"]
    assert saved[0].equipment_bindings[0].node_id == "fitrace-edge-01-01"
    # in-memory config matches what was persisted
    assert [b.antenna_channel for b in manager._edge_config.equipment_bindings] == [
        "uart-1"
    ]


@pytest.mark.asyncio
async def test_antenna_manager_skips_config_clearing_while_pairing_is_active(
    monkeypatch, tmp_path
):
    # A pairing session may be writing config.json concurrently -- clearing
    # here would race it, so an active pairing flag must block clearing
    # entirely, even for an explicit NO_LIST reply.
    flag_path = tmp_path / "pairing.flag"
    flag_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FITRACE_PAIRING_FLAG_PATH", str(flag_path))

    channels = [AntennaChannelConfig(id="uart-1", port="/dev/ttyAMA0")]
    serials = {"uart-1": FakeSerial({"PING;\r\n": ["BOOT:NO_LIST;\r\n"]})}
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
            )
        ],
    )
    saved = []

    async def on_telemetry(_telemetry):
        pass

    manager = AntennaFtmsManager(
        edge_config=config,
        on_telemetry=on_telemetry,
        serial_factory=lambda channel: serials[channel.id],
        command_timeout_sec=0.05,
        config_loader=lambda: config.model_copy(deep=True),
        config_saver=saved.append,
    )

    await manager.start()
    await asyncio.sleep(0.2)
    await manager.stop()

    assert saved == []
    assert len(manager._edge_config.equipment_bindings) == 1


@pytest.mark.asyncio
async def test_antenna_manager_no_crash_when_config_saver_is_none():
    channels = [AntennaChannelConfig(id="uart-1", port="/dev/ttyAMA0")]
    serials = {"uart-1": FakeSerial({"PING;\r\n": ["BOOT:NO_LIST;\r\n"]})}
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
            )
        ],
    )

    async def on_telemetry(_telemetry):
        pass

    manager = AntennaFtmsManager(
        edge_config=config,
        on_telemetry=on_telemetry,
        serial_factory=lambda channel: serials[channel.id],
        command_timeout_sec=0.05,
        # no config_loader/config_saver configured at all
    )

    await manager.start()
    await asyncio.sleep(0.2)
    task = manager._task
    await manager.stop()

    assert task.exception() is None
    assert len(manager._edge_config.equipment_bindings) == 1
