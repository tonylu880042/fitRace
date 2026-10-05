"""B2: the state's signup_url / cloud_signup_online / cloud_signup_queue_length,
and the app wiring that feeds the processor."""

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import hub_server.infrastructure.fastapi.app as hub_app
from hub_server.infrastructure.cloud_signup_config import load_cloud_signup_config
from hub_server.usecases.cloud_signup import build_signup_fields
from hub_server.usecases.signup_token import make_signup_token, verify_signup_token

SECRET = "s3cret"
VENUE = "gym-a"
LAN = "http://192.168.1.5:8000/static/signup.html"
NOW = 1_800_000_000
CLOUD = "https://signup.example.app"
PHOTO = "data:image/webp;base64,UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4H"


def _fields(**overrides):
    args = dict(
        cloud_base_url=CLOUD,
        secret=SECRET,
        venue=VENUE,
        last_success_epoch_s=NOW - 5,
        now_s=NOW,
        token_exp_epoch_s=NOW + 300,
        token_nonce="n1",
        issue_tokens=True,
        assigned=[1],
        registered=[],
        lan_url=LAN,
        queue_length=3,
    )
    args.update(overrides)
    return build_signup_fields(**args)


# -- pure field builder --------------------------------------------------------


def test_online_cloud_uses_the_cloud_url_with_a_valid_token():
    fields = _fields()
    assert fields["cloud_signup_online"] is True
    assert fields["cloud_signup_queue_length"] == 3
    q = parse_qs(urlparse(fields["signup_url"]).query)
    assert fields["signup_url"].startswith(CLOUD + "?")
    assert q["v"] == [VENUE] and q["s"] == ["1"]
    assert verify_signup_token(SECRET, VENUE, 1, q["t"][0], NOW)
    assert q["t"][0] == make_signup_token(SECRET, VENUE, 1, NOW + 300, "n1")


def test_no_token_is_issued_while_challenge_mode_is_off():
    fields = _fields(issue_tokens=False)
    assert fields["signup_url"] == LAN
    assert fields["cloud_signup_online"] is True


def test_stale_pull_falls_back_to_the_lan_url():
    fields = _fields(last_success_epoch_s=NOW - 31)
    assert fields["cloud_signup_online"] is False
    assert fields["signup_url"] == LAN


def test_pull_exactly_30s_old_still_counts_as_online():
    assert _fields(last_success_epoch_s=NOW - 30)["cloud_signup_online"] is True


def test_never_pulled_falls_back_to_the_lan_url():
    fields = _fields(last_success_epoch_s=None)
    assert fields["cloud_signup_online"] is False
    assert fields["signup_url"] == LAN


def test_no_assigned_station_falls_back_to_the_lan_url():
    assert _fields(assigned=[])["signup_url"] == LAN


def test_disabled_feature_is_just_the_lan_url():
    fields = _fields(
        cloud_base_url=None, secret=None, venue=None, queue_length=0, issue_tokens=False
    )
    assert fields == {
        "signup_url": LAN,
        "cloud_signup_online": False,
        "cloud_signup_queue_length": 0,
    }


def test_qr_points_at_the_first_unregistered_station():
    q = parse_qs(urlparse(_fields(assigned=[1, 2], registered=[1])["signup_url"]).query)
    assert q["s"] == ["2"]


# -- env config ------------------------------------------------------------------

ENV = {
    "FITRACE_CLOUD_SIGNUP_URL": CLOUD,
    "FITRACE_CLOUD_SIGNUP_SECRET": SECRET,
    "FITRACE_VENUE_ID": VENUE,
    "UPSTASH_REDIS_REST_URL": "https://r.upstash.io",
    "UPSTASH_REDIS_REST_TOKEN": "tok",
}


def test_config_loads_when_all_five_variables_are_present():
    cfg = load_cloud_signup_config(ENV)
    assert cfg.base_url == CLOUD and cfg.secret == SECRET and cfg.venue == VENUE
    assert cfg.upstash_url == "https://r.upstash.io" and cfg.upstash_token == "tok"


@pytest.mark.parametrize("missing", sorted(ENV))
def test_config_is_disabled_if_any_variable_is_missing_or_blank(missing):
    assert (
        load_cloud_signup_config({k: v for k, v in ENV.items() if k != missing}) is None
    )
    assert load_cloud_signup_config({**ENV, missing: "  "}) is None


# -- app wiring ----------------------------------------------------------------------


class FakeSource:
    def __init__(self, claims=(), last_success=None):
        self.claims = list(claims)
        self.last_success_epoch_s = last_success

    async def fetch(self):
        out, self.claims = self.claims, []
        return out


@pytest.fixture
def client(monkeypatch):
    c = TestClient(hub_app.app)
    c.post("/api/race/reset")
    for sn in list(hub_app.race_manager.get_stations_status()["stations"]):
        hub_app.race_manager.assign_station(sn, None)
    monkeypatch.setattr(hub_app, "get_real_ip", lambda: "192.168.1.5")
    monkeypatch.setattr(hub_app, "_lan_ip_cache", None)
    yield c
    c.post("/api/race/reset")
    for sn in list(hub_app.race_manager.get_stations_status()["stations"]):
        hub_app.race_manager.assign_station(sn, None)


def test_state_has_lan_signup_url_when_cloud_is_not_configured(client, monkeypatch):
    monkeypatch.setattr(hub_app, "cloud_signup_config", None)
    monkeypatch.setattr(hub_app, "cloud_claim_source", None)
    monkeypatch.setattr(hub_app, "cloud_signup_processor", None)

    state = client.get("/api/race/state").json()

    assert state["signup_url"] == LAN
    assert state["cloud_signup_online"] is False
    assert state["cloud_signup_queue_length"] == 0


