"""RaceManager scoring/auto-stop for a "mixed" race (Batch 1a, commit 2):
each equipment group is judged against its OWN scoped RaceConfig (see
RaceConfig.scoped_config/group_index_for in hub_server/domain/models.py),
not the mixed top-level config. A node whose equipment_type matches no
group never finishes and never blocks auto-stop -- except when EVERY row
is unmatched, in which case the race must not auto-stop at all (an
all-skipped partition is not vacuously "all finished").

Non-mixed races must be byte-for-byte unaffected: no `group_index` key on
their progress rows.
"""

from hub_server.domain.models import RaceConfig, RaceGroup, RaceState
from hub_server.usecases.race_manager import RaceManager


def _mixed_two_distance_groups(target_a=100.0, target_b=100.0):
    return RaceConfig(
        race_type="mixed",
        groups=[
            RaceGroup(
                equipment_types=["treadmill"],
                race_type="distance",
                target_value=target_a,
            ),
            RaceGroup(
                equipment_types=["rower"], race_type="distance", target_value=target_b
            ),
        ],
    )


def test_mixed_race_each_group_finishes_at_its_own_distance_target():
    manager = RaceManager()
    manager.configure(_mixed_two_distance_groups(target_a=800.0, target_b=500.0))
    manager.register_node("node-tm", "Runner A")
    manager.register_node("node-row", "Rower B")
    manager.start_race()

    progress = manager.update_telemetry(
        {
            "node_id": "node-tm",
            "equipment_type": "treadmill",
            "distance_m": 800.0,
            "elapsed_time_ms": 200000,
        }
    )
    assert progress["node-tm"]["finished_time_ms"] == 200000
    assert progress["node-tm"]["group_index"] == 0
    assert progress["node-tm"]["progress_percent"] == 100.0

    # Rower's target is 500m -- 250m is only 50%, not finished yet.
    progress = manager.update_telemetry(
        {
            "node_id": "node-row",
            "equipment_type": "rower",
            "distance_m": 250.0,
            "elapsed_time_ms": 150000,
        }
    )
    assert progress["node-row"]["finished_time_ms"] is None
    assert progress["node-row"]["group_index"] == 1
    assert progress["node-row"]["progress_percent"] == 50.0
    assert manager.get_state() == RaceState.RUNNING


def test_mixed_race_time_boxed_group_and_distance_group_autostop_needs_both():
    manager = RaceManager()
    config = RaceConfig(
        race_type="mixed",
        groups=[
            RaceGroup(equipment_types=["fan_bike"], race_type="time", duration_sec=60),
            RaceGroup(
                equipment_types=["treadmill"], race_type="distance", target_value=100.0
            ),
        ],
    )
    manager.configure(config)
    manager.register_node("bike-1", "Biker")
    manager.register_node("tm-1", "Runner")
    manager.start_race()

    # Bike is time-boxed at 60s, only 30s elapsed -- not finished.
    progress = manager.update_telemetry(
        {
            "node_id": "bike-1",
            "equipment_type": "fan_bike",
            "elapsed_time_ms": 30000,
            "distance_m": 5.0,
        }
    )
    assert progress["bike-1"]["group_index"] == 0
    assert progress["bike-1"]["progress_percent"] == 50.0
    assert progress["bike-1"]["finished_time_ms"] is None

    # Treadmill hits its distance target -- finishes, but bike hasn't, so
    # the race as a whole must stay RUNNING.
    progress = manager.update_telemetry(
        {
            "node_id": "tm-1",
            "equipment_type": "treadmill",
            "elapsed_time_ms": 20000,
            "distance_m": 100.0,
        }
    )
    assert progress["tm-1"]["group_index"] == 1
    assert progress["tm-1"]["finished_time_ms"] == 20000
    assert manager.get_state() == RaceState.RUNNING

    # Bike crosses its 60s duration -- now both groups are done.
    manager.update_telemetry(
        {
            "node_id": "bike-1",
            "equipment_type": "fan_bike",
            "elapsed_time_ms": 60000,
            "distance_m": 10.0,
        }
    )
    assert manager.get_state() == RaceState.STOPPED


