"""A2: challenge mode -- auto countdown/start when every assigned station is
registered, auto reset after a delay, re-applying the timed config.

Decision logic lives in the usecase (fake clock, no sleeping); the app only
schedules and calls.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import hub_server.infrastructure.fastapi.app as hub_app
from hub_server.domain.models import RaceConfig, RaceState
from hub_server.usecases.challenge_mode import next_challenge_action
from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.race_settings_store import RaceSettingsStore


def _decide(**overrides):
    inputs = dict(
        enabled=True,
        state=RaceState.READY,
        session_mode="race",
        assigned_stations=[1],
        registered_stations=[1],
        countdown_active=False,
        pending_signups=0,
    )
    inputs.update(overrides)
    return next_challenge_action(**inputs)


# -- decision logic --------------------------------------------------------


def test_disabled_never_acts():
    for state in RaceState:
        assert _decide(enabled=False, state=state, pending_signups=5) is None


def test_ready_with_every_assigned_station_registered_starts():
    assert _decide(assigned_stations=[1, 2], registered_stations=[2, 1]) == "start"


def test_ready_waits_while_any_assigned_station_is_unregistered():
    assert _decide(assigned_stations=[1, 2], registered_stations=[1]) is None


def test_ready_with_no_assigned_stations_does_nothing():
    assert _decide(assigned_stations=[], registered_stations=[]) is None


def test_ready_does_not_start_during_an_active_countdown():
    assert _decide(countdown_active=True) is None


def test_stopped_keeps_the_result_screen_until_someone_signs_up():
    assert _decide(state=RaceState.STOPPED, pending_signups=0) is None


def test_stopped_resets_as_soon_as_a_new_signup_is_waiting():
    assert _decide(state=RaceState.STOPPED, pending_signups=1) == "reset"


def test_idle_reapplies_the_challenge_config():
    assert _decide(state=RaceState.IDLE) == "configure"


def test_running_is_left_alone():
    assert _decide(state=RaceState.RUNNING) is None


def test_class_session_is_never_touched():
    for state in (RaceState.IDLE, RaceState.READY, RaceState.STOPPED):
        assert _decide(state=state, session_mode="class", pending_signups=3) is None


# -- persisted settings ------------------------------------------------------


def test_challenge_settings_default_off_180():
    manager = RaceManager()
    assert manager.get_challenge_settings() == {
        "challenge_mode_enabled": False,
        "challenge_duration_sec": 180,
    }


def test_challenge_settings_persist_across_restart(tmp_path):
    path = tmp_path / "settings.json"
    manager = RaceManager(settings_store=RaceSettingsStore(path))
    manager.set_challenge_settings(True, 120)

    restored = RaceManager(settings_store=RaceSettingsStore(path))

    assert restored.get_challenge_settings() == {
        "challenge_mode_enabled": True,
        "challenge_duration_sec": 120,
    }


def test_challenge_settings_are_in_the_state_snapshot():
    manager = RaceManager()
    manager.set_challenge_settings(True, 90)
    snap = manager.get_state_snapshot()
    assert snap["challenge_mode_enabled"] is True
    assert snap["challenge_duration_sec"] == 90
    assert "challenge_reset_delay_sec" not in snap


def test_challenge_settings_cannot_change_while_running():
    manager = RaceManager()
    manager.register_node("node-01", "A")
    manager.configure(RaceConfig(race_type="time", duration_sec=60))
    manager.start_race()
    with pytest.raises(ValueError):
        manager.set_challenge_settings(True, 180)


@pytest.mark.parametrize("duration", [0, -1, 9, 99999])
def test_challenge_settings_reject_out_of_range_values(duration):
    with pytest.raises(ValueError):
        RaceManager().set_challenge_settings(True, duration)


# -- API + scheduler wiring ----------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    c = TestClient(hub_app.app)
    c.post("/api/race/reset")
    for sn in list(hub_app.race_manager.get_stations_status()["stations"]):
        hub_app.race_manager.assign_station(sn, None)
    monkeypatch.setattr(hub_app, "RACE_START_COUNTDOWN_DURATION_MS", 0)
    monkeypatch.setattr(hub_app, "enforce_race_readiness", lambda: None)
    yield c
    hub_app.race_manager.reset_race()
    hub_app.race_manager.set_challenge_settings(False, 180)
    for sn in list(hub_app.race_manager.get_stations_status()["stations"]):
        hub_app.race_manager.assign_station(sn, None)


def _assign_and_register(client, stations=(1,)):
    for n in stations:
        client.post(
            "/api/stations/assign", json={"station_number": n, "node_id": f"ch-{n}"}
        )
        client.post(
            "/api/race/register", json={"station_number": n, "athlete_name": f"P{n}"}
        )


def test_enabling_applies_the_timed_config_immediately(client):
    res = client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 120},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "READY"
    assert body["config"]["race_type"] == "time"
    assert body["config"]["duration_sec"] == 120
    assert body["challenge_mode_enabled"] is True


def test_changing_duration_while_ready_reapplies_config(client):
    client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 120},
    )
    res = client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 90},
    )
    assert res.json()["config"]["duration_sec"] == 90


def test_challenge_endpoint_blocked_while_running(client):
    client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 120},
    )
    _assign_and_register(client)
    client.post("/api/race/start")
    assert hub_app.race_manager.get_state() == RaceState.RUNNING

    res = client.post(
        "/api/race/challenge",
        json={"enabled": False, "duration_sec": 120},
    )

    assert res.status_code == 400
    assert hub_app.race_manager.get_challenge_settings()["challenge_mode_enabled"]


def test_challenge_endpoint_requires_admin_token(client, monkeypatch):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "s3cret")
    res = client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 120},
    )
    assert res.status_code == 401


def test_challenge_endpoint_rejects_bad_payload(client):
    res = client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 0},
    )
    assert res.status_code == 422


def test_tick_starts_the_race_once_every_station_is_registered(client):
    client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 120},
    )
    client.post("/api/stations/assign", json={"station_number": 1, "node_id": "ch-1"})
    client.post("/api/stations/assign", json={"station_number": 2, "node_id": "ch-2"})
    client.post("/api/race/register", json={"station_number": 1, "athlete_name": "P1"})

    assert asyncio.run(hub_app.challenge_tick()) is None
    assert hub_app.race_manager.get_state() == RaceState.READY

    client.post("/api/race/register", json={"station_number": 2, "athlete_name": "P2"})
    assert asyncio.run(hub_app.challenge_tick()) == "start"
    assert hub_app.race_manager.get_state() == RaceState.RUNNING


def _finish_a_challenge_race(client):
    client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 120},
    )
    _assign_and_register(client)
    asyncio.run(hub_app.challenge_tick())
    assert hub_app.race_manager.get_state() == RaceState.RUNNING
    hub_app.race_manager.stop_race()


def test_result_screen_stays_until_a_new_signup_arrives(client):
    _finish_a_challenge_race(client)

    for _ in range(3):
        assert asyncio.run(hub_app.challenge_tick()) is None
    assert hub_app.race_manager.get_state() == RaceState.STOPPED


def test_lan_signup_during_stopped_resets_registers_and_starts_next_run(client):
    _finish_a_challenge_race(client)

    res = client.post(
        "/api/race/register", json={"station_number": 1, "athlete_name": "Next"}
    )
    assert res.status_code == 200
    assert hub_app.race_manager.get_state() == RaceState.STOPPED  # queued only

    assert asyncio.run(hub_app.challenge_tick()) == "reset"
    manager = hub_app.race_manager
    assert manager.get_state() == RaceState.READY
    assert manager.get_config().race_type == "time"
    assert manager.get_config().duration_sec == 120
    status = manager.get_stations_status()["stations"][1]
    assert status["node_id"] == "ch-1"
    assert status["athlete_name"] == "Next" and status["registered"] is True

    assert asyncio.run(hub_app.challenge_tick()) == "start"
    assert manager.get_state() == RaceState.RUNNING


def test_cloud_signup_waiting_in_queue_triggers_the_reset(client, monkeypatch):
    from types import SimpleNamespace

    _finish_a_challenge_race(client)
    monkeypatch.setattr(
        hub_app, "cloud_signup_processor", SimpleNamespace(queue_length=1)
    )

    assert asyncio.run(hub_app.challenge_tick()) == "reset"
    assert hub_app.race_manager.get_state() == RaceState.READY


def test_stopped_registration_is_still_rejected_without_challenge_mode(client):
    client.post("/api/race/configure", json={"race_type": "time", "duration_sec": 60})
    _assign_and_register(client)
    client.post("/api/race/start")
    hub_app.race_manager.stop_race()

    res = client.post(
        "/api/race/register", json={"station_number": 1, "athlete_name": "Late"}
    )

    assert res.status_code == 400


def test_tick_is_inert_when_challenge_mode_is_off(client):
    _assign_and_register(client)
    client.post(
        "/api/race/configure",
        json={"race_type": "time", "duration_sec": 60},
    )

    assert asyncio.run(hub_app.challenge_tick()) is None
    assert hub_app.race_manager.get_state() == RaceState.READY


def test_tick_does_not_start_while_a_countdown_is_active(client):
    client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 120},
    )
    _assign_and_register(client)

    async def scenario():
        async with hub_app.race_start_countdown_lock:
            return await hub_app.challenge_tick()

    assert asyncio.run(scenario()) is None
    assert hub_app.race_manager.get_state() == RaceState.READY


def test_tick_waits_when_readiness_check_fails(client, monkeypatch):
    from fastapi import HTTPException

    def not_ready():
        raise HTTPException(status_code=409, detail="edge offline")

    client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 120},
    )
    _assign_and_register(client)
    monkeypatch.setattr(hub_app, "enforce_race_readiness", not_ready)

    assert asyncio.run(hub_app.challenge_tick()) is None
    assert hub_app.race_manager.get_state() == RaceState.READY


def test_countdown_endpoint_still_works_and_shares_the_countdown(client):
    client.post("/api/race/configure", json={"race_type": "time", "duration_sec": 60})
    _assign_and_register(client)

    res = client.post("/api/race/countdown-start")

    assert res.status_code == 200
    assert res.json()["state"] == "RUNNING"
