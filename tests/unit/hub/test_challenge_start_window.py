"""R6: with several stations, the first sign-up opens a wait window
(challenge_start_wait_sec); the run starts when every assigned station is
registered or the window ends. Stations nobody signed up on do not take part."""

import asyncio

import pytest
from fastapi.testclient import TestClient

import hub_server.infrastructure.fastapi.app as hub_app
from hub_server.domain.models import RaceConfig, RaceState
from hub_server.usecases.challenge_mode import challenge_start_at, next_challenge_action
from hub_server.usecases.race_manager import RaceManager

T0 = 1_000_000
WAIT = 30_000


def _decide(**overrides):
    args = dict(
        enabled=True,
        state=RaceState.READY,
        session_mode="race",
        assigned_stations=[1, 2],
        registered_stations=[1],
        countdown_active=False,
        pending_signups=0,
        now_epoch_ms=T0,
        end_time_epoch_ms=None,
        min_result_ms=10_000,
        first_signup_epoch_ms=T0,
        start_wait_ms=WAIT,
    )
    args.update(overrides)
    return next_challenge_action(**args)


# -- pure decision ---------------------------------------------------------------------


def test_partial_signups_start_only_when_the_window_ends():
    assert _decide(now_epoch_ms=T0) is None
    assert _decide(now_epoch_ms=T0 + WAIT - 1) is None
    assert _decide(now_epoch_ms=T0 + WAIT) == "start"


def test_window_never_starts_an_empty_run():
    assert _decide(registered_stations=[], now_epoch_ms=T0 + 10 * WAIT) is None


def test_registration_on_an_unassigned_station_does_not_count():
    assert _decide(registered_stations=[9], now_epoch_ms=T0 + 10 * WAIT) is None


def test_everyone_registered_starts_at_once_without_waiting():
    assert _decide(registered_stations=[1, 2], now_epoch_ms=T0) == "start"


def test_single_station_one_signup_starts_immediately():
    assert (
        _decide(
            assigned_stations=[1],
            registered_stations=[1],
            first_signup_epoch_ms=T0,
            now_epoch_ms=T0,
        )
        == "start"
    )


def test_zero_wait_starts_as_soon_as_one_is_registered():
    assert _decide(start_wait_ms=0, now_epoch_ms=T0) == "start"


def test_missing_first_signup_time_never_times_out():
    assert _decide(first_signup_epoch_ms=None, now_epoch_ms=T0 + 10 * WAIT) is None


def test_start_at_is_published_only_while_waiting():
    base = dict(
        enabled=True,
        state=RaceState.READY,
        assigned_stations=[1, 2],
        registered_stations=[1],
        first_signup_epoch_ms=T0,
        start_wait_ms=WAIT,
    )
    assert challenge_start_at(**base) == T0 + WAIT
    assert challenge_start_at(**{**base, "registered_stations": [1, 2]}) is None
    assert challenge_start_at(**{**base, "registered_stations": []}) is None
    assert challenge_start_at(**{**base, "first_signup_epoch_ms": None}) is None
    assert challenge_start_at(**{**base, "enabled": False}) is None
    assert challenge_start_at(**{**base, "state": RaceState.RUNNING}) is None
    assert challenge_start_at(**{**base, "assigned_stations": [1]}) is None


# -- manager ------------------------------------------------------------------------------


def test_first_signup_time_is_recorded_once_and_cleared_by_reset():
    clock = {"now": 5_000}
    m = RaceManager(now_ms=lambda: clock["now"])
    m.configure(RaceConfig(race_type="time", duration_sec=60))
    assert m.get_first_signup_epoch_ms() is None
    m.register_athlete(1, "A")
    clock["now"] = 9_000
    m.register_athlete(2, "B")
    assert m.get_first_signup_epoch_ms() == 5_000
    m.reset_race()
    assert m.get_first_signup_epoch_ms() is None


def test_start_wait_setting_defaults_persists_and_validates(tmp_path):
    from hub_server.usecases.race_settings_store import RaceSettingsStore

    path = tmp_path / "s.json"
    m = RaceManager(settings_store=RaceSettingsStore(path))
    assert m.get_challenge_settings()["challenge_start_wait_sec"] == 30
    m.set_challenge_settings(True, 180, 10, 45)
    again = RaceManager(settings_store=RaceSettingsStore(path))
    assert again.get_challenge_settings()["challenge_start_wait_sec"] == 45
    assert again.get_state_snapshot()["challenge_start_wait_sec"] == 45
    for bad in (-1, 301):
        with pytest.raises(ValueError):
            m.set_challenge_settings(True, 180, 10, bad)
    m.set_challenge_settings(True, 180, 10, 0)
    m.set_challenge_settings(True, 180, 10, 300)


def _two_station_manager(challenge):
    m = RaceManager()
    for n in (1, 2):
        m.update_active_node(f"node-0{n}", "treadmill")
        m.assign_station(n, f"node-0{n}")
    m.set_challenge_settings(challenge, 60)
    m.register_athlete(1, "Runner 1")
    m.configure(RaceConfig(race_type="time", duration_sec=60))
    m.start_race()
    return m


