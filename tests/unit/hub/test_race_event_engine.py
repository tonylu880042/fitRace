from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.race_event_engine import RaceEventEngine


def test_event_engine_no_events_when_idle():
    manager = RaceManager()
    engine = RaceEventEngine()
    progress = manager.get_leaderboard_progress()
    events = engine.evaluate(manager, progress)
    assert events == []


def test_event_engine_checkpoint_distance_race():
    manager = RaceManager()
    engine = RaceEventEngine()

    config = RaceConfig(race_type="distance", target_value=400.0)
    manager.configure(config)
    manager.register_node("node-01", "Runner A")
    manager.register_node("node-02", "Runner B")
    manager.start_race()

    # Runner A progresses to 26% (past 25% checkpoint)
    progress = manager.update_telemetry({
        "node_id": "node-01",
        "distance_m": 104.0,
        "elapsed_time_ms": 15000,
    })
    events = engine.evaluate(manager, progress)

    checkpoint_events = [e for e in events if e["event_type"] == "checkpoint_crossed"]
    assert len(checkpoint_events) == 1
    assert checkpoint_events[0]["data"]["checkpoint_pct"] == 25
    assert checkpoint_events[0]["data"]["node_id"] == "node-01"

    # Runner B also passes 25%
    progress = manager.update_telemetry({
        "node_id": "node-02",
        "distance_m": 110.0,
        "elapsed_time_ms": 14000,
    })
    events = engine.evaluate(manager, progress)

    checkpoint_events = [e for e in events if e["event_type"] == "checkpoint_crossed"]
    assert len(checkpoint_events) == 1
    assert checkpoint_events[0]["data"]["is_fastest"] is True  # B was faster


def test_event_engine_no_duplicate_checkpoints():
    manager = RaceManager()
    engine = RaceEventEngine()

    config = RaceConfig(race_type="distance", target_value=400.0)
    manager.configure(config)
    manager.register_node("node-01", "Runner A")
    manager.start_race()

    # Pass 25% checkpoint
    progress = manager.update_telemetry({
        "node_id": "node-01",
        "distance_m": 110.0,
        "elapsed_time_ms": 10000,
    })
    events = engine.evaluate(manager, progress)
    assert len([e for e in events if e["event_type"] == "checkpoint_crossed"]) == 1

    # Same node, same progress level - should not re-emit
    events = engine.evaluate(manager, progress)
    assert len([e for e in events if e["event_type"] == "checkpoint_crossed"]) == 0


def test_event_engine_catch_up_warning():
    manager = RaceManager()
    engine = RaceEventEngine()

    config = RaceConfig(race_type="distance", target_value=1000.0)
    manager.configure(config)
    manager.register_node("node-01", "Runner A")
    manager.register_node("node-02", "Runner B")
    manager.start_race()

    # Runner A at 200m, Runner B at 0m (gap=200m, not small)
    progress = manager.update_telemetry({
        "node_id": "node-01",
        "distance_m": 200.0,
        "elapsed_time_ms": 20000,
    })
    engine.evaluate(manager, progress)

    # Runner B jumps to 150m (gap goes from 200→50, ratio=0.25, gap<100 → triggers)
    progress = manager.update_telemetry({
        "node_id": "node-02",
        "distance_m": 150.0,
        "elapsed_time_ms": 20000,
    })
    events = engine.evaluate(manager, progress)

    catch_up_events = [e for e in events if e["event_type"] == "catch_up_warning"]
    assert len(catch_up_events) == 1
    warning = catch_up_events[0]
    assert warning["data"]["chaser_node_id"] == "node-02"
    assert warning["data"]["target_node_id"] == "node-01"
    assert warning["data"]["gap_unit"] == "m"


def test_event_engine_no_catch_up_for_max_power():
    manager = RaceManager()
    engine = RaceEventEngine()

    config = RaceConfig(race_type="max_power", duration_sec=60)
    manager.configure(config)
    manager.register_node("node-01", "Athlete A")
    manager.register_node("node-02", "Athlete B")
    manager.start_race()

    progress = manager.update_telemetry({
        "node_id": "node-01",
        "elapsed_time_ms": 10000,
        "power_watts": 200,
    })
    progress = manager.update_telemetry({
        "node_id": "node-02",
        "elapsed_time_ms": 10000,
        "power_watts": 180,
    })
    events = engine.evaluate(manager, progress)

    catch_up_events = [e for e in events if e["event_type"] == "catch_up_warning"]
    assert len(catch_up_events) == 0


def test_event_engine_countdown():
    manager = RaceManager()
    engine = RaceEventEngine()

    config = RaceConfig(race_type="time", duration_sec=30)
    manager.configure(config)
    manager.register_node("node-01", "Athlete A")
    manager.start_race()

    # Simulate progress with elapsed_time_ms approaching duration
    progress = manager.update_telemetry({
        "node_id": "node-01",
        "elapsed_time_ms": 20000,
        "distance_m": 200,
    })
    events = engine.evaluate(manager, progress)

    # At 20s elapsed (10s remaining), countdown 10 should trigger
    countdown_events = [e for e in events if e["event_type"] == "countdown"]
    assert len(countdown_events) == 1
    assert countdown_events[0]["data"]["seconds_left"] == 10


def test_event_engine_final_sprint():
    manager = RaceManager()
    engine = RaceEventEngine()

    config = RaceConfig(race_type="distance", target_value=1000.0)
    manager.configure(config)
    manager.register_node("node-01", "Athlete A")
    manager.start_race()

    # 86% progress (above 85% threshold)
    progress = manager.update_telemetry({
        "node_id": "node-01",
        "distance_m": 860.0,
        "elapsed_time_ms": 60000,
    })
    events = engine.evaluate(manager, progress)

    sprint_events = [e for e in events if e["event_type"] == "final_sprint"]
    assert len(sprint_events) == 1


def test_event_engine_final_sprint_once():
    manager = RaceManager()
    engine = RaceEventEngine()

    config = RaceConfig(race_type="distance", target_value=1000.0)
    manager.configure(config)
    manager.register_node("node-01", "Athlete A")
    manager.start_race()

    progress = manager.update_telemetry({
        "node_id": "node-01",
        "distance_m": 860.0,
        "elapsed_time_ms": 60000,
    })
    events = engine.evaluate(manager, progress)
    assert len([e for e in events if e["event_type"] == "final_sprint"]) == 1

    # Second evaluation should not re-emit
    events = engine.evaluate(manager, progress)
    assert len([e for e in events if e["event_type"] == "final_sprint"]) == 0


def test_event_engine_reset():
    manager = RaceManager()
    engine = RaceEventEngine()

    config = RaceConfig(race_type="distance", target_value=400.0)
    manager.configure(config)
    manager.register_node("node-01", "Runner A")
    manager.start_race()

    progress = manager.update_telemetry({
        "node_id": "node-01",
        "distance_m": 110.0,
        "elapsed_time_ms": 10000,
    })
    engine.evaluate(manager, progress)

    engine.reset()

    # Same progress after reset should re-emit checkpoint
    events = engine.evaluate(manager, progress)
    checkpoint_events = [e for e in events if e["event_type"] == "checkpoint_crossed"]
    assert len(checkpoint_events) == 1
