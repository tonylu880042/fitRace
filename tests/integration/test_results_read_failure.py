"""The stop endpoint must survive a results-file read failure."""

from fastapi.testclient import TestClient

from hub_server.domain.models import RaceState
from hub_server.infrastructure.fastapi import app as hub_app
from hub_server.infrastructure.fastapi.app import app
from hub_server.usecases.race_result_store import RaceResultStore


class _RunningRace:
    def __init__(self):
        self._state = RaceState.RUNNING
        self._end_time_epoch_ms = None

    def stop_race(self):
        self._state = RaceState.STOPPED
        self._end_time_epoch_ms = 2000

    def get_state_snapshot(self):
        return {
            "state": self._state.value,
            "session_mode": "race",
            "config": {
                "race_type": "distance",
                "target_value": 100,
                "duration_sec": 0,
            },
            "start_time_epoch_ms": 1000,
            "end_time_epoch_ms": self._end_time_epoch_ms,
            "leaderboard": {},
            "team_leaderboard": [],
        }

    def get_stations_status(self):
        return {"stations": {}}

    def get_challenge_settings(self):
        return {
            "challenge_mode_enabled": False,
            "challenge_min_result_sec": 10,
            "challenge_start_wait_sec": 30,
        }

    def get_session_mode(self):
        return "race"

    def get_state(self):
        return self._state

    def get_end_time_epoch_ms(self):
        return self._end_time_epoch_ms

    def get_first_signup_epoch_ms(self):
        return None


def test_stop_returns_stopped_state_when_results_read_fails(monkeypatch, tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    class_store = RaceResultStore(
        tmp_path / "class_results.jsonl", session_mode="class"
    )
    race_manager = _RunningRace()
    read_attempts = []

    def fail_key_read(result_key):
        read_attempts.append(result_key)
        raise PermissionError("results file cannot be read")

    monkeypatch.setattr(hub_app, "race_manager", race_manager)
    monkeypatch.setattr(hub_app, "race_result_store", store)
    monkeypatch.setattr(hub_app, "class_result_store", class_store)
    monkeypatch.setattr(store, "_key_exists", fail_key_read)
    monkeypatch.setattr(hub_app, "lan_signup_url", lambda: None)
    broadcasts = []

    async def capture_broadcast(payload):
        broadcasts.append(payload)

    monkeypatch.setattr(hub_app.ws_manager, "broadcast", capture_broadcast)

    response = TestClient(app).post("/api/race/stop")

    assert response.status_code == 200
    assert response.json()["state"] == "STOPPED"
    assert race_manager.get_state() == RaceState.STOPPED
    assert len(read_attempts) == 2
    assert len(broadcasts) == 1
    assert broadcasts[0]["type"] == "state_change"
    assert broadcasts[0]["state"] == "STOPPED"


def test_stop_persists_result_when_results_file_has_a_torn_utf8_line(
    monkeypatch, tmp_path
):
    results_path = tmp_path / "race_results.jsonl"
    torn = '{"result_id":"x","snapshot":{"n":"'.encode() + "王".encode()[:2] + b"\n"
    results_path.write_bytes(b'{"result_id":"old","snapshot":{}}\n' + torn)
    store = RaceResultStore(results_path)
    class_store = RaceResultStore(
        tmp_path / "class_results.jsonl", session_mode="class"
    )
    race_manager = _RunningRace()

    monkeypatch.setattr(hub_app, "race_manager", race_manager)
    monkeypatch.setattr(hub_app, "race_result_store", store)
    monkeypatch.setattr(hub_app, "class_result_store", class_store)
    monkeypatch.setattr(hub_app, "lan_signup_url", lambda: None)

    async def ignore_broadcast(payload):
        return None

    monkeypatch.setattr(hub_app.ws_manager, "broadcast", ignore_broadcast)

    response = TestClient(app).post("/api/race/stop")

    assert response.status_code == 200
    assert response.json()["state"] == "STOPPED"
    ids = [r["result_id"] for r in store.list_results(limit=None)]
    assert ids == ["old", "1000-2000-distance"]
