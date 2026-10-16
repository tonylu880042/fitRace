"""HTTP surface for the relay team library and the /teams race flow:
GET/POST/PUT/DELETE /api/relay-teams, POST /api/relay-teams/assign and
POST /api/race/next-race (see docs/relay_teams_page_spec.md)."""

import pytest
from fastapi.testclient import TestClient

from hub_server.domain.models import RaceState
from hub_server.infrastructure.fastapi import app as app_module
from hub_server.usecases.race_settings_store import RaceSettingsStore
from hub_server.usecases.relay_teams import RelayTeamLibrary

client = TestClient(app_module.app)


@pytest.fixture(autouse=True)
def isolated_library_and_race(monkeypatch, tmp_path):
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    monkeypatch.setattr(
        app_module,
        "relay_team_library",
        RelayTeamLibrary(RaceSettingsStore(tmp_path / "relay_teams.json")),
    )

    async def ignore_broadcast(payload):
        return None

    monkeypatch.setattr(app_module.ws_manager, "broadcast", ignore_broadcast)
    _reset_all()
    yield
    _reset_all()


def _reset_all():
    client.post("/api/race/reset")
    for station in (1, 2, 3):
        app_module.race_manager.assign_station(station, None)


def _stations(*numbers):
    for n in numbers:
        app_module.race_manager.assign_station(n, f"node-{n}")


def _configure(mode="relay", legs=2):
    body = {"race_type": "distance", "target_value": 500, "competition_mode": mode}
    if mode == "relay":
        body["relay_legs"] = legs
    assert client.post("/api/race/configure", json=body).status_code == 200


def _team(name, members):
    res = client.post("/api/relay-teams", json={"name": name, "members": members})
    assert res.status_code == 200, res.text
    return res.json()


def _registered():
    stations = client.get("/api/stations").json()["stations"]
    return {
        n: (s.get("team_name"), s.get("relay_members"))
        for n, s in stations.items()
        if s.get("registered")
    }


def _state():
    return client.get("/api/race/state").json()["state"]


# ---- library CRUD ----


def test_library_starts_empty():
    res = client.get("/api/relay-teams")
    assert res.status_code == 200
    assert res.json() == {"teams": []}


def test_create_update_delete_roundtrip():
    volt = _team("Volt", ["Alice", "Bob"])
    assert volt["name"] == "Volt" and volt["members"] == ["Alice", "Bob"]
    _team("Amp", ["Cara", "Dan"])

    res = client.put(
        f"/api/relay-teams/{volt['id']}",
        json={"name": "Volt 2", "members": ["Zed", "Yan"]},
    )
    assert res.status_code == 200
    assert res.json()["id"] == volt["id"] and res.json()["name"] == "Volt 2"

    listing = client.get("/api/relay-teams").json()["teams"]
    assert [t["name"] for t in listing] == ["Volt 2", "Amp"]

    res = client.delete(f"/api/relay-teams/{volt['id']}")
    assert res.status_code == 200
    assert [t["name"] for t in res.json()["teams"]] == ["Amp"]


def test_validation_errors_are_400_and_unknown_id_is_404():
    _team("Volt", ["Alice"])
    dup = client.post("/api/relay-teams", json={"name": "VOLT", "members": ["B"]})
    assert dup.status_code == 400
    blank = client.post("/api/relay-teams", json={"name": " ", "members": ["B"]})
    assert blank.status_code == 400
    assert (
        client.put(
            "/api/relay-teams/nope", json={"name": "X", "members": ["a"]}
        ).status_code
        == 404
    )
    assert client.delete("/api/relay-teams/nope").status_code == 404
    assert len(client.get("/api/relay-teams").json()["teams"]) == 1


def test_every_relay_team_endpoint_requires_admin_token(monkeypatch):
    team = _team("Volt", ["Alice"])
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "s3cret")
    calls = [
        client.get("/api/relay-teams"),
        client.post("/api/relay-teams", json={"name": "A", "members": ["x"]}),
        client.put(
            f"/api/relay-teams/{team['id']}", json={"name": "A", "members": ["x"]}
        ),
        client.delete(f"/api/relay-teams/{team['id']}"),
        client.post("/api/relay-teams/assign", json={"assignments": {}}),
        client.post("/api/race/next-race"),
    ]
    assert [c.status_code for c in calls] == [401] * 6
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN")
    assert len(client.get("/api/relay-teams").json()["teams"]) == 1


