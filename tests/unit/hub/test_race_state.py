import pytest
from pydantic import ValidationError
from hub_server.domain.models import RaceState, RaceConfig
from hub_server.usecases.race_manager import RaceManager


def test_race_config_model():
    config = RaceConfig(
        race_type="distance", target_value=1000.0, duration_sec=0  # 1000 meters
    )
    assert config.race_type == "distance"
    assert config.target_value == 1000.0
    assert config.duration_sec == 0
    assert config.competition_mode == "individual"
    assert config.team_scoring_policy == "average"
    assert config.team_completion_policy == "aggregate"


def test_race_config_accepts_team_competition_mode():
    config = RaceConfig(
        race_type="distance",
        target_value=1000.0,
        competition_mode="team",
        team_scoring_policy="total",
    )

    assert config.competition_mode == "team"
    assert config.team_scoring_policy == "total"


@pytest.mark.parametrize("race_type", ["distance", "calories"])
def test_race_config_requires_positive_target_for_target_based_races(race_type):
    with pytest.raises(ValidationError):
        RaceConfig(race_type=race_type, target_value=0, duration_sec=0)


@pytest.mark.parametrize("race_type", ["time", "max_power", "watts"])
def test_race_config_requires_positive_duration_for_duration_based_races(race_type):
    with pytest.raises(ValidationError):
        RaceConfig(race_type=race_type, target_value=0, duration_sec=0)


def test_race_config_rejects_unknown_race_type():
    with pytest.raises(ValidationError):
        RaceConfig(race_type="mystery", target_value=100, duration_sec=0)


def test_race_config_rejects_unknown_competition_mode():
    with pytest.raises(ValidationError):
        RaceConfig(
            race_type="distance",
            target_value=100,
            competition_mode="relay",
        )


def test_race_config_rejects_unknown_team_scoring_policy():
    with pytest.raises(ValidationError):
        RaceConfig(
            race_type="distance",
            target_value=100,
            competition_mode="team",
            team_scoring_policy="fastest",
        )


def test_race_config_rejects_unknown_team_completion_policy():
    with pytest.raises(ValidationError):
        RaceConfig(
            race_type="distance",
            target_value=100,
            competition_mode="team",
            team_completion_policy="first_member",
        )


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


def test_team_leaderboard_aggregates_distance_by_average_progress():
    manager = RaceManager()
    config = RaceConfig(
        race_type="distance",
        target_value=100.0,
        competition_mode="team",
        team_scoring_policy="average",
    )
    manager.assign_station(1, "node-01")
    manager.assign_station(2, "node-02")
    manager.assign_station(3, "node-03")
    manager.register_athlete(1, "Runner A", team_name="Volt")
    manager.register_athlete(2, "Runner B", team_name="Volt")
    manager.register_athlete(3, "Runner C", team_name="Apex")
    manager.configure(config)
    manager.start_race()

    manager.update_telemetry({"node_id": "node-01", "distance_m": 80.0, "elapsed_time_ms": 10000})
    manager.update_telemetry({"node_id": "node-02", "distance_m": 40.0, "elapsed_time_ms": 10000})
    manager.update_telemetry({"node_id": "node-03", "distance_m": 70.0, "elapsed_time_ms": 10000})

    teams = manager.get_team_leaderboard_progress()

    assert [team["team_name"] for team in teams] == ["Apex", "Volt"]
    assert teams[0]["member_count"] == 1
    assert teams[0]["progress_percent"] == 70.0
    assert teams[0]["score_value"] == 70.0
    assert teams[1]["member_count"] == 2
    assert teams[1]["distance_m"] == 120.0
    assert teams[1]["progress_percent"] == 60.0


def test_team_leaderboard_aggregates_distance_by_total_progress():
    manager = RaceManager()
    config = RaceConfig(
        race_type="distance",
        target_value=100.0,
        competition_mode="team",
        team_scoring_policy="total",
    )
    manager.assign_station(1, "node-01")
    manager.assign_station(2, "node-02")
    manager.assign_station(3, "node-03")
    manager.register_athlete(1, "Runner A", team_name="Volt")
    manager.register_athlete(2, "Runner B", team_name="Volt")
    manager.register_athlete(3, "Runner C", team_name="Apex")
    manager.configure(config)
    manager.start_race()

    manager.update_telemetry({"node_id": "node-01", "distance_m": 80.0, "elapsed_time_ms": 10000})
    manager.update_telemetry({"node_id": "node-02", "distance_m": 40.0, "elapsed_time_ms": 10000})
    manager.update_telemetry({"node_id": "node-03", "distance_m": 70.0, "elapsed_time_ms": 10000})

    teams = manager.get_team_leaderboard_progress()

    assert [team["team_name"] for team in teams] == ["Volt", "Apex"]
    assert teams[0]["score_value"] == 120.0
    assert teams[0]["progress_percent"] == 120.0
    assert [member["athlete_name"] for member in teams[0]["members"]] == ["Runner A", "Runner B"]


def test_team_leaderboard_all_members_requires_every_distance_member_to_finish():
    manager = RaceManager()
    config = RaceConfig(
        race_type="distance",
        target_value=100.0,
        competition_mode="team",
        team_scoring_policy="average",
        team_completion_policy="all_members",
    )
    manager.assign_station(1, "node-01")
    manager.assign_station(2, "node-02")
    manager.assign_station(3, "node-03")
    manager.assign_station(4, "node-04")
    manager.register_athlete(1, "Runner A", team_name="Volt")
    manager.register_athlete(2, "Runner B", team_name="Volt")
    manager.register_athlete(3, "Runner C", team_name="Apex")
    manager.register_athlete(4, "Runner D", team_name="Apex")
    manager.configure(config)
    manager.start_race()

    manager.update_telemetry({"node_id": "node-01", "distance_m": 100.0, "elapsed_time_ms": 10000})
    manager.update_telemetry({"node_id": "node-02", "distance_m": 70.0, "elapsed_time_ms": 10000})
    manager.update_telemetry({"node_id": "node-03", "distance_m": 100.0, "elapsed_time_ms": 12000})
    manager.update_telemetry({"node_id": "node-04", "distance_m": 100.0, "elapsed_time_ms": 18000})

    teams = manager.get_team_leaderboard_progress()

    assert [team["team_name"] for team in teams] == ["Apex", "Volt"]
    assert teams[0]["team_finished"] is True
    assert teams[0]["team_finished_time_ms"] == 18000
    assert teams[1]["team_finished"] is False
    assert teams[1]["team_finished_time_ms"] is None
    assert teams[1]["progress_percent"] == 85.0


def test_team_leaderboard_all_members_applies_to_calories():
    manager = RaceManager()
    config = RaceConfig(
        race_type="calories",
        target_value=50.0,
        competition_mode="team",
        team_scoring_policy="average",
        team_completion_policy="all_members",
    )
    manager.assign_station(1, "node-01")
    manager.assign_station(2, "node-02")
    manager.register_athlete(1, "Rider A", team_name="Volt")
    manager.register_athlete(2, "Rider B", team_name="Volt")
    manager.configure(config)
    manager.start_race()

    manager.update_telemetry({"node_id": "node-01", "calories": 52.0, "elapsed_time_ms": 10000})
    manager.update_telemetry({"node_id": "node-02", "calories": 40.0, "elapsed_time_ms": 10000})

    teams = manager.get_team_leaderboard_progress()

    assert teams[0]["team_name"] == "Volt"
    assert teams[0]["team_finished"] is False
    assert teams[0]["progress_percent"] == 90.0


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