def test_state_uses_cloud_url_when_online_and_challenge_mode_is_on(client, monkeypatch):
    cfg = load_cloud_signup_config(ENV)
    source = FakeSource(last_success=hub_app.time.time())
    monkeypatch.setattr(hub_app, "cloud_signup_config", cfg)
    monkeypatch.setattr(hub_app, "cloud_claim_source", source)
    monkeypatch.setattr(hub_app, "cloud_signup_processor", None)
    client.post("/api/stations/assign", json={"station_number": 1, "node_id": "cs-1"})
    hub_app.race_manager.set_challenge_settings(True, 180)
    try:
        state = client.get("/api/race/state").json()
        hub_app.race_manager.set_challenge_settings(False, 180)
        challenge_off = client.get("/api/race/state").json()
    finally:
        hub_app.race_manager.set_challenge_settings(False, 180)

    assert state["cloud_signup_online"] is True
    assert state["signup_url"].startswith(CLOUD + "?v=")
    assert challenge_off["signup_url"] == LAN


def test_pulling_a_claim_rotates_the_qr_token(client, monkeypatch):
    client.post("/api/stations/assign", json={"station_number": 1, "node_id": "cs-1"})
    client.post("/api/race/configure", json={"race_type": "time", "duration_sec": 60})
    hub_app.race_manager.set_challenge_settings(True, 180)
    try:
        _enable_cloud(monkeypatch, [])
        before = client.get("/api/race/state").json()["signup_url"]
        assert client.get("/api/race/state").json()["signup_url"] == before

        hub_app.cloud_claim_source.claims = [_valid_claim()]
        asyncio.run(hub_app.cloud_signup_tick())
        after = client.get("/api/race/state").json()["signup_url"]
    finally:
        hub_app.race_manager.set_challenge_settings(False, 180)

    assert before.startswith(CLOUD) and after.startswith(CLOUD)
    assert before != after


def _enable_cloud(monkeypatch, claims):
    cfg = load_cloud_signup_config(ENV)
    source = FakeSource(claims, last_success=hub_app.time.time())
    monkeypatch.setattr(hub_app, "cloud_signup_config", cfg)
    monkeypatch.setattr(hub_app, "cloud_claim_source", source)
    monkeypatch.setattr(
        hub_app,
        "cloud_signup_processor",
        hub_app.build_cloud_signup_processor(cfg, source),
    )
    return source


def _valid_claim(cid="c1", station=1, name="Amy", avatar=PHOTO):
    exp = int(hub_app.time.time()) + 250
    return {
        "id": cid,
        "venue": VENUE,
        "station": station,
        "token": make_signup_token(SECRET, VENUE, station, exp, "n0"),
        "name": name,
        "avatar_base64": avatar,
        "received_at": 1,
    }


def test_tick_registers_a_cloud_claim_with_a_stable_avatar(client, monkeypatch):
    client.post("/api/stations/assign", json={"station_number": 1, "node_id": "cs-1"})
    client.post("/api/race/configure", json={"race_type": "time", "duration_sec": 60})
    _enable_cloud(monkeypatch, [_valid_claim()])

    assert asyncio.run(hub_app.cloud_signup_tick()) == 1

    station = hub_app.race_manager.get_stations_status()["stations"][1]
    assert station["registered"] is True
    assert station["athlete_name"] == "Amy"
    assert station["has_avatar"] is True
    rows = client.get("/api/race/state").json()["leaderboard"]
    url = next(r["avatar_url"] for r in rows.values() if r["station_number"] == 1)
    assert client.get(url).status_code == 200


def test_tick_with_bad_photo_drops_the_claim_and_leaves_station_open(
    client, monkeypatch
):
    client.post("/api/stations/assign", json={"station_number": 1, "node_id": "cs-1"})
    client.post("/api/race/configure", json={"race_type": "time", "duration_sec": 60})
    _enable_cloud(monkeypatch, [_valid_claim(avatar="data:image/png;base64,AAAA")])

    assert asyncio.run(hub_app.cloud_signup_tick()) == 0

    assert (
        hub_app.race_manager.get_stations_status()["stations"][1]["registered"] is False
    )


def test_claim_waits_in_queue_while_race_is_running_then_registers_after_reset(
    client, monkeypatch
):
    monkeypatch.setattr(hub_app, "enforce_race_readiness", lambda: None)
    client.post("/api/stations/assign", json={"station_number": 1, "node_id": "cs-1"})
    client.post("/api/race/configure", json={"race_type": "time", "duration_sec": 60})
    client.post(
        "/api/race/register", json={"station_number": 1, "athlete_name": "Runner"}
    )
    client.post("/api/race/start")
    _enable_cloud(monkeypatch, [_valid_claim(name="Next")])

    assert asyncio.run(hub_app.cloud_signup_tick()) == 0
    assert hub_app.cloud_signup_processor.queue_length == 1
    assert client.get("/api/race/state").json()["cloud_signup_queue_length"] == 1

    client.post("/api/race/reset")
    client.post("/api/race/configure", json={"race_type": "time", "duration_sec": 60})
    assert asyncio.run(hub_app.cloud_signup_tick()) == 1
    assert (
        hub_app.race_manager.get_stations_status()["stations"][1]["athlete_name"]
        == "Next"
    )


def test_tick_is_a_noop_when_cloud_signup_is_disabled(client, monkeypatch):
    monkeypatch.setattr(hub_app, "cloud_signup_processor", None)
    assert asyncio.run(hub_app.cloud_signup_tick()) == 0