# ---- assign ----


def test_assign_registers_teams_on_stations_and_leaves_race_ready():
    _stations(1, 2, 3)
    _configure()
    volt = _team("Volt", ["Alice", "Bob"])
    amp = _team("Amp", ["Cara", "Dan"])

    res = client.post(
        "/api/relay-teams/assign",
        json={"assignments": {"1": amp["id"], "2": volt["id"], "3": None}},
    )

    assert res.status_code == 200
    assert res.json()["stations"]["1"]["team_name"] == "Amp"
    assert _registered() == {
        "1": ("Amp", ["Cara", "Dan"]),
        "2": ("Volt", ["Alice", "Bob"]),
    }
    assert _state() == "READY"


def test_assign_replaces_earlier_assignment():
    _stations(1, 2)
    _configure()
    volt = _team("Volt", ["Alice", "Bob"])
    amp = _team("Amp", ["Cara", "Dan"])
    client.post(
        "/api/relay-teams/assign",
        json={"assignments": {"1": volt["id"], "2": amp["id"]}},
    )

    res = client.post(
        "/api/relay-teams/assign", json={"assignments": {"2": volt["id"]}}
    )

    assert res.status_code == 200
    assert _registered() == {"2": ("Volt", ["Alice", "Bob"])}


def test_assign_from_idle_with_saved_config_ends_ready():
    """After a hub restart the saved config sits in an IDLE race; assign must
    re-apply it so the race ends READY (same reason as load_next_heat)."""
    from hub_server.domain.models import RaceConfig

    _stations(1)
    app_module.race_manager._config = RaceConfig(
        race_type="distance", competition_mode="relay", relay_legs=2, target_value=500
    )
    assert app_module.race_manager.get_state() == RaceState.IDLE
    volt = _team("Volt", ["Alice", "Bob"])

    res = client.post(
        "/api/relay-teams/assign", json={"assignments": {"1": volt["id"]}}
    )

    assert res.status_code == 200
    assert _state() == "READY"
    assert _registered() == {"1": ("Volt", ["Alice", "Bob"])}


def test_assign_blocked_when_running_or_stopped_and_changes_nothing():
    _stations(1, 2)
    _configure()
    volt = _team("Volt", ["Alice", "Bob"])
    amp = _team("Amp", ["Cara", "Dan"])
    client.post("/api/relay-teams/assign", json={"assignments": {"1": volt["id"]}})
    before = _registered()

    app_module.race_manager.start_race()
    res = client.post("/api/relay-teams/assign", json={"assignments": {"2": amp["id"]}})
    assert res.status_code == 409
    assert _state() == "RUNNING"
    assert _registered() == before

    app_module.race_manager.stop_race()
    res = client.post("/api/relay-teams/assign", json={"assignments": {"2": amp["id"]}})
    assert res.status_code == 409
    assert "Next Race" in res.json()["detail"]
    assert _state() == "STOPPED"
    assert _registered() == before


def test_assign_requires_a_saved_relay_config():
    _stations(1)
    volt = _team("Volt", ["Alice", "Bob"])

    no_config = client.post(
        "/api/relay-teams/assign", json={"assignments": {"1": volt["id"]}}
    )
    assert no_config.status_code == 409
    assert _state() == "IDLE"

    _configure(mode="individual")
    individual = client.post(
        "/api/relay-teams/assign", json={"assignments": {"1": volt["id"]}}
    )
    assert individual.status_code == 409
    assert _registered() == {}


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"2": "ghost"}, id="unknown-team"),
        pytest.param({"9": "AMP"}, id="station-without-node"),
        pytest.param({"2": "AMP", "3": "AMP"}, id="same-team-twice"),
        pytest.param({"2": "TRIO"}, id="wrong-member-count"),
        pytest.param({"two": "AMP"}, id="non-numeric-station"),
    ],
)
def test_rejected_assign_leaves_registrations_and_state_unchanged(bad):
    _stations(1, 2, 3)
    _configure()
    volt = _team("Volt", ["Alice", "Bob"])
    amp = _team("Amp", ["Cara", "Dan"])
    trio = _team("Trio", ["Eve", "Fay", "Gus"])
    ids = {"AMP": amp["id"], "TRIO": trio["id"]}
    client.post("/api/relay-teams/assign", json={"assignments": {"1": volt["id"]}})
    before = _registered()
    assert before == {"1": ("Volt", ["Alice", "Bob"])}

    payload = {k: ids.get(v, v) for k, v in bad.items()}
    res = client.post("/api/relay-teams/assign", json={"assignments": payload})

    assert res.status_code == 400
    assert _registered() == before
    assert _state() == "READY"


