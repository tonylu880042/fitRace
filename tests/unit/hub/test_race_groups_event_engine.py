"""RaceEventEngine for a "mixed" race (Batch 1a, commit 4): each equipment
group is evaluated independently, against only its own progress rows and
its own scoped RaceConfig (RaceConfig.scoped_config). Fire-once dedupe
state that used to be race-wide (final_sprint/countdown) must be keyed per
group, so group 2's announcement is not suppressed by group 1's already
having fired, and neither one double-fires. Rows with no group
(group_index None -- unmatched equipment) never generate events.

Non-mixed behaviour is exercised by the untouched
tests/unit/hub/test_race_event_engine.py -- this module only covers the
new mixed-race branch.
"""

from hub_server.domain.models import RaceConfig, RaceGroup
from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.race_event_engine import RaceEventEngine


def test_mixed_race_checkpoint_events_use_each_groups_own_target():
    manager = RaceManager()
    engine = RaceEventEngine()
    config = RaceConfig(
        race_type="mixed",
        groups=[
            RaceGroup(
                equipment_types=["treadmill"], race_type="distance", target_value=400.0
            ),
            RaceGroup(
                equipment_types=["rower"], race_type="distance", target_value=1000.0
            ),
        ],
    )
    manager.configure(config)
    manager.register_node("tm-1", "Runner")
    manager.register_node("row-1", "Rower")
    manager.start_race()

    # Treadmill at 104/400 = 26% -- past its own 25% checkpoint.
    progress = manager.update_telemetry(
        {
            "node_id": "tm-1",
            "equipment_type": "treadmill",
            "distance_m": 104.0,
            "elapsed_time_ms": 15000,
        }
    )
    events = engine.evaluate(manager, progress)
    checkpoint_events = [e for e in events if e["event_type"] == "checkpoint_crossed"]
    assert len(checkpoint_events) == 1
    assert checkpoint_events[0]["data"]["node_id"] == "tm-1"
    assert checkpoint_events[0]["data"]["checkpoint_pct"] == 25
    # segment_value/segment_unit only come out right if the checkpoint was
    # evaluated against the group's OWN scoped ("distance") config -- the
    # mixed top-level config's race_type ("mixed") matches none of
    # _check_checkpoints' race_type branches and would leave these blank.
    assert checkpoint_events[0]["data"]["segment_value"] == 104.0
    assert checkpoint_events[0]["data"]["segment_unit"] == "m"

    # Rower at 260/1000 = 26% of ITS OWN (much larger) target -- also
    # crosses 25%, computed against the rower group's own scoped config,
    # not the treadmill's 400 m target.
    progress = manager.update_telemetry(
        {
            "node_id": "row-1",
            "equipment_type": "rower",
            "distance_m": 260.0,
            "elapsed_time_ms": 20000,
        }
    )
    events = engine.evaluate(manager, progress)
    checkpoint_events = [e for e in events if e["event_type"] == "checkpoint_crossed"]
    assert len(checkpoint_events) == 1
    assert checkpoint_events[0]["data"]["node_id"] == "row-1"
    assert checkpoint_events[0]["data"]["checkpoint_pct"] == 25


def test_mixed_race_unmatched_rows_produce_no_events():
    manager = RaceManager()
    engine = RaceEventEngine()
    config = RaceConfig(
        race_type="mixed",
        groups=[
            RaceGroup(
                equipment_types=["treadmill"], race_type="distance", target_value=400.0
            ),
            RaceGroup(
                equipment_types=["rower"], race_type="distance", target_value=1000.0
            ),
        ],
    )
    manager.configure(config)
    manager.register_node("mystery-1", "Mystery")
    manager.start_race()

    progress = manager.update_telemetry(
        {
            "node_id": "mystery-1",
            "equipment_type": "fan_bike",
            "distance_m": 999.0,
            "elapsed_time_ms": 15000,
        }
    )
    events = engine.evaluate(manager, progress)
    assert events == []


