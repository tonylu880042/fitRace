"""Anonymous participation at the RaceManager level (no HTTP layer).

FitRaceStudio is sold into Europe; under GDPR the cheapest compliance
posture is to not collect personal data at all. Athlete names have moved
from mandatory to optional. The dangerous part is that several call sites
used to treat `station.get("athlete_name")` truthiness as a proxy for "is
this station registered?" -- once a name can legitimately be absent, that
proxy silently means something else. `get_stations_status()` now exposes an
explicit "registered" boolean (backed by `_station_registrations`
membership) that must be used instead.

These tests cover: registering with no name, that such a station still
counts as registered, that a named registration is unaffected, and that a
team race with a mix of named and anonymous members still groups by team.
"""

from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager


def test_register_athlete_with_none_name_marks_station_registered():
    manager = RaceManager()
    manager.update_active_node("node-01", "fan_bike")
    manager.assign_station(1, "node-01")

    manager.register_athlete(1, None)

    status = manager.get_stations_status()
    assert status["stations"][1]["athlete_name"] is None
    assert status["stations"][1]["registered"] is True


def test_assigned_but_unregistered_station_is_not_registered():
    """A station can have hardware bound to it (assign_station) without
    anyone having registered yet -- that must stay distinguishable from an
    anonymous registration, both of which show athlete_name=None."""
    manager = RaceManager()
    manager.update_active_node("node-01", "fan_bike")
    manager.assign_station(1, "node-01")

    status = manager.get_stations_status()
    assert status["stations"][1]["athlete_name"] is None
    assert status["stations"][1]["registered"] is False


def test_named_registration_still_reports_registered_true():
    manager = RaceManager()
    manager.update_active_node("node-01", "fan_bike")
    manager.assign_station(1, "node-01")
    manager.register_athlete(1, "Tony")

    status = manager.get_stations_status()
    assert status["stations"][1]["athlete_name"] == "Tony"
    assert status["stations"][1]["registered"] is True


def test_anonymous_registration_without_bound_node_is_still_registered():
    manager = RaceManager()
    manager.register_athlete(3, None)

    status = manager.get_stations_status()
    assert status["stations"][3]["node_id"] is None
    assert status["stations"][3]["athlete_name"] is None
    assert status["stations"][3]["registered"] is True


def test_team_race_groups_anonymous_and_named_members_under_same_team():
    manager = RaceManager()
    manager.assign_station(1, "node-01")
    manager.assign_station(2, "node-02")
    manager.register_athlete(1, "Runner A", team_name="Volt")
    manager.register_athlete(2, None, team_name="Volt")

    config = RaceConfig(
        race_type="distance", target_value=1000.0, competition_mode="team"
    )
    manager.configure(config)

    teams = manager.get_team_leaderboard_progress()

    assert [team["team_name"] for team in teams] == ["Volt"]
    volt = teams[0]
    assert volt["member_count"] == 2
    assert [member["athlete_name"] for member in volt["members"]] == [
        "Runner A",
        None,
    ]
