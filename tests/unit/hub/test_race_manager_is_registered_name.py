"""RaceManager.update_telemetry is the ONLY producer of the
`is_registered_name` flag that RaceResultsQuery's dedupe (see
test_standings_placeholder_dedupe.py) relies on to stop merging different,
never-registered stations across heats. Every one of those dedupe tests
builds its own leaderboard rows by hand, so none of them actually exercises
RaceManager -- a bug in the producer (e.g. always tagging True) would leave
that whole suite green while the live bug it fixes stays live.

These tests drive RaceManager itself through both leaderboard-row paths
(a station number, and the legacy bare node_id path) and assert the flag
RaceManager actually writes. The final test is end-to-end: two heats run
through a real RaceManager, saved to a real RaceResultStore the way the app
does (get_state_snapshot() -> save_finished_snapshot()), each with the SAME
unregistered station -- proving RaceResultsQuery.get_standings() keeps them
as two separate rows rather than merging on the shared "Station 1" text.
"""

from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.race_result_store import RaceResultStore
from hub_server.usecases.race_results_query import RaceResultsQuery


def test_unregistered_station_is_flagged_as_placeholder_name():
    manager = RaceManager()
    manager.assign_station(1, "node-01")
    manager.configure(RaceConfig(race_type="time", duration_sec=60))
    manager.start_race()

    progress = manager.update_telemetry(
        {
            "node_id": "node-01",
            "equipment_type": "rowing_machine",
            "distance_m": 10.0,
            "elapsed_time_ms": 1000,
        }
    )

    assert progress["node-01"]["athlete_name"] == "Station 1"
    assert progress["node-01"]["is_registered_name"] is False


def test_registered_station_is_flagged_as_genuine_name():
    manager = RaceManager()
    manager.assign_station(1, "node-01")
    manager.register_athlete(1, "Amy")
    manager.configure(RaceConfig(race_type="time", duration_sec=60))
    manager.start_race()

    progress = manager.update_telemetry(
        {
            "node_id": "node-01",
            "equipment_type": "rowing_machine",
            "distance_m": 10.0,
            "elapsed_time_ms": 1000,
        }
    )

    assert progress["node-01"]["athlete_name"] == "Amy"
    assert progress["node-01"]["is_registered_name"] is True


def test_unregistered_bare_node_id_is_flagged_as_placeholder_name():
    # No station assignment at all -- the legacy _registered_nodes path.
    manager = RaceManager()
    manager.configure(
        RaceConfig(race_type="distance", target_value=500.0, competition_mode="team")
    )
    manager.start_race()

    progress = manager.update_telemetry(
        {"node_id": "treadmill-99", "distance_m": 10.0, "elapsed_time_ms": 1000}
    )

    assert progress["treadmill-99"]["is_registered_name"] is False


def test_registered_bare_node_id_is_flagged_as_genuine_name():
    manager = RaceManager()
    manager.register_node("treadmill-01", "Runner A")
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.start_race()

    progress = manager.update_telemetry(
        {"node_id": "treadmill-01", "distance_m": 10.0, "elapsed_time_ms": 1000}
    )

    assert progress["treadmill-01"]["athlete_name"] == "Runner A"
    assert progress["treadmill-01"]["is_registered_name"] is True


def test_two_heats_of_the_same_unregistered_station_stay_two_standings_rows(
    tmp_path,
):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    # The test pins fake start times (1_000 / 3_000); a clock at 0 keeps the
    # hub deadline from treating them as long expired.
    manager = RaceManager(now_ms=lambda: 0)

    # Heat 1 -- station 1, never registered.
    manager.assign_station(1, "node-01")
    manager.configure(RaceConfig(race_type="time", duration_sec=60))
    manager.start_race()
    manager._start_time_epoch_ms = 1_000
    manager.update_telemetry(
        {
            "node_id": "node-01",
            "equipment_type": "rowing_machine",
            "distance_m": 100.0,
            "elapsed_time_ms": 5_000,
        }
    )
    manager.stop_race()
    manager._end_time_epoch_ms = 2_000
    snapshot_1 = manager.get_state_snapshot()
    assert snapshot_1["leaderboard"]["node-01"]["is_registered_name"] is False
    saved_1 = store.save_finished_snapshot(snapshot_1)
    assert saved_1 is not None

    manager.reset_race()

    # Heat 2 -- same physical station, still never registered.
    manager.assign_station(1, "node-01")
    manager.configure(RaceConfig(race_type="time", duration_sec=60))
    manager.start_race()
    manager._start_time_epoch_ms = 3_000
    manager.update_telemetry(
        {
            "node_id": "node-01",
            "equipment_type": "rowing_machine",
            "distance_m": 80.0,
            "elapsed_time_ms": 5_000,
        }
    )
    manager.stop_race()
    manager._end_time_epoch_ms = 4_000
    snapshot_2 = manager.get_state_snapshot()
    assert snapshot_2["leaderboard"]["node-01"]["is_registered_name"] is False
    saved_2 = store.save_finished_snapshot(snapshot_2)
    assert saved_2 is not None

    query = RaceResultsQuery(store)
    standings = query.get_standings()

    rows = standings["sections"][0]["rows"]
    assert len(rows) == 2
    assert all(r["athlete_name"] == "Station 1" for r in rows)