def _sample(node, dist):
    return {
        "node_id": node,
        "equipment_type": "treadmill",
        "distance_m": dist,
        "elapsed_time_ms": 5000,
    }


def test_challenge_run_ignores_stations_nobody_signed_up_on():
    m = _two_station_manager(challenge=True)
    m.ingest_telemetry(_sample("node-01", 100.0))
    m.ingest_telemetry(_sample("node-02", 103.0))

    board = m.get_leaderboard_progress()
    assert set(board) == {"node-01"}
    assert set(m.get_state_snapshot()["leaderboard"]) == {"node-01"}


def test_without_challenge_mode_unregistered_stations_still_appear():
    m = _two_station_manager(challenge=False)
    m.ingest_telemetry(_sample("node-02", 103.0))
    assert "node-02" in m.get_leaderboard_progress()


def test_exclusion_is_released_by_reset():
    m = _two_station_manager(challenge=True)
    m.stop_race()
    m.reset_race()
    m.configure(RaceConfig(race_type="time", duration_sec=60))
    m.register_athlete(1, "A")
    m.register_athlete(2, "B")
    m.start_race()
    m.ingest_telemetry(_sample("node-02", 5.0))
    assert "node-02" in m.get_leaderboard_progress()


# -- scheduler wiring ---------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    c = TestClient(hub_app.app)
    c.post("/api/race/reset")
    for sn in list(hub_app.race_manager.get_stations_status()["stations"]):
        hub_app.race_manager.assign_station(sn, None)
    monkeypatch.setattr(hub_app, "RACE_START_COUNTDOWN_DURATION_MS", 0)
    monkeypatch.setattr(hub_app, "enforce_race_readiness", lambda: None)
    monkeypatch.setattr(hub_app, "_last_broadcast_challenge_view", None)
    yield c
    hub_app.race_manager.reset_race()
    hub_app.race_manager.set_challenge_settings(False, 180)
    for sn in list(hub_app.race_manager.get_stations_status()["stations"]):
        hub_app.race_manager.assign_station(sn, None)


def _enable(client, stations, start_wait=30):
    client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 120, "start_wait_sec": start_wait},
    )
    for n in stations:
        client.post(
            "/api/stations/assign", json={"station_number": n, "node_id": f"sw-{n}"}
        )


def _signup(client, n):
    client.post(
        "/api/race/register", json={"station_number": n, "athlete_name": f"P{n}"}
    )


def test_single_station_one_signup_starts_immediately_with_no_wait(client):
    _enable(client, [1])
    _signup(client, 1)
    assert asyncio.run(hub_app.challenge_tick()) == "start"
    assert hub_app.race_manager.get_state() == RaceState.RUNNING
    assert client.get("/api/race/state").json()["challenge_start_at_epoch_ms"] is None


def test_two_stations_wait_for_the_window_then_start_without_the_empty_one(client):
    _enable(client, [1, 2])
    _signup(client, 1)
    first = hub_app.race_manager.get_first_signup_epoch_ms()

    assert asyncio.run(hub_app.challenge_tick(lambda: first + 29_999)) is None
    state = client.get("/api/race/state").json()
    assert state["challenge_start_at_epoch_ms"] == first + 30_000
    assert hub_app.race_manager.get_state() == RaceState.READY

    assert asyncio.run(hub_app.challenge_tick(lambda: first + 30_000)) == "start"
    assert hub_app.race_manager.get_state() == RaceState.RUNNING
    hub_app.race_manager.ingest_telemetry(_sample("sw-2", 103.0))
    assert set(hub_app.race_manager.get_leaderboard_progress()) <= {"sw-1"}


def test_second_signup_inside_the_window_starts_the_run_at_once(client):
    _enable(client, [1, 2])
    _signup(client, 1)
    first = hub_app.race_manager.get_first_signup_epoch_ms()
    assert asyncio.run(hub_app.challenge_tick(lambda: first + 1)) is None
    _signup(client, 2)
    assert asyncio.run(hub_app.challenge_tick(lambda: first + 2)) == "start"


def test_state_change_is_broadcast_once_when_the_window_opens(client, monkeypatch):
    _enable(client, [1, 2])
    asyncio.run(hub_app.broadcast_race_state())
    sent = []

    async def fake(message):
        if message.get("type") == "state_change":
            sent.append(message)

    monkeypatch.setattr(hub_app.ws_manager, "broadcast", fake)
    _signup(client, 1)
    first = hub_app.race_manager.get_first_signup_epoch_ms()
    asyncio.run(hub_app.challenge_tick(lambda: first + 1))
    asyncio.run(hub_app.challenge_tick(lambda: first + 500))
    asyncio.run(hub_app.challenge_tick(lambda: first + 1000))
    published = [m for m in sent if m.get("challenge_start_at_epoch_ms")]
    assert len(sent) == 1 and len(published) == 1


def test_endpoint_accepts_and_validates_start_wait(client):
    ok = client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 120, "start_wait_sec": 45},
    )
    assert ok.json()["challenge_start_wait_sec"] == 45
    bad = client.post(
        "/api/race/challenge",
        json={"enabled": True, "duration_sec": 120, "start_wait_sec": 301},
    )
    assert bad.status_code == 422
