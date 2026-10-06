"""Final-seconds countdown cues come from the HUB clock, not from whatever
elapsed time the equipment last reported: a runner who stopped (or samples
ignored after the hub deadline) must not silence the 10/5/3/2/1 cues."""

import asyncio
import time

from fastapi.testclient import TestClient

import hub_server.infrastructure.fastapi.app as hub_app
from hub_server.domain.models import RaceConfig, RaceGroup
from hub_server.usecases.race_event_engine import RaceEventEngine
from hub_server.usecases.race_manager import RaceManager

T0 = 1_000_000_000_000
DURATION = 180


def _started(monkeypatch, config=None, nodes=("node-01",)):
    monkeypatch.setattr(time, "time", lambda: T0 / 1000.0)
    manager = RaceManager()
    manager.configure(config or RaceConfig(race_type="time", duration_sec=DURATION))
    for n in nodes:
        manager.register_node(n, n)
    manager.start_race()
    return manager


def _fired(events):
    return [e["data"]["seconds_left"] for e in events if e["event_type"] == "countdown"]


def test_each_threshold_fires_once_as_the_clock_advances_without_telemetry(monkeypatch):
    manager = _started(monkeypatch)
    engine = RaceEventEngine()
    seen = []
    end = T0 + DURATION * 1000
    # 250 ms ticks across the last 12 s, twice over (repeats must not re-fire).
    engine.evaluate_clock(manager, T0)
    for now in range(end - 12_000, end + 1, 250):
        seen += _fired(engine.evaluate_clock(manager, now))
        seen += _fired(engine.evaluate_clock(manager, now))
    assert seen == [10, 5, 3, 2, 1]


def test_cues_fire_on_the_second_not_a_second_early(monkeypatch):
    """Remaining time rounds UP, so threshold N fires when remaining <= N s."""
    manager = _started(monkeypatch)
    end = T0 + DURATION * 1000
    for threshold in (10, 5, 3, 2, 1):
        engine = RaceEventEngine()
        engine.evaluate_clock(manager, T0)
        # Prime just above the mark (cues crossed on the way are not asserted).
        engine.evaluate_clock(manager, end - threshold * 1000 - 900)
        early = end - threshold * 1000 - 500  # 0.5 s before the mark: not yet
        assert _fired(engine.evaluate_clock(manager, early)) == [], threshold
        assert _fired(engine.evaluate_clock(manager, end - threshold * 1000)) == [
            threshold
        ], threshold


def test_a_tick_that_jumps_over_thresholds_fires_each_crossed_one_once(monkeypatch):
    manager = _started(monkeypatch)
    engine = RaceEventEngine()
    end = T0 + DURATION * 1000
    engine.evaluate_clock(manager, T0)
    assert _fired(engine.evaluate_clock(manager, end - 12_000)) == []
    # 2.5 s left rounds UP to 3: the "3" is due, the "2" is not yet.
    assert _fired(engine.evaluate_clock(manager, end - 2_500)) == [10, 5, 3]
    assert _fired(engine.evaluate_clock(manager, end - 2_400)) == []
    assert _fired(engine.evaluate_clock(manager, end - 2_000)) == [2]
    assert _fired(engine.evaluate_clock(manager, end - 500)) == [1]


def test_telemetry_after_a_tick_does_not_fire_the_same_threshold_again(monkeypatch):
    manager = _started(monkeypatch)
    clock = {"now": T0}
    engine = RaceEventEngine(now_ms=lambda: clock["now"])
    end = T0 + DURATION * 1000
    engine.evaluate_clock(manager, T0)
    clock["now"] = end - 10_000
    assert _fired(engine.evaluate_clock(manager, clock["now"])) == [10]

    progress = manager.update_telemetry(
        {"node_id": "node-01", "elapsed_time_ms": 170_000, "distance_m": 900}
    )
    assert _fired(engine.evaluate(manager, progress)) == []


def test_telemetry_path_also_uses_the_hub_clock_not_equipment_elapsed(monkeypatch):
    manager = _started(monkeypatch)
    engine = RaceEventEngine(now_ms=lambda: T0 + 5_000)  # hub: 175 s remain
    progress = manager.update_telemetry(
        {"node_id": "node-01", "elapsed_time_ms": 175_000, "distance_m": 900}
    )
    assert _fired(engine.evaluate(manager, progress)) == []  # equipment says 5 s left


def test_distance_race_is_unaffected(monkeypatch):
    manager = _started(
        monkeypatch, RaceConfig(race_type="distance", target_value=1000.0)
    )
    engine = RaceEventEngine()
    assert engine.evaluate_clock(manager, T0 + 10**9) == []
    progress = manager.update_telemetry({"node_id": "node-01", "distance_m": 860.0})
    events = engine.evaluate(manager, progress)
    assert [e["event_type"] for e in events if e["event_type"] == "final_sprint"] == [
        "final_sprint"
    ]


def test_mixed_race_clock_cues_use_each_groups_own_duration(monkeypatch):
    config = RaceConfig(
        race_type="mixed",
        groups=[
            RaceGroup(equipment_types=["bike-a"], race_type="time", duration_sec=30),
            RaceGroup(equipment_types=["bike-b"], race_type="time", duration_sec=60),
        ],
    )
    manager = _started(monkeypatch, config, nodes=("a-1", "b-1"))
    manager.update_telemetry(
        {"node_id": "a-1", "equipment_type": "bike-a", "distance_m": 1}
    )
    manager.update_telemetry(
        {"node_id": "b-1", "equipment_type": "bike-b", "distance_m": 1}
    )
    engine = RaceEventEngine()
    engine.evaluate_clock(manager, T0)
    # 20 s in: group A (30 s) has 10 s left, group B (60 s) has 40 s left.
    assert _fired(engine.evaluate_clock(manager, T0 + 20_000)) == [10]


# -- the 250 ms tick broadcasts them -------------------------------------------------------


def test_deadline_tick_broadcasts_race_events_exactly_once_with_no_telemetry(
    monkeypatch,
):
    client = TestClient(hub_app.app)
    client.post("/api/race/reset")
    manager = hub_app.race_manager
    manager.update_active_node("cue-node", "treadmill")
    manager.assign_station(1, "cue-node")
    manager.register_athlete(1, "Cue Runner")
    manager.configure(RaceConfig(race_type="time", duration_sec=DURATION))
    manager.start_race()
    hub_app.race_event_engine.reset()
    start = manager.get_start_time_epoch_ms()
    end = start + DURATION * 1000

    sent = []

    async def fake(message):
        sent.append(message)

    monkeypatch.setattr(hub_app.ws_manager, "broadcast", fake)
    try:
        asyncio.run(hub_app.enforce_time_deadline_tick(lambda: start))
        for now in range(end - 11_000, end + 1, 250):
            asyncio.run(hub_app.enforce_time_deadline_tick(lambda now=now: now))
        cues = [
            m["event"]["data"]["seconds_left"]
            for m in sent
            if m.get("type") == "race_event" and m["event"]["event_type"] == "countdown"
        ]
        assert cues == [10, 5, 3, 2, 1]
        assert all(
            set(m) == {"type", "event"} for m in sent if m.get("type") == "race_event"
        )
        assert any(
            m.get("type") == "state_change" for m in sent
        )  # deadline still stops it
    finally:
        client.post("/api/race/reset")
