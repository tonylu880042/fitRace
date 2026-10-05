"""A1: a timed race must end on the HUB's clock.

Before this, RaceManager only stopped a time race once every participant's
own elapsed_time_ms reached duration -- and elapsed prefers the equipment's
reported value, so a stalled or desynced machine kept the race RUNNING
forever. enforce_time_deadline() is the hub-side backstop.
"""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from hub_server.domain.models import RaceConfig, RaceState
from hub_server.usecases.race_manager import RaceManager

START_MS = 1_000_000_000_000
DURATION_SEC = 180
DEADLINE_MS = START_MS + DURATION_SEC * 1000


def _running_time_race(monkeypatch, race_type="time", stations=2):
    monkeypatch.setattr(time, "time", lambda: START_MS / 1000.0)
    manager = RaceManager()
    for n in range(1, stations + 1):
        node = f"node-0{n}"
        manager.update_active_node(node, "treadmill")
        manager.assign_station(n, node)
        manager.register_athlete(n, f"Runner {n}")
    manager.configure(RaceConfig(race_type=race_type, duration_sec=DURATION_SEC))
    manager.start_race()
    assert manager.get_start_time_epoch_ms() == START_MS
    return manager


def _telemetry(node, distance, elapsed_ms):
    return {
        "node_id": node,
        "equipment_type": "treadmill",
        "distance_m": distance,
        "elapsed_time_ms": elapsed_ms,
        "instantaneous_speed_kph": 10.0,
    }


def test_deadline_not_reached_keeps_race_running(monkeypatch):
    manager = _running_time_race(monkeypatch)
    manager.ingest_telemetry(_telemetry("node-01", 100.0, 60_000))

    assert manager.enforce_time_deadline(DEADLINE_MS - 1) is False
    assert manager.get_state() == RaceState.RUNNING


def test_deadline_stops_race_even_when_a_machine_stalled(monkeypatch):
    manager = _running_time_race(monkeypatch)
    manager.ingest_telemetry(_telemetry("node-01", 400.0, 120_000))
    # node-02 stopped sending early: its own elapsed never reaches 180s, so
    # the old all-participants-finished rule would never fire.
    manager.ingest_telemetry(_telemetry("node-02", 90.0, 30_000))

    assert manager.enforce_time_deadline(DEADLINE_MS) is True

    assert manager.get_state() == RaceState.STOPPED
    assert manager.get_end_time_epoch_ms() == DEADLINE_MS
    board = manager.get_leaderboard_progress()
    # Distance keeps its last pre-deadline value; nothing is invented.
    assert board["node-01"]["distance_m"] == 400.0
    assert board["node-02"]["distance_m"] == 90.0


def test_deadline_caps_elapsed_and_progress(monkeypatch):
    manager = _running_time_race(monkeypatch, stations=2)
    # Equipment clock already ahead of the hub's: 181s reported.
    manager.ingest_telemetry(_telemetry("node-01", 500.0, 181_000))

    assert manager.enforce_time_deadline(DEADLINE_MS) is True

    row = manager.get_leaderboard_progress()["node-01"]
    assert row["elapsed_time_ms"] == DURATION_SEC * 1000
    assert row["progress_percent"] == 100.0


def test_progress_never_exceeds_100_for_overlong_equipment_elapsed(monkeypatch):
    manager = _running_time_race(monkeypatch, stations=2)

    progress = manager.ingest_telemetry(_telemetry("node-01", 500.0, 181_000))

    assert progress["node-01"]["progress_percent"] == 100.0


def test_telemetry_after_deadline_does_not_change_results(monkeypatch):
    manager = _running_time_race(monkeypatch)
    manager.ingest_telemetry(_telemetry("node-01", 400.0, 120_000))
    manager.ingest_telemetry(_telemetry("node-02", 90.0, 30_000))
    manager.enforce_time_deadline(DEADLINE_MS)
    before = {k: dict(v) for k, v in manager.get_leaderboard_progress().items()}

    manager.ingest_telemetry(_telemetry("node-01", 999.0, 170_000))
    manager.update_telemetry(_telemetry("node-02", 999.0, 170_000))

    assert manager.get_leaderboard_progress() == before


def test_enforce_is_a_noop_unless_running(monkeypatch):
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="time", duration_sec=DURATION_SEC))

    assert manager.enforce_time_deadline(DEADLINE_MS * 2) is False
    assert manager.get_state() == RaceState.READY


def test_enforce_second_call_after_stop_returns_false(monkeypatch):
    manager = _running_time_race(monkeypatch)
    assert manager.enforce_time_deadline(DEADLINE_MS) is True
    assert manager.enforce_time_deadline(DEADLINE_MS + 5000) is False
    assert manager.get_end_time_epoch_ms() == DEADLINE_MS


@pytest.mark.parametrize("race_type", ["max_power", "watts"])
def test_other_duration_race_types_also_have_a_deadline(monkeypatch, race_type):
    manager = _running_time_race(monkeypatch, race_type=race_type)
    assert manager.enforce_time_deadline(DEADLINE_MS) is True
    assert manager.get_state() == RaceState.STOPPED


def test_distance_race_has_no_time_deadline(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: START_MS / 1000.0)
    manager = RaceManager()
    manager.register_node("node-01", "Runner")
    manager.configure(RaceConfig(race_type="distance", target_value=500.0))
    manager.start_race()

    assert manager.enforce_time_deadline(START_MS + 10_000_000) is False
    assert manager.get_state() == RaceState.RUNNING


def test_mixed_race_is_not_touched_by_the_deadline(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: START_MS / 1000.0)
    manager = RaceManager()
    manager.register_node("node-01", "Runner")
    manager.configure(
        RaceConfig(
            race_type="mixed",
            groups=[
                {
                    "equipment_types": ["treadmill"],
                    "race_type": "time",
                    "duration_sec": DURATION_SEC,
                },
                {
                    "equipment_types": ["rower"],
                    "race_type": "distance",
                    "target_value": 500,
                },
            ],
        )
    )
    manager.start_race()

    assert manager.enforce_time_deadline(START_MS + 10_000_000) is False
    assert manager.get_state() == RaceState.RUNNING


def test_deadline_tick_stops_broadcasts_and_saves_results(monkeypatch):
    """Infrastructure wiring: the tick uses an injected clock (no sleeping),
    and a deadline stop goes through broadcast_race_state() so the finished
    snapshot is persisted."""
    import hub_server.infrastructure.fastapi.app as hub_app

    client = TestClient(hub_app.app)
    client.post("/api/race/reset")
    manager = hub_app.race_manager
    manager.update_active_node("deadline-node", "treadmill")
    manager.assign_station(1, "deadline-node")
    manager.register_athlete(1, "Deadline Runner")
    manager.configure(RaceConfig(race_type="time", duration_sec=DURATION_SEC))
    manager.start_race()
    start_ms = manager.get_start_time_epoch_ms()
    manager.ingest_telemetry(_telemetry("deadline-node", 250.0, 20_000))

    try:
        early = asyncio.run(
            hub_app.enforce_time_deadline_tick(
                lambda: start_ms + DURATION_SEC * 1000 - 1
            )
        )
        assert early is False
        assert manager.get_state() == RaceState.RUNNING

        late = asyncio.run(
            hub_app.enforce_time_deadline_tick(lambda: start_ms + DURATION_SEC * 1000)
        )
        assert late is True
        assert manager.get_state() == RaceState.STOPPED
        saved = hub_app.race_result_store.list_results()
        assert len(saved) == 1
        assert saved[0]["snapshot"]["state"] == "STOPPED"
    finally:
        client.post("/api/race/reset")
