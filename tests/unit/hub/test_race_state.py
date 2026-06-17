import pytest
from hub_server.domain.models import RaceState, RaceConfig
from hub_server.usecases.race_manager import RaceManager


def test_race_config_model():
    config = RaceConfig(
        race_type="distance", target_value=1000.0, duration_sec=0  # 1000 meters
    )
    assert config.race_type == "distance"
    assert config.target_value == 1000.0
    assert config.duration_sec == 0


def test_race_manager_initial_state():
    manager = RaceManager()
    assert manager.get_state() == RaceState.IDLE
    assert manager.get_config() is None
    assert len(manager.get_registered_nodes()) == 0


def test_race_manager_configure():
    manager = RaceManager()
    config = RaceConfig(race_type="time", target_value=0, duration_sec=300)
    manager.configure(config)

    assert manager.get_state() == RaceState.READY
    assert manager.get_config().duration_sec == 300


def test_race_manager_invalid_transitions():
    manager = RaceManager()

    # Cannot start from IDLE
    with pytest.raises(ValueError):
        manager.start_race()

    # Configure first
    config = RaceConfig(race_type="distance", target_value=500.0, duration_sec=0)
    manager.configure(config)

    # Cannot stop from READY
    with pytest.raises(ValueError):
        manager.stop_race()


def test_race_manager_valid_flow():
    manager = RaceManager()
    config = RaceConfig(race_type="distance", target_value=500.0, duration_sec=0)

    manager.configure(config)
    assert manager.get_state() == RaceState.READY

    manager.register_node("treadmill-01", "Athlete A")
    assert manager.get_registered_nodes()["treadmill-01"] == "Athlete A"

    manager.start_race()
    assert manager.get_state() == RaceState.RUNNING

    # Telemetry ingestion when running
    telemetry_payload = {
        "node_id": "treadmill-01",
        "distance_m": 120.0,
        "elapsed_time_ms": 15000,
        "instantaneous_speed_kph": 10.0,
    }

    progress = manager.update_telemetry(telemetry_payload)
    assert progress["treadmill-01"]["distance_m"] == 120.0
    assert progress["treadmill-01"]["progress_percent"] == 24.0  # 120 / 500 * 100

    manager.stop_race()
    assert manager.get_state() == RaceState.STOPPED

    manager.reset_race()
    assert manager.get_state() == RaceState.IDLE
    assert len(manager.get_registered_nodes()) == 0


def test_calorie_challenge():
    manager = RaceManager()
    config = RaceConfig(race_type="calories", target_value=50.0)  # 50 kcal target
    manager.configure(config)
    manager.register_node("bike-01", "Rider A")
    manager.start_race()

    # Step 1: Low calories
    progress = manager.update_telemetry({
        "node_id": "bike-01",
        "elapsed_time_ms": 10000,
        "power_watts": 200,
        "calories": 20.0
    })
    assert progress["bike-01"]["progress_percent"] == 40.0
    assert progress["bike-01"]["finished_time_ms"] is None

    # Step 2: Cross target (50 kcal)
    progress = manager.update_telemetry({
        "node_id": "bike-01",
        "elapsed_time_ms": 25000,
        "power_watts": 200,
        "calories": 52.0
    })
    assert progress["bike-01"]["progress_percent"] >= 100.0
    assert progress["bike-01"]["finished_time_ms"] == 25000

    # Step 3: Send more telemetry, should remain locked to Step 2 values
    progress = manager.update_telemetry({
        "node_id": "bike-01",
        "elapsed_time_ms": 35000,
        "power_watts": 150,
        "calories": 70.0
    })
    assert progress["bike-01"]["finished_time_ms"] == 25000
    assert progress["bike-01"]["elapsed_time_ms"] == 25000
    assert progress["bike-01"]["calories"] == 52.0


def test_max_power_challenge():
    manager = RaceManager()
    config = RaceConfig(race_type="max_power", duration_sec=30)  # 30 seconds
    manager.configure(config)
    manager.register_node("bike-01", "Rider A")
    manager.start_race()

    progress = manager.update_telemetry({
        "node_id": "bike-01",
        "elapsed_time_ms": 5000,
        "power_watts": 180,
    })
    assert progress["bike-01"]["max_power_watts"] == 180

    progress = manager.update_telemetry({
        "node_id": "bike-01",
        "elapsed_time_ms": 10000,
        "power_watts": 350,
    })
    assert progress["bike-01"]["max_power_watts"] == 350

    progress = manager.update_telemetry({
        "node_id": "bike-01",
        "elapsed_time_ms": 15000,
        "power_watts": 120,
    })
    # Peak max wattage should remain 350
    assert progress["bike-01"]["max_power_watts"] == 350


