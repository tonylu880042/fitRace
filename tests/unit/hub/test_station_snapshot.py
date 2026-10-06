"""R5 (hub side): publish the assignable stations to Upstash so the cloud
sign-up page can show a picker -- only on change, refreshed before the 30 s
TTL runs out."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import hub_server.infrastructure.fastapi.app as hub_app
from hub_server.adapters.upstash_claim_source import UpstashClaimSource
from hub_server.infrastructure.cloud_signup_config import load_cloud_signup_config
from hub_server.usecases.cloud_signup import (
    build_station_snapshot,
    should_publish_snapshot,
)

STATIONS = {
    1: {
        "node_id": "n1",
        "node_display_name": "Treadmill A",
        "equipment_type": "treadmill",
        "registered": False,
    },
    2: {
        "node_id": "n2",
        "node_display_name": "n2",
        "equipment_type": "treadmill",
        "registered": True,
    },
    3: {"node_id": None, "equipment_type": None, "registered": True},  # not assigned
    4: {"node_id": "n4", "equipment_type": "rowing_machine", "registered": False},
}


def test_snapshot_lists_assigned_stations_in_order_with_availability():
    snap = build_station_snapshot(STATIONS, queued_stations=[4])
    assert snap == [
        {
            "station": 1,
            "label": "Treadmill A",
            "equipment_type": "treadmill",
            "available": True,
        },
        {
            "station": 2,
            "label": None,
            "equipment_type": "treadmill",
            "available": False,
        },
        {
            "station": 4,
            "label": None,
            "equipment_type": "rowing_machine",
            "available": False,
        },
    ]


def test_snapshot_label_falls_back_to_none_when_it_is_just_the_node_id():
    assert build_station_snapshot(STATIONS, [])[1]["label"] is None


def test_snapshot_of_nothing_is_an_empty_list():
    assert build_station_snapshot({}, []) == []


def test_publish_policy_first_change_and_refresh():
    a, b = '[{"x":1}]', '[{"x":2}]'
    assert should_publish_snapshot(None, None, a, now_s=100) is True
    assert should_publish_snapshot(a, 100, a, now_s=119.9) is False
    assert should_publish_snapshot(a, 100, a, now_s=120) is True
    assert should_publish_snapshot(a, 100, b, now_s=101) is True


# -- adapter ------------------------------------------------------------------------------


class FakePost:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        if self.error:
            raise self.error
        return {"result": "OK"}


def test_adapter_sets_the_snapshot_key_with_a_30s_ttl():
    post = FakePost()
    source = UpstashClaimSource(
        "https://r.io", "tok", "gym-a", post_json=post, now_s=lambda: 7.0
    )
    snap = [
        {"station": 1, "label": None, "equipment_type": "treadmill", "available": True}
    ]

    assert asyncio.run(source.publish_stations(snap)) is True

    url, headers, body, timeout = post.calls[0]
    assert url == "https://r.io" and headers["Authorization"] == "Bearer tok"
    assert body[:2] == ["SET", "fitrace:stations:gym-a"] and body[3:] == ["EX", "30"]
    assert json.loads(body[2]) == snap
    assert timeout == 5


def test_adapter_publish_failure_is_swallowed_and_reported():
    source = UpstashClaimSource(
        "https://r.io", "tok", "gym-a", post_json=FakePost(OSError("down"))
    )
    assert asyncio.run(source.publish_stations([])) is False


# -- app wiring -----------------------------------------------------------------------------

ENV = {
    "FITRACE_CLOUD_SIGNUP_URL": "https://signup.example.app",
    "FITRACE_CLOUD_SIGNUP_SECRET": "s3cret",
    "FITRACE_VENUE_ID": "gym-a",
    "UPSTASH_REDIS_REST_URL": "https://r.upstash.io",
    "UPSTASH_REDIS_REST_TOKEN": "tok",
}


class FakeSource:
    def __init__(self):
        self.last_success_epoch_s = 1e12
        self.published = []

    async def fetch(self):
        return []

    async def publish_stations(self, snapshot):
        self.published.append(snapshot)
        return True


@pytest.fixture
def client(monkeypatch):
    c = TestClient(hub_app.app)
    c.post("/api/race/reset")
    for sn in list(hub_app.race_manager.get_stations_status()["stations"]):
        hub_app.race_manager.assign_station(sn, None)
    cfg = load_cloud_signup_config(ENV)
    source = FakeSource()
    monkeypatch.setattr(hub_app, "cloud_signup_config", cfg)
    monkeypatch.setattr(hub_app, "cloud_claim_source", source)
    monkeypatch.setattr(
        hub_app,
        "cloud_signup_processor",
        hub_app.build_cloud_signup_processor(cfg, source),
    )
    monkeypatch.setattr(hub_app, "_last_published_stations", None)
    yield c, source
    c.post("/api/race/reset")
    for sn in list(hub_app.race_manager.get_stations_status()["stations"]):
        hub_app.race_manager.assign_station(sn, None)


def test_tick_publishes_once_then_only_on_change_or_refresh(client):
    c, source = client
    c.post("/api/stations/assign", json={"station_number": 1, "node_id": "ss-1"})
    c.post("/api/stations/assign", json={"station_number": 2, "node_id": "ss-2"})
    c.post("/api/race/configure", json={"race_type": "time", "duration_sec": 60})

    asyncio.run(hub_app.cloud_signup_tick(lambda: 1000.0))
    asyncio.run(hub_app.cloud_signup_tick(lambda: 1005.0))
    asyncio.run(hub_app.cloud_signup_tick(lambda: 1019.0))
    assert len(source.published) == 1
    assert [s["station"] for s in source.published[0]] == [1, 2]
    assert all(s["available"] for s in source.published[0])

    asyncio.run(hub_app.cloud_signup_tick(lambda: 1020.0))
    assert len(source.published) == 2  # TTL refresh

    c.post("/api/race/register", json={"station_number": 1, "athlete_name": "A"})
    asyncio.run(hub_app.cloud_signup_tick(lambda: 1021.0))
    assert len(source.published) == 3  # changed
    by_station = {s["station"]: s["available"] for s in source.published[-1]}
    assert by_station == {1: False, 2: True}


def test_queued_signups_make_their_station_unavailable(client):
    c, source = client
    c.post("/api/stations/assign", json={"station_number": 1, "node_id": "ss-1"})
    hub_app.challenge_pending_registrations.append(
        {"station_number": 1, "athlete_name": "Q"}
    )
    try:
        asyncio.run(hub_app.cloud_signup_tick(lambda: 1.0))
    finally:
        hub_app.challenge_pending_registrations.clear()
    assert source.published[-1][0]["available"] is False