def test_rejected_assign_from_idle_does_not_apply_the_config():
    from hub_server.domain.models import RaceConfig

    _stations(1)
    app_module.race_manager._config = RaceConfig(
        race_type="distance", competition_mode="relay", relay_legs=2, target_value=500
    )
    trio = _team("Trio", ["Eve", "Fay", "Gus"])

    res = client.post(
        "/api/relay-teams/assign", json={"assignments": {"1": trio["id"]}}
    )

    assert res.status_code == 400
    assert _state() == "IDLE"
    assert _registered() == {}


# ---- next race ----


def test_next_race_keeps_config_clears_registrations_and_ends_ready():
    _stations(1, 2)
    _configure()
    volt = _team("Volt", ["Alice", "Bob"])
    client.post("/api/relay-teams/assign", json={"assignments": {"1": volt["id"]}})
    app_module.race_manager.start_race()
    app_module.race_manager.stop_race()
    assert _state() == "STOPPED"

    res = client.post("/api/race/next-race")

    assert res.status_code == 200
    assert res.json()["state"] == "READY"
    assert res.json()["config"]["competition_mode"] == "relay"
    assert res.json()["config"]["relay_legs"] == 2
    assert _registered() == {}
    assert _state() == "READY"

    # ... and the follow-up assignment works (plain reset would have lost
    # the config and made this a 409).
    amp = _team("Amp", ["Cara", "Dan"])
    again = client.post(
        "/api/relay-teams/assign", json={"assignments": {"2": amp["id"]}}
    )
    assert again.status_code == 200
    assert _registered() == {"2": ("Amp", ["Cara", "Dan"])}


def test_next_race_refused_while_running_and_without_config():
    _stations(1)
    assert client.post("/api/race/next-race").status_code == 409  # no config

    _configure()
    volt = _team("Volt", ["Alice", "Bob"])
    client.post("/api/relay-teams/assign", json={"assignments": {"1": volt["id"]}})
    app_module.race_manager.start_race()

    res = client.post("/api/race/next-race")

    assert res.status_code == 409
    assert _state() == "RUNNING"
    assert _registered() == {"1": ("Volt", ["Alice", "Bob"])}


# ---- countdown guard ----


def _lock_countdown(monkeypatch):
    """The 3-2-1 countdown holds race_start_countdown_lock while the race is
    still READY."""
    import asyncio

    lock = asyncio.Lock()
    asyncio.run(lock.acquire())
    assert lock.locked()
    monkeypatch.setattr(app_module, "race_start_countdown_lock", lock)


def test_assign_refused_during_countdown_and_changes_nothing(monkeypatch):
    _stations(1, 2)
    _configure()
    volt = _team("Volt", ["Alice", "Bob"])
    amp = _team("Amp", ["Cara", "Dan"])
    client.post("/api/relay-teams/assign", json={"assignments": {"1": volt["id"]}})
    before = _registered()
    _lock_countdown(monkeypatch)

    res = client.post("/api/relay-teams/assign", json={"assignments": {"2": amp["id"]}})

    assert res.status_code == 409
    assert "countdown" in res.json()["detail"].lower()
    assert _registered() == before
    assert _state() == "READY"


def test_next_race_refused_during_countdown_and_changes_nothing(monkeypatch):
    _stations(1)
    _configure()
    volt = _team("Volt", ["Alice", "Bob"])
    client.post("/api/relay-teams/assign", json={"assignments": {"1": volt["id"]}})
    before = _registered()
    _lock_countdown(monkeypatch)

    res = client.post("/api/race/next-race")

    assert res.status_code == 409
    assert "countdown" in res.json()["detail"].lower()
    assert _registered() == before
    assert _state() == "READY"
    assert client.get("/api/race/state").json()["config"] is not None


def test_race_state_reports_countdown_active(monkeypatch):
    assert client.get("/api/race/state").json()["countdown_active"] is False
    _lock_countdown(monkeypatch)
    assert client.get("/api/race/state").json()["countdown_active"] is True