def test_mixed_race_final_sprint_fires_independently_per_group():
    manager = RaceManager()
    engine = RaceEventEngine()
    config = RaceConfig(
        race_type="mixed",
        groups=[
            RaceGroup(
                equipment_types=["treadmill"], race_type="distance", target_value=1000.0
            ),
            RaceGroup(
                equipment_types=["rower"], race_type="distance", target_value=1000.0
            ),
        ],
    )
    manager.configure(config)
    manager.register_node("tm-1", "Runner")
    manager.register_node("row-1", "Rower")
    manager.start_race()

    # Treadmill crosses 85% -- its group's final_sprint fires.
    progress = manager.update_telemetry(
        {
            "node_id": "tm-1",
            "equipment_type": "treadmill",
            "distance_m": 860.0,
            "elapsed_time_ms": 60000,
        }
    )
    events = engine.evaluate(manager, progress)
    assert len([e for e in events if e["event_type"] == "final_sprint"]) == 1

    # Rower ALSO crosses 85% in its own, separate group -- must fire too,
    # not suppressed by the treadmill group's dedupe already having fired.
    progress = manager.update_telemetry(
        {
            "node_id": "row-1",
            "equipment_type": "rower",
            "distance_m": 870.0,
            "elapsed_time_ms": 65000,
        }
    )
    events = engine.evaluate(manager, progress)
    assert len([e for e in events if e["event_type"] == "final_sprint"]) == 1

    # And neither group re-fires on a later evaluate of the same progress.
    events = engine.evaluate(manager, progress)
    assert len([e for e in events if e["event_type"] == "final_sprint"]) == 0


def test_mixed_race_countdown_fires_independently_per_group():
    manager = RaceManager()
    engine = RaceEventEngine()
    config = RaceConfig(
        race_type="mixed",
        groups=[
            RaceGroup(equipment_types=["bike-a"], race_type="time", duration_sec=30),
            RaceGroup(equipment_types=["bike-b"], race_type="time", duration_sec=30),
        ],
    )
    manager.configure(config)
    manager.register_node("a-1", "A")
    manager.register_node("b-1", "B")
    manager.start_race()

    # Group A at 20s elapsed (10s remaining of its own 30s) -- countdown 10
    # fires for group A. distance_m is supplied (like
    # test_event_engine_countdown in test_race_event_engine.py) since
    # _check_checkpoints' "time" branch reads it for segment_value.
    progress = manager.update_telemetry(
        {
            "node_id": "a-1",
            "equipment_type": "bike-a",
            "elapsed_time_ms": 20000,
            "distance_m": 200,
        }
    )
    events = engine.evaluate(manager, progress)
    countdown_events = [e for e in events if e["event_type"] == "countdown"]
    assert len(countdown_events) == 1
    assert countdown_events[0]["data"]["seconds_left"] == 10

    # Group B independently reaches the SAME 10s-remaining mark -- must
    # also fire; it is not suppressed by group A's countdown dedupe.
    progress = manager.update_telemetry(
        {
            "node_id": "b-1",
            "equipment_type": "bike-b",
            "elapsed_time_ms": 20000,
            "distance_m": 200,
        }
    )
    events = engine.evaluate(manager, progress)
    countdown_events = [e for e in events if e["event_type"] == "countdown"]
    assert len(countdown_events) == 1
    assert countdown_events[0]["data"]["seconds_left"] == 10


def test_mixed_race_reset_clears_per_group_dedupe_state():
    manager = RaceManager()
    engine = RaceEventEngine()
    config = RaceConfig(
        race_type="mixed",
        groups=[
            RaceGroup(
                equipment_types=["treadmill"], race_type="distance", target_value=400.0
            ),
            RaceGroup(
                equipment_types=["rower"], race_type="distance", target_value=1000.0
            ),
        ],
    )
    manager.configure(config)
    manager.register_node("tm-1", "Runner")
    manager.start_race()

    progress = manager.update_telemetry(
        {
            "node_id": "tm-1",
            "equipment_type": "treadmill",
            "distance_m": 110.0,
            "elapsed_time_ms": 10000,
        }
    )
    events = engine.evaluate(manager, progress)
    assert len([e for e in events if e["event_type"] == "checkpoint_crossed"]) == 1

    engine.reset()

    events = engine.evaluate(manager, progress)
    assert len([e for e in events if e["event_type"] == "checkpoint_crossed"]) == 1