def test_race_auto_stop_on_completion():
    manager = RaceManager()
    config = RaceConfig(race_type="distance", target_value=100.0)  # 100 meters
    manager.configure(config)
    manager.register_node("node-01", "Runner A")
    manager.register_node("node-02", "Runner B")
    manager.start_race()

    assert manager.get_state() == RaceState.RUNNING

    # Runner A completes 50m
    progress = manager.update_telemetry({
        "node_id": "node-01",
        "distance_m": 50.0,
        "elapsed_time_ms": 10000,
    })
    assert manager.get_state() == RaceState.RUNNING
    assert progress["node-01"]["finished_time_ms"] is None

    # Runner B completes 100m (reaches 100%)
    progress = manager.update_telemetry({
        "node_id": "node-02",
        "distance_m": 100.0,
        "elapsed_time_ms": 15000,
    })
    assert manager.get_state() == RaceState.RUNNING  # node-01 is still active
    assert progress["node-02"]["finished_time_ms"] == 15000

    # Runner A completes 100m (reaches 100%)
    progress = manager.update_telemetry({
        "node_id": "node-01",
        "distance_m": 100.0,
        "elapsed_time_ms": 18000,
    })
    # Now all registered nodes have finished, the race state should transition to STOPPED
    assert manager.get_state() == RaceState.STOPPED
    assert progress["node-01"]["finished_time_ms"] == 18000
    assert manager._end_time_epoch_ms is not None


def test_close_race_keeps_unfinished_progress_for_awards():
    manager = RaceManager()
    config = RaceConfig(race_type="distance", target_value=100.0)
    manager.configure(config)
    manager.register_node("node-01", "Runner A")
    manager.register_node("node-02", "Runner B")
    manager.start_race()

    progress = manager.update_telemetry({
        "node_id": "node-01",
        "distance_m": 100.0,
        "elapsed_time_ms": 15000,
    })
    assert progress["node-01"]["finished_time_ms"] == 15000
    assert progress["node-02"]["finished_time_ms"] is None
    assert manager.get_state() == RaceState.RUNNING

    manager.close_race()

    assert manager.get_state() == RaceState.STOPPED
    assert manager._end_time_epoch_ms is not None
    assert manager.get_leaderboard_progress()["node-01"]["finished_time_ms"] == 15000
    assert manager.get_leaderboard_progress()["node-02"]["finished_time_ms"] is None


def test_configure_from_stopped_state():
    manager = RaceManager()
    config1 = RaceConfig(race_type="distance", target_value=100.0)
    manager.configure(config1)
    manager.register_node("node-01", "Runner A")
    manager.start_race()
    
    # Complete the race
    manager.update_telemetry({
        "node_id": "node-01",
        "distance_m": 100.0,
        "elapsed_time_ms": 10000,
    })
    assert manager.get_state() == RaceState.STOPPED
    
    # Configure new race from STOPPED state
    config2 = RaceConfig(race_type="calories", target_value=50.0)
    manager.configure(config2)
    
    assert manager.get_state() == RaceState.READY
    assert manager.get_config().race_type == "calories"
    assert manager.get_config().target_value == 50.0
    # Confirm old results/registrations are reset
    assert len(manager.get_registered_nodes()) == 0


def test_unbound_station_start_and_telemetry():
    manager = RaceManager()
    config = RaceConfig(race_type="distance", target_value=100.0)
    manager.configure(config)

    # Station 1: bound to "bike-01"
    manager.assign_station(1, "bike-01")
    manager.register_athlete(1, "Athlete Bound")

    # Station 2: unbound (no device node assigned)
    manager.register_athlete(2, "Athlete Unbound")

    # Check READY state leaderboard
    ready_leaderboard = manager.get_leaderboard_progress()
    assert "bike-01" in ready_leaderboard
    assert "station-2" in ready_leaderboard
    assert ready_leaderboard["bike-01"]["athlete_name"] == "Athlete Bound"
    assert ready_leaderboard["station-2"]["athlete_name"] == "Athlete Unbound"

    # Start race
    manager.start_race()
    assert manager.get_state() == RaceState.RUNNING

    # Check RUNNING state leaderboard
    running_leaderboard = manager.get_leaderboard_progress()
    assert "bike-01" in running_leaderboard
    assert "station-2" in running_leaderboard
    assert running_leaderboard["station-2"]["progress_percent"] == 0.0

    # Send telemetry for Station 1
    progress = manager.update_telemetry({
        "node_id": "bike-01",
        "distance_m": 50.0,
        "elapsed_time_ms": 5000,
    })
    assert progress["bike-01"]["progress_percent"] == 50.0
    assert progress["station-2"]["progress_percent"] == 0.0
    assert manager.get_state() == RaceState.RUNNING

    # Send finished telemetry for Station 1
    progress = manager.update_telemetry({
        "node_id": "bike-01",
        "distance_m": 100.0,
        "elapsed_time_ms": 10000,
    })
    # Since only Station 1 is bound and active, it should auto-stop the race (ignoring unbound Station 2)
    assert manager.get_state() == RaceState.STOPPED
    assert progress["bike-01"]["finished_time_ms"] == 10000