def test_mixed_race_unmatched_equipment_type_never_finishes_and_does_not_block_autostop():
    manager = RaceManager()
    manager.configure(_mixed_two_distance_groups())
    manager.register_node("tm-1", "Runner")
    manager.register_node("row-1", "Rower")
    manager.register_node("mystery-1", "Mystery")
    manager.start_race()

    # An equipment type that matches no group.
    progress = manager.update_telemetry(
        {
            "node_id": "mystery-1",
            "equipment_type": "fan_bike",
            "distance_m": 999.0,
            "elapsed_time_ms": 5000,
        }
    )
    assert progress["mystery-1"]["group_index"] is None
    assert progress["mystery-1"]["finished_time_ms"] is None
    assert progress["mystery-1"]["progress_percent"] == 0.0

    manager.update_telemetry(
        {
            "node_id": "tm-1",
            "equipment_type": "treadmill",
            "distance_m": 100.0,
            "elapsed_time_ms": 20000,
        }
    )
    progress = manager.update_telemetry(
        {
            "node_id": "row-1",
            "equipment_type": "rower",
            "distance_m": 100.0,
            "elapsed_time_ms": 25000,
        }
    )

    # Both matched groups are done -- the unmatched node must not block
    # auto-stop, and it still never got a finish time.
    assert manager.get_state() == RaceState.STOPPED
    assert progress["mystery-1"]["finished_time_ms"] is None


def test_mixed_race_never_auto_stops_when_every_row_is_unmatched():
    manager = RaceManager()
    manager.configure(_mixed_two_distance_groups())
    manager.register_node("mystery-1", "Mystery A")
    manager.register_node("mystery-2", "Mystery B")
    manager.start_race()

    manager.update_telemetry(
        {
            "node_id": "mystery-1",
            "equipment_type": "fan_bike",
            "distance_m": 999.0,
            "elapsed_time_ms": 5000,
        }
    )
    progress = manager.update_telemetry(
        {
            "node_id": "mystery-2",
            "equipment_type": "ski_erg",
            "distance_m": 999.0,
            "elapsed_time_ms": 5000,
        }
    )

    assert manager.get_state() == RaceState.RUNNING
    assert progress["mystery-1"]["group_index"] is None
    assert progress["mystery-2"]["group_index"] is None


def test_mixed_race_initial_rows_carry_group_index_from_known_station_equipment():
    manager = RaceManager()
    manager.assign_station(1, "node-tm")
    manager.update_active_node("node-tm", "treadmill")
    manager.configure(_mixed_two_distance_groups())
    manager.register_athlete(1, "Runner A")
    manager.start_race()

    progress = manager.get_leaderboard_progress()
    assert progress["node-tm"]["group_index"] == 0


def test_mixed_race_does_not_auto_stop_while_a_slower_group_has_not_reported_yet():
    # A node whose equipment type is genuinely not known yet (it has never
    # sent telemetry) must BLOCK auto-stop like an ordinary not-yet-finished
    # participant -- it must NOT be treated the same as a node whose type
    # is known but matches no group (which is skipped/never blocks). Without
    # this distinction, a fast group finishing before a slower group's node
    # sends its very first packet would read as "all matched rows finished"
    # and stop the race out from under the slower group.
    manager = RaceManager()
    manager.configure(_mixed_two_distance_groups(target_a=100.0, target_b=500.0))
    manager.register_node("node-tm", "Runner A")
    manager.register_node("node-row", "Rower B")
    manager.start_race()

    # Only the treadmill has reported and finished; the rower has not sent
    # a single telemetry packet yet, so its equipment type is unknown.
    manager.update_telemetry(
        {
            "node_id": "node-tm",
            "equipment_type": "treadmill",
            "distance_m": 100.0,
            "elapsed_time_ms": 20000,
        }
    )
    assert manager.get_state() == RaceState.RUNNING


def test_non_mixed_race_progress_rows_have_no_group_index_key():
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    manager.register_node("node-1", "Runner")
    manager.start_race()

    progress = manager.update_telemetry(
        {"node_id": "node-1", "distance_m": 50.0, "elapsed_time_ms": 10000}
    )
    assert "group_index" not in progress["node-1"]
    assert "group_index" not in manager.get_leaderboard_progress()["node-1"]


def test_non_mixed_race_auto_stop_behaviour_is_unchanged():
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    manager.register_node("node-01", "Runner A")
    manager.start_race()

    progress = manager.update_telemetry(
        {"node_id": "node-01", "distance_m": 100.0, "elapsed_time_ms": 18000}
    )
    assert manager.get_state() == RaceState.STOPPED
    assert progress["node-01"]["finished_time_ms"] == 18000
