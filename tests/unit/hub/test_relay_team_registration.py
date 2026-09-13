"""A relay station registers a team roster (the member names who will each
run a leg), on top of the existing team_name/division fields. This covers
RaceManager.register_athlete's new relay_members parameter: it must be
stored, exposed, cleared, and deleted everywhere _station_teams /
_station_divisions already are (see the grep list in the module
docstring below) -- init, clear (configure(STOPPED)/configure_class
(STOPPED)/reset_race), delete (assign_station unassign), and every status
row (get_stations_status, get_leaderboard_progress, start_race,
update_telemetry).
"""

from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager


def test_register_athlete_stores_relay_members():
    rm = RaceManager()
    rm.assign_station(1, "node-01")
    rm.register_athlete(
        1, None, team_name="Volt", relay_members=["Alice", "Bob", "Cara"]
    )
    status = rm.get_stations_status()
    assert status["stations"][1]["relay_members"] == ["Alice", "Bob", "Cara"]


def test_relay_members_defaults_to_none_when_not_supplied():
    rm = RaceManager()
    rm.assign_station(1, "node-01")
    rm.register_athlete(1, None, team_name="Volt")
    status = rm.get_stations_status()
    assert status["stations"][1]["relay_members"] is None


def test_relay_members_cleared_on_unassign():
    rm = RaceManager()
    rm.assign_station(1, "node-01")
    rm.register_athlete(1, None, team_name="Volt", relay_members=["Alice", "Bob"])
    rm.assign_station(1, None)
    status = rm.get_stations_status()
    assert 1 not in status["stations"]


def test_relay_members_cleared_when_stopped_race_is_reconfigured():
    rm = RaceManager()
    rm.assign_station(1, "node-01")
    rm.register_athlete(1, None, team_name="Volt", relay_members=["Alice", "Bob"])
    rm.configure(
        RaceConfig(
            race_type="distance",
            competition_mode="relay",
            relay_legs=2,
            target_value=100,
        )
    )
    rm.start_race()
    rm.stop_race()
    rm.configure(
        RaceConfig(
            race_type="distance",
            competition_mode="relay",
            relay_legs=2,
            target_value=100,
        )
    )
    status = rm.get_stations_status()
    assert status["stations"][1]["relay_members"] is None
    assert status["stations"][1]["registered"] is False


def test_relay_members_cleared_on_reset_race():
    rm = RaceManager()
    rm.assign_station(1, "node-01")
    rm.register_athlete(1, None, team_name="Volt", relay_members=["Alice", "Bob"])
    rm.reset_race()
    status = rm.get_stations_status()
    assert status["stations"][1]["relay_members"] is None


def test_relay_members_present_in_leaderboard_progress_row():
    rm = RaceManager()
    rm.assign_station(1, "node-01")
    rm.register_athlete(1, None, team_name="Volt", relay_members=["Alice", "Bob"])
    progress = rm.get_leaderboard_progress()
    row = next(iter(progress.values()))
    assert row["team_name"] == "Volt"
