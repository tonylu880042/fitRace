import pytest
from hub_server.domain.models import RaceState, RaceConfig
from hub_server.usecases.race_manager import RaceManager


def test_station_assignment():
    manager = RaceManager()

    # Initially no stations are assigned
    status = manager.get_stations_status()
    assert len(status["stations"]) == 0
    assert len(status["unassigned_nodes"]) == 0

    # Let's say a telemetry node-01 is detected (active)
    manager.update_active_node("node-01", "fan_bike")
    status = manager.get_stations_status()
    assert "node-01" in status["unassigned_nodes"]

    # Assign station 1 to node-01
    manager.assign_station(1, "node-01")
    status = manager.get_stations_status()
    assert status["stations"][1]["node_id"] == "node-01"
    assert status["stations"][1]["equipment_type"] == "fan_bike"
    assert "node-01" not in status["unassigned_nodes"]

    # Re-assign station 1 to node-02 (after node-02 becomes active)
    manager.update_active_node("node-02", "treadmill")
    manager.assign_station(1, "node-02")
    status = manager.get_stations_status()
    assert status["stations"][1]["node_id"] == "node-02"
    assert "node-01" in status["unassigned_nodes"]

    # Unassign station 1
    manager.assign_station(1, None)
    status = manager.get_stations_status()
    assert 1 not in status["stations"]
    assert "node-02" in status["unassigned_nodes"]


def test_athlete_registration_and_overwrite():
    manager = RaceManager()
    manager.update_active_node("node-01", "fan_bike")
    manager.assign_station(1, "node-01")

    # Register Athlete A to Station 1
    manager.register_athlete(1, "Athlete A")
    status = manager.get_stations_status()
    assert status["stations"][1]["athlete_name"] == "Athlete A"

    # Register Athlete B to Station 1 (should overwrite)
    manager.register_athlete(1, "Athlete B")
    status = manager.get_stations_status()
    assert status["stations"][1]["athlete_name"] == "Athlete B"

    # Cannot register athlete when race is running
    config = RaceConfig(race_type="distance", target_value=500.0)
    manager.configure(config)
    manager.start_race()

    with pytest.raises(ValueError):
        manager.register_athlete(1, "Athlete C")


def test_telemetry_mapping_to_station_athlete():
    manager = RaceManager()
    # Configure and setup stations
    manager.update_active_node("node-01", "fan_bike")
    manager.assign_station(1, "node-01")
    manager.register_athlete(1, "Tony")

    # Configure and start race
    config = RaceConfig(race_type="distance", target_value=1000.0)
    manager.configure(config)
    manager.start_race()

    # Incoming telemetry from node-01
    telemetry_payload = {
        "node_id": "node-01",
        "distance_m": 150.0,
        "elapsed_time_ms": 10000,
        "instantaneous_speed_kph": 15.0,
    }

    progress = manager.update_telemetry(telemetry_payload)
    assert "node-01" in progress
    assert progress["node-01"]["athlete_name"] == "Tony"
    assert progress["node-01"]["station_number"] == 1
    assert progress["node-01"]["progress_percent"] == 15.0


def test_reset_race_clears_athletes_but_keeps_station_mapping():
    manager = RaceManager()
    manager.update_active_node("node-01", "fan_bike")
    manager.assign_station(1, "node-01")
    manager.register_athlete(1, "Tony")

    config = RaceConfig(race_type="distance", target_value=1000.0)
    manager.configure(config)
    manager.start_race()
    manager.stop_race()

    manager.reset_race()
    assert manager.get_state() == RaceState.IDLE

    # Check that station 1 is still mapped to node-01, but the athlete is cleared
    status = manager.get_stations_status()
    assert status["stations"][1]["node_id"] == "node-01"
    assert status["stations"][1]["athlete_name"] is None


def test_unassign_station_clears_athlete():
    manager = RaceManager()
    manager.update_active_node("node-01", "fan_bike")
    manager.assign_station(1, "node-01")
    manager.register_athlete(1, "Tony")

    status = manager.get_stations_status()
    assert status["stations"][1]["athlete_name"] == "Tony"

    # Unassign station (assign None)
    manager.assign_station(1, None)
    status = manager.get_stations_status()
    assert 1 not in status["stations"]
    # Verify that athlete registrations dictionary is cleared for this station
    assert len(status["stations"]) == 0


def test_update_active_node_remembers_ble_equipment_id():
    manager = RaceManager()

    # equipment_id (the BLE name the operator reads off the machine itself)
    # must be captured so it survives the edge node dropping off later.
    manager.update_active_node("node-01", "fan_bike", equipment_id="Vmax53932")

    status = manager.get_stations_status()
    assert status["remembered_equipment_ids"]["node-01"] == "Vmax53932"


def test_update_active_node_without_equipment_id_does_not_clear_remembered_name():
    manager = RaceManager()
    manager.update_active_node("node-01", "fan_bike", equipment_id="Vmax53932")

    # A later telemetry tick that (for whatever reason) carries no
    # equipment_id must not blank out the name we already learned.
    manager.update_active_node("node-01", "fan_bike")

    status = manager.get_stations_status()
    assert status["remembered_equipment_ids"]["node-01"] == "Vmax53932"


def test_ingest_telemetry_captures_ble_equipment_id_from_payload():
    manager = RaceManager()

    manager.ingest_telemetry(
        {
            "node_id": "node-01",
            "equipment_type": "rowing_machine",
            "equipment_id": "Vmax53932",
        }
    )

    status = manager.get_stations_status()
    assert status["remembered_equipment_ids"]["node-01"] == "Vmax53932"


def test_update_telemetry_captures_ble_equipment_id_during_running_race():
    manager = RaceManager()
    manager.update_active_node("node-01", "fan_bike")
    manager.assign_station(1, "node-01")
    config = RaceConfig(race_type="distance", target_value=1000.0)
    manager.configure(config)
    manager.start_race()

    manager.update_telemetry(
        {
            "node_id": "node-01",
            "equipment_type": "fan_bike",
            "equipment_id": "Vmax53932",
            "distance_m": 10.0,
            "elapsed_time_ms": 1000,
        }
    )

    status = manager.get_stations_status()
    assert status["remembered_equipment_ids"]["node-01"] == "Vmax53932"


def test_remembered_ble_equipment_id_survives_race_reset():
    manager = RaceManager()
    manager.update_active_node("node-01", "fan_bike", equipment_id="Vmax53932")
    manager.assign_station(1, "node-01")

    config = RaceConfig(race_type="distance", target_value=1000.0)
    manager.configure(config)
    manager.start_race()
    manager.stop_race()
    manager.reset_race()

    # The physical machine hasn't changed just because the race was reset,
    # so the operator-facing BLE name must not be forgotten.
    status = manager.get_stations_status()
    assert status["remembered_equipment_ids"]["node-01"] == "Vmax53932"
    assert status["stations"][1]["node_id"] == "node-01"


def test_remembered_ble_equipment_id_survives_reconfigure_from_stopped():
    manager = RaceManager()
    manager.update_active_node("node-01", "fan_bike", equipment_id="Vmax53932")
    manager.assign_station(1, "node-01")

    config = RaceConfig(race_type="distance", target_value=1000.0)
    manager.configure(config)
    manager.start_race()
    manager.stop_race()

    # Reconfigure straight from STOPPED (the other place _active_nodes gets
    # cleared) without going through reset_race().
    manager.configure(config)

    status = manager.get_stations_status()
    assert status["remembered_equipment_ids"]["node-01"] == "Vmax53932"
