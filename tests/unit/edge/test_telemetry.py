import pytest
import time
from edge_node.domain.models import RowerFtmsPayload, TelemetryData
from edge_node.usecases.mock_generator import generate_mock_telemetry


def test_telemetry_data_model_validation():
    # Test valid model instantiation
    data = TelemetryData(
        node_id="treadmill-01",
        equipment_id="TREAD_01",
        equipment_type="treadmill",
        instantaneous_speed_kph=10.5,
        cadence_rpm=80,
        power_watts=200,
        heart_rate_bpm=140,
        distance_m=100.0,
        elapsed_time_ms=30000,
        timestamp_epoch_ms=int(time.time() * 1000),
    )
    assert data.node_id == "treadmill-01"
    assert data.instantaneous_speed_kph == 10.5
    assert data.cadence_rpm == 80
    assert data.power_watts == 200


def test_telemetry_data_dict_serialization():
    data = TelemetryData(
        node_id="fanbike-01",
        equipment_id="BIKE_01",
        equipment_type="fan_bike",
        instantaneous_speed_kph=12.0,
        cadence_rpm=60,
        power_watts=150,
        heart_rate_bpm=130,
        distance_m=200.0,
        elapsed_time_ms=60000,
        timestamp_epoch_ms=123456789,
    )
    data_dict = data.model_dump()
    assert data_dict["node_id"] == "fanbike-01"
    assert data_dict["equipment_type"] == "fan_bike"
    assert data_dict["instantaneous_speed_kph"] == 12.0
    assert data_dict["distance_m"] == 200.0


def test_telemetry_data_serializes_type_specific_ftms_payload():
    data = TelemetryData(
        node_id="rower-01",
        equipment_id="ROW_01",
        equipment_type="rowing_machine",
        mac_address="AA:BB:CC:DD:EE:03",
        ftms_type="ROWER",
        rssi=-60,
        cadence_rpm=24,
        pace_sec_per_500m=125,
        power_watts=98,
        distance_m=850,
        total_energy_kcal=22,
        calories=22,
        elapsed_time_ms=0,
        timestamp_epoch_ms=123456789,
        ftms_payload=RowerFtmsPayload(
            rssi=-60,
            stroke_rate=24.5,
            total_distance=850,
            instantaneous_pace=125,
            instantaneous_power=98,
            total_energy=22,
        ),
        raw_payload={
            "rssi": -60,
            "stroke_rate": 24.5,
            "total_distance": 850,
            "instantaneous_pace": 125,
            "instantaneous_power": 98,
            "total_energy": 22,
        },
    )

    data_dict = data.model_dump()
    assert data_dict["ftms_type"] == "ROWER"
    assert data_dict["pace_sec_per_500m"] == 125
    assert data_dict["ftms_payload"]["kind"] == "rower"
    assert data_dict["raw_payload"]["stroke_rate"] == 24.5


@pytest.mark.asyncio
async def test_mock_generator_yields_valid_telemetry():
    # Create generator
    generator = generate_mock_telemetry(
        node_id="skierg-01",
        equipment_id="SKI_01",
        equipment_type="ski_erg",
        interval_sec=0.1,
    )

    # Take first item
    first_item = await anext(generator)
    assert isinstance(first_item, TelemetryData)
    assert first_item.node_id == "skierg-01"
    assert first_item.equipment_type == "ski_erg"
    assert first_item.distance_m > 0
    assert first_item.elapsed_time_ms == 100

    # Take second item, verify elapsed time and distance increase
    second_item = await anext(generator)
    assert second_item.elapsed_time_ms == 200
    assert second_item.distance_m > first_item.distance_m
    assert second_item.timestamp_epoch_ms >= first_item.timestamp_epoch_ms
