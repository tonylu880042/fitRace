import pytest
from edge_node.usecases.ble_ftms_parser import (
    parse_indoor_bike,
    parse_treadmill,
    parse_rower,
    parse_ftms
)

def test_parse_indoor_bike_valid():
    # Flags:
    # bit 2 (1 << 2 = 0x04) - Instantaneous Cadence Present
    # bit 4 (1 << 4 = 0x10) - Total Distance Present
    # bit 6 (1 << 6 = 0x40) - Instantaneous Power Present
    # bit 9 (1 << 9 = 0x0200) - Heart Rate Present
    # bit 11 (1 << 11 = 0x0800) - Elapsed Time Present
    # Flags = 0x0A54 -> [0x54, 0x0A]
    #
    # Fields:
    # - Flags: [0x54, 0x0A]
    # - Speed: 3600 (36.00 km/h) -> [0x10, 0x0E]
    # - Cadence: 160 (80.0 rpm, unit is 0.5) -> [0xA0, 0x00]
    # - Distance: 1234 (1234 m, 3 bytes) -> [0xD2, 0x04, 0x00]
    # - Power: 250 (250 W) -> [0xFA, 0x00]
    # - Heart Rate: 145 bpm -> [0x91]
    # - Elapsed Time: 120 sec -> [0x78, 0x00]
    raw_data = bytes([
        0x54, 0x0A,
        0x10, 0x0E,
        0xA0, 0x00,
        0xD2, 0x04, 0x00,
        0xFA, 0x00,
        0x91,
        0x78, 0x00
    ])
    result = parse_indoor_bike(raw_data)
    assert result["speed_kph"] == 36.0
    assert result["cadence_rpm"] == 80
    assert result["distance_m"] == 1234.0
    assert result["power_watts"] == 250
    assert result["heart_rate_bpm"] == 145
    assert result["elapsed_time_sec"] == 120

def test_parse_treadmill_valid():
    # Flags:
    # bit 2 (1 << 2 = 0x04) - Total Distance Present
    # bit 8 (1 << 8 = 0x0100) - Heart Rate Present
    # bit 10 (1 << 10 = 0x0400) - Elapsed Time Present
    # Flags = 0x0504 -> [0x04, 0x05]
    #
    # Fields:
    # - Flags: [0x04, 0x05]
    # - Speed: 1250 (12.50 km/h) -> [0xE2, 0x04]
    # - Distance: 500 (500 m, 3 bytes) -> [0xF4, 0x01, 0x00]
    # - Heart Rate: 130 bpm -> [0x82]
    # - Elapsed Time: 300 sec -> [0x2C, 0x01]
    raw_data = bytes([
        0x04, 0x05,
        0xE2, 0x04,
        0xF4, 0x01, 0x00,
        0x82,
        0x2C, 0x01
    ])
    result = parse_treadmill(raw_data)
    assert result["speed_kph"] == 12.5
    assert result["distance_m"] == 500.0
    assert result["heart_rate_bpm"] == 130
    assert result["elapsed_time_sec"] == 300

def test_parse_rower_valid():
    # Flags:
    # bit 2 (1 << 2 = 0x04) - Total Distance Present
    # bit 5 (1 << 5 = 0x20) - Instantaneous Power Present
    # bit 9 (1 << 9 = 0x0200) - Heart Rate Present
    # bit 11 (1 << 11 = 0x0800) - Elapsed Time Present
    # Flags = 0x0A24 -> [0x24, 0x0A]
    #
    # Fields:
    # - Flags: [0x24, 0x0A]
    # - Stroke Rate: 56 (28.0 strokes/min, unit 0.5) -> [0x38] (mandatory)
    # - Stroke Count: 150 -> [0x96, 0x00] (mandatory)
    # - Distance: 1000 (1000 m, 3 bytes) -> [0xE8, 0x03, 0x00]
    # - Power: 180 (180 W) -> [0xB4, 0x00]
    # - Heart Rate: 150 bpm -> [0x96]
    # - Elapsed Time: 240 sec -> [0xF0, 0x00]
    raw_data = bytes([
        0x24, 0x0A,
        0x38,
        0x96, 0x00,
        0xE8, 0x03, 0x00,
        0xB4, 0x00,
        0x96,
        0xF0, 0x00
    ])
    result = parse_rower(raw_data)
    assert result["cadence_rpm"] == 28
    assert result["stroke_count"] == 150
    assert result["distance_m"] == 1000.0
    assert result["power_watts"] == 180
    assert result["heart_rate_bpm"] == 150
    assert result["elapsed_time_sec"] == 240

def test_parse_ftms_multiplexer():
    # Test multiplexing using standard UUID strings
    # Indoor Bike Data UUID: 00002ad2-0000-1000-8000-00805f9b34fb
    # Treadmill Data UUID: 00002acd-0000-1000-8000-00805f9b34fb
    # Rower Data UUID: 00002ad1-0000-1000-8000-00805f9b34fb
    bike_raw = bytes([0x54, 0x0A, 0x10, 0x0E, 0xA0, 0x00, 0xD2, 0x04, 0x00, 0xFA, 0x00, 0x91, 0x78, 0x00])
    res_bike = parse_ftms("00002ad2-0000-1000-8000-00805f9b34fb", bike_raw)
    assert res_bike["speed_kph"] == 36.0

    tread_raw = bytes([0x04, 0x05, 0xE2, 0x04, 0xF4, 0x01, 0x00, 0x82, 0x2C, 0x01])
    res_tread = parse_ftms("00002acd-0000-1000-8000-00805f9b34fb", tread_raw)
    assert res_tread["speed_kph"] == 12.5

    rower_raw = bytes([0x24, 0x0A, 0x38, 0x96, 0x00, 0xE8, 0x03, 0x00, 0xB4, 0x00, 0x96, 0xF0, 0x00])
    res_rower = parse_ftms("00002ad1-0000-1000-8000-00805f9b34fb", rower_raw)
    assert res_rower["cadence_rpm"] == 28

def test_parser_invalid_length():
    with pytest.raises(ValueError):
        parse_indoor_bike(bytes([0x00]))
    with pytest.raises(ValueError):
        parse_treadmill(bytes([]))
    with pytest.raises(ValueError):
        parse_rower(bytes([0x01]))
