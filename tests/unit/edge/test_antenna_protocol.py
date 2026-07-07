from edge_node.infrastructure.antenna import protocol
import pytest


def test_build_antenna_commands_match_uart_protocol():
    assert protocol.build_ping() == "PING;\r\n"
    assert protocol.build_scan_start() == "SCAN:START;\r\n"
    assert protocol.build_scan_stop() == "SCAN:STOP;\r\n"
    assert protocol.build_connect(["AA:BB:CC:DD:EE:01"]) == "CONNECT:AA:BB:CC:DD:EE:01;\r\n"
    assert protocol.build_disconnect_all() == "DISCONNECT:ALL;\r\n"
    assert protocol.build_report_interval(1000) == "REPORT:1000;\r\n"
    assert protocol.build_status() == "STATUS;\r\n"
    assert protocol.build_version() == "VERSION;\r\n"


def test_build_connect_allows_more_than_three_devices():
    assert (
        protocol.build_connect(
            [
                "AA:BB:CC:DD:EE:01",
                "AA:BB:CC:DD:EE:02",
                "AA:BB:CC:DD:EE:03",
                "AA:BB:CC:DD:EE:04",
            ]
        )
        == "CONNECT:AA:BB:CC:DD:EE:01,AA:BB:CC:DD:EE:02,AA:BB:CC:DD:EE:03,AA:BB:CC:DD:EE:04;\r\n"
    )


def test_build_connect_rejects_empty_device_list():
    with pytest.raises(ValueError, match="at least one"):
        protocol.build_connect([])


def test_parse_speed_based_telemetry_line_maps_firmware_payload():
    parsed = protocol.parse_line(
        'FTMS:ED:6D:3F:20:DD:17,BIKE,{"rssi":-62,"instantaneous_speed":24.46,'
        '"total_distance":41040,"instantaneous_power":127,"total_energy":798};'
    )

    assert parsed["type"] == "telemetry"
    assert parsed["address"] == "ED:6D:3F:20:DD:17"
    assert parsed["device_type"] == "BIKE"
    assert parsed["payload"]["instantaneous_speed"] == 24.46
    assert parsed["payload"]["total_distance"] == 41040
    assert parsed["instantaneous_speed_kph"] == 24.46
    assert parsed["distance_m"] == 41040
    assert parsed["power_watts"] == 127
    assert parsed["rssi"] == -62
    assert parsed["equipment_type"] == "fan_bike"
    assert parsed["ftms_type"] == "BIKE"
    assert parsed["total_energy_kcal"] == 798
    assert parsed["ftms_payload"] == {
        "kind": "speed",
        "rssi": -62,
        "instantaneous_speed": 24.46,
        "total_distance": 41040,
        "instantaneous_power": 127,
        "total_energy": 798,
    }
    assert parsed["raw_payload"]["total_energy"] == 798


def test_parse_rower_telemetry_line_uses_stroke_rate_not_speed():
    parsed = protocol.parse_line(
        'FTMS:AA:BB:CC:DD:EE:03,ROWER,{"rssi":-60,"stroke_rate":24.5,'
        '"total_distance":850,"instantaneous_pace":125,"instantaneous_power":98,'
        '"total_energy":22};\r\n'
    )

    assert parsed["type"] == "telemetry"
    assert parsed["address"] == "AA:BB:CC:DD:EE:03"
    assert parsed["device_type"] == "ROWER"
    assert parsed["payload"]["stroke_rate"] == 24.5
    assert parsed["instantaneous_speed_kph"] == 0.0
    assert parsed["cadence_rpm"] == 24.5
    assert parsed["pace_sec_per_500m"] == 125
    assert parsed["distance_m"] == 850
    assert parsed["power_watts"] == 98
    assert parsed["equipment_type"] == "rowing_machine"
    assert parsed["total_energy_kcal"] == 22
    assert parsed["ftms_payload"] == {
        "kind": "rower",
        "rssi": -60,
        "stroke_rate": 24.5,
        "total_distance": 850,
        "instantaneous_pace": 125,
        "instantaneous_power": 98,
        "total_energy": 22,
    }


def test_parse_unknown_telemetry_line_keeps_rssi_only():
    parsed = protocol.parse_line("FTMS:AA:BB:CC:DD:EE:99,UNKNOWN,{\"rssi\":-70};")

    assert parsed["type"] == "telemetry"
    assert parsed["device_type"] == "UNKNOWN"
    assert parsed["payload"] == {"rssi": -70}
    assert parsed["rssi"] == -70
    assert parsed["instantaneous_speed_kph"] == 0.0
    assert parsed["power_watts"] == 0
    assert parsed["equipment_type"] == "unknown"
    assert parsed["ftms_payload"] == {"kind": "unknown", "rssi": -70}


def test_parse_malformed_telemetry_line_returns_invalid():
    parsed = protocol.parse_line("FTMS:AA:BB:CC:DD:EE:01,BIKE,{\"rssi\":-62")

    assert parsed["type"] == "invalid"
    assert parsed["message_type"] == "telemetry"


def test_parse_scan_device_and_status_lines():
    device = protocol.parse_line("DEVICE:AA:BB:CC:DD:EE:01,-55,Bike One,BIKE;")
    status = protocol.parse_line("STATUS:REPORT,2/3;")

    assert device == {
        "type": "device",
        "address": "AA:BB:CC:DD:EE:01",
        "rssi": -55,
        "name": "Bike One",
        "device_type": "BIKE",
        "raw": "DEVICE:AA:BB:CC:DD:EE:01,-55,Bike One,BIKE;",
    }
    assert status["type"] == "status"
    assert status["state"] == "REPORT"
    assert status["connected"] == 2
    assert status["target"] == 3


def test_parse_legacy_key_value_status_line():
    status = protocol.parse_line("STATUS:REPORT,conn=2,target=3;")

    assert status["type"] == "status"
    assert status["state"] == "REPORT"
    assert status["connected"] == 2
    assert status["target"] == 3
