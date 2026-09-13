"""The venue splits overall ranking by men/women. RaceManager must carry an
optional `division` ("men"/"women"/None) for each station exactly the way
it already carries `team_name`: stored per station via register_athlete,
surfaced on get_stations_status rows and every leaderboard row (placeholder
and live), and cleared everywhere team_name is cleared (assign_station
unassign, register-in-STOPPED-state configure, reset_race)."""

from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager


def test_register_athlete_stores_division_on_stations_status():
    manager = RaceManager()
    manager.assign_station(1, "bike-01")
    manager.register_athlete(1, "Runner A", division="women")

    status = manager.get_stations_status()
    assert status["stations"][1]["division"] == "women"


def test_register_athlete_defaults_division_to_none():
    manager = RaceManager()
    manager.assign_station(1, "bike-01")
    manager.register_athlete(1, "Runner A")

    status = manager.get_stations_status()
    assert status["stations"][1]["division"] is None


def test_unbound_station_registration_carries_division():
    manager = RaceManager()
    manager.register_athlete(2, "Runner B", division="men")

    status = manager.get_stations_status()
    assert status["stations"][2]["division"] == "men"


def test_ready_state_leaderboard_carries_division_bound_and_placeholder():
    manager = RaceManager()
    config = RaceConfig(race_type="distance", target_value=100.0)
    manager.configure(config)
    manager.assign_station(1, "bike-01")
    manager.register_athlete(1, "Athlete Bound", division="women")
    manager.register_athlete(2, "Athlete Unbound", division="men")

    leaderboard = manager.get_leaderboard_progress()
    assert leaderboard["bike-01"]["division"] == "women"
    assert leaderboard["station-2"]["division"] == "men"


def test_running_leaderboard_and_telemetry_carry_division():
    manager = RaceManager()
    config = RaceConfig(race_type="distance", target_value=100.0)
    manager.configure(config)
    manager.assign_station(1, "bike-01")
    manager.register_athlete(1, "Athlete Bound", division="women")
    manager.start_race()

    running_leaderboard = manager.get_leaderboard_progress()
    assert running_leaderboard["bike-01"]["division"] == "women"

    progress = manager.update_telemetry(
        {"node_id": "bike-01", "distance_m": 10.0, "elapsed_time_ms": 1000}
    )
    assert progress["bike-01"]["division"] == "women"


def test_reset_race_clears_division():
    manager = RaceManager()
    config = RaceConfig(race_type="distance", target_value=100.0)
    manager.configure(config)
    manager.assign_station(1, "bike-01")
    manager.register_athlete(1, "Athlete Bound", division="women")

    manager.reset_race()

    status = manager.get_stations_status()
    # Hardware station mapping (self._stations) survives reset_race, but the
    # registration -- and therefore its division -- must not.
    assert 1 not in status["stations"] or status["stations"][1]["division"] is None
    assert status["stations"].get(1, {}).get("registered", False) is False


def test_unassign_station_clears_division():
    manager = RaceManager()
    manager.assign_station(1, "bike-01")
    manager.register_athlete(1, "Athlete Bound", division="women")

    manager.assign_station(1, None)

    status = manager.get_stations_status()
    assert 1 not in status["stations"]
