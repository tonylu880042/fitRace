"""Challenge mode: after the result screen's minimum hold, the projector
shows the top-10 standings until the next sign-up. The decision is made on
the hub (pure usecase) and reaches the display-only dashboard as
`challenge_show_standings` in the race state."""

import asyncio
import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import hub_server.infrastructure.fastapi.app as hub_app
from hub_server.domain.models import RaceState
from hub_server.usecases.challenge_mode import challenge_shows_standings

INDEX = Path(__file__).resolve().parents[3] / "hub_server" / "static" / "index.html"
END = 1_000_000


def _shows(**overrides):
    args = dict(
        enabled=True,
        state=RaceState.STOPPED,
        end_time_epoch_ms=END,
        now_epoch_ms=END + 10_000,
        min_result_ms=10_000,
        registered_stations=[],
    )
    args.update(overrides)
    return challenge_shows_standings(**args)


# -- pure truth table -------------------------------------------------------------


def test_stopped_holds_the_result_screen_then_shows_standings():
    assert _shows(now_epoch_ms=END) is False
    assert _shows(now_epoch_ms=END + 9_999) is False
    assert _shows(now_epoch_ms=END + 10_000) is True
    assert _shows(now_epoch_ms=END + 999_999) is True


def test_stopped_without_an_end_time_shows_standings():
    assert _shows(end_time_epoch_ms=None, now_epoch_ms=0) is True


def test_idle_or_ready_with_nobody_registered_shows_standings():
    for state in (RaceState.IDLE, RaceState.READY):
        assert _shows(state=state, registered_stations=[]) is True


def test_ready_with_someone_registered_does_not():
    assert _shows(state=RaceState.READY, registered_stations=[1]) is False
    assert _shows(state=RaceState.IDLE, registered_stations=[2, 3]) is False


def test_running_and_challenge_off_never_show_standings():
    assert _shows(state=RaceState.RUNNING) is False
    for state in RaceState:
        assert _shows(enabled=False, state=state, now_epoch_ms=10**12) is False


# -- state payload and tick ----------------------------------------------------------


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


def _finish_run(client):
    client.post("/api/race/challenge", json={"enabled": True, "duration_sec": 120})
    client.post("/api/stations/assign", json={"station_number": 1, "node_id": "cs-1"})
    client.post("/api/race/register", json={"station_number": 1, "athlete_name": "P"})
    asyncio.run(hub_app.challenge_tick())
    assert hub_app.race_manager.get_state() == RaceState.RUNNING
    hub_app.race_manager.stop_race()
    return hub_app.race_manager.get_end_time_epoch_ms()


def test_state_payload_carries_the_flag(client):
    assert "challenge_show_standings" in client.get("/api/race/state").json()
    off = client.get("/api/race/state").json()
    assert off["challenge_show_standings"] is False
    client.post("/api/race/challenge", json={"enabled": True, "duration_sec": 120})
    on = client.get("/api/race/state").json()  # READY, nobody registered
    assert on["challenge_show_standings"] is True


def _count_state_broadcasts(monkeypatch):
    sent = []

    async def fake_broadcast(message):
        if message.get("type") == "state_change":
            sent.append(message)

    monkeypatch.setattr(hub_app.ws_manager, "broadcast", fake_broadcast)
    return sent


def test_tick_broadcasts_exactly_once_when_standings_start_showing(client, monkeypatch):
    end = _finish_run(client)
    asyncio.run(hub_app.broadcast_race_state(now_ms=end + 1))  # result screen sent
    sent = _count_state_broadcasts(monkeypatch)

    asyncio.run(hub_app.challenge_tick(lambda: end + 9_999))
    assert sent == []  # still inside the hold window

    asyncio.run(hub_app.challenge_tick(lambda: end + 10_000))
    assert len(sent) == 1
    assert sent[0]["challenge_show_standings"] is True

    asyncio.run(hub_app.challenge_tick(lambda: end + 10_500))
    asyncio.run(hub_app.challenge_tick(lambda: end + 20_000))
    assert len(sent) == 1  # not every tick


def test_tick_is_silent_when_challenge_mode_is_off(client, monkeypatch):
    sent = _count_state_broadcasts(monkeypatch)
    asyncio.run(hub_app.challenge_tick(lambda: 10**13))
    assert sent == []


# -- dashboard JS under node -----------------------------------------------------------

_LINE = re.compile(r"^[ \t]*//.*$\n?", re.MULTILINE)
_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)


def _script():
    src = INDEX.read_text(encoding="utf-8")
    start = src.index("<script>") + len("<script>")
    return _LINE.sub("", _BLOCK.sub("", src[start : src.index("</script>", start)]))


def _function(source, name):
    start = source.index(f"function {name}(")
    i = source.index("{", source.index(")", start))
    depth, in_str = 0, None
    while True:
        ch = source[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'`":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1


def _sync(state, data):
    js = f"""
let currentState = {json.dumps(state)};
const calls = [];
function enterIdleRecordWall() {{ calls.push("enter"); }}
function exitIdleRecordWall() {{ calls.push("exit"); }}
{_function(_script(), "syncRecordWall")}
syncRecordWall({json.dumps(data)});
console.log(JSON.stringify(calls));
"""
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=10)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_challenge_standings_flag_enters_the_wall_even_when_stopped():
    assert _sync("STOPPED", {"challenge_show_standings": True}) == ["enter"]
    assert _sync("READY", {"challenge_show_standings": True}) == ["enter"]


def test_flag_false_exits_the_wall():
    assert _sync("STOPPED", {"challenge_show_standings": False}) == ["exit"]
    assert _sync("READY", {"challenge_show_standings": False}) == ["exit"]


def test_challenge_off_keeps_todays_idle_only_behaviour():
    assert _sync("STOPPED", {}) == ["exit"]
    assert _sync("READY", {}) == ["exit"]
    assert _sync("RUNNING", {}) == ["exit"]
    assert _sync("IDLE", {}) == ["enter"]


def test_class_mode_never_shows_the_wall():
    assert _sync("IDLE", {"session_mode": "class"}) == ["exit"]
    assert _sync(
        "STOPPED", {"session_mode": "class", "challenge_show_standings": True}
    ) == ["exit"]
