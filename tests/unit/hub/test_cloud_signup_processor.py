import asyncio
import logging

from hub_server.usecases.cloud_signup import (
    CloudSignupProcessor,
    build_cloud_signup_url,
    choose_signup_station,
)
from hub_server.usecases.signup_token import make_signup_token

SECRET = "s3cret"
VENUE = "gym-a"
NOW = 1_800_000_000
PHOTO = "data:image/webp;base64,UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4H"


def _claim(cid, station=1, name="Amy", venue=VENUE, token=None, avatar=PHOTO):
    if token is None:
        token = make_signup_token(SECRET, venue, station, NOW + 500, cid)
    return {
        "id": cid,
        "venue": venue,
        "station": station,
        "token": token,
        "name": name,
        "avatar_base64": avatar,
        "received_at": NOW,
    }


class Harness:
    def __init__(self, assigned=(1,), open_stations=None):
        self.batches = []
        self.registered = []
        self.assigned = set(assigned)
        self.open = set(self.assigned if open_stations is None else open_stations)
        self.fail_for = set()
        self.pulled = []
        self.now = NOW
        self.processor = CloudSignupProcessor(
            secret=SECRET,
            venue=VENUE,
            fetch_claims=self._fetch,
            register=self._register,
            assigned_stations=lambda: set(self.assigned),
            station_open=lambda sn: sn in self.open,
            now_s=lambda: self.now,
            on_claims_pulled=lambda: self.pulled.append(True),
        )

    async def _fetch(self):
        return self.batches.pop(0) if self.batches else []

    async def _register(self, station, name, avatar):
        if name in self.fail_for:
            raise ValueError("bad avatar")
        self.registered.append((station, name, avatar))
        self.open.discard(station)  # a registered station is no longer open

    def tick(self, *claims):
        self.batches.append(list(claims))
        return asyncio.run(self.processor.tick())


def test_valid_claim_registers_on_open_station():
    h = Harness()
    assert h.tick(_claim("c1")) == 1
    assert h.registered == [(1, "Amy", PHOTO)]
    assert h.processor.queue_length == 0


def test_claim_without_photo_registers_with_none():
    h = Harness()
    claim = _claim("c1", avatar=None)
    h.tick(claim)
    assert h.registered == [(1, "Amy", None)]


def test_invalid_claims_are_dropped_and_logged_without_the_photo(caplog):
    h = Harness(assigned=(1,))
    secret_photo = "data:image/webp;base64,SUPERSECRETPHOTOBYTES"
    bad = [
        _claim("wrong-venue", venue="other", avatar=secret_photo),
        _claim("unassigned", station=9, avatar=secret_photo),
        _claim("bad-token", token="1.deadbeefdeadbeef", avatar=secret_photo),
        _claim(
            "expired",
            token=make_signup_token(SECRET, VENUE, 1, NOW - 1, "n0"),
            avatar=secret_photo,
        ),
        _claim("empty-name", name="   ", avatar=secret_photo),
        _claim("long-name", name="x" * 21, avatar=secret_photo),
        {"id": "no-fields"},
        "not-a-dict",
    ]
    with caplog.at_level(logging.DEBUG):
        assert h.tick(*bad) == 0
    assert h.registered == []
    assert h.processor.queue_length == 0
    assert "SUPERSECRETPHOTOBYTES" not in caplog.text
    assert "wrong-venue" in caplog.text  # something useful is logged


def test_name_is_stripped_and_20_chars_is_allowed():
    h = Harness()
    h.tick(_claim("c1", name="  " + "x" * 20 + "  "))
    assert h.registered[0][1] == "x" * 20


def test_duplicate_claim_ids_are_registered_once_across_ticks():
    h = Harness()
    h.tick(_claim("dup"))
    h.open.add(1)
    h.tick(_claim("dup", name="Replay"))
    assert [r[1] for r in h.registered] == ["Amy"]


def test_duplicate_id_within_one_batch_is_queued_once():
    h = Harness(open_stations=())
    h.tick(_claim("dup"), _claim("dup"))
    assert h.processor.queue_length == 1


def test_busy_station_keeps_claims_queued_in_fifo_order():
    h = Harness(open_stations=())  # race running: nothing open
    h.tick(_claim("c1", name="First"), _claim("c2", name="Second"))
    assert h.registered == []
    assert h.processor.queue_length == 2

    h.open.add(1)  # race reset, station open again
    assert h.tick() == 1
    assert [r[1] for r in h.registered] == ["First"]
    assert h.processor.queue_length == 1

    h.open.add(1)
    h.tick()
    assert [r[1] for r in h.registered] == ["First", "Second"]
    assert h.processor.queue_length == 0


def test_stations_are_served_independently():
    h = Harness(assigned=(1, 2), open_stations=(2,))
    h.tick(_claim("c1", station=1, name="A"), _claim("c2", station=2, name="B"))
    assert [(r[0], r[1]) for r in h.registered] == [(2, "B")]
    assert h.processor.queue_length == 1  # station 1's claim waits


def test_queued_claim_survives_token_expiry_while_waiting():
    h = Harness(open_stations=())
    h.tick(_claim("c1"))
    h.now = NOW + 10_000  # token long expired, but it was valid on arrival
    h.open.add(1)
    h.tick()
    assert [r[1] for r in h.registered] == ["Amy"]


def test_registration_failure_drops_that_claim_and_continues():
    h = Harness(assigned=(1, 2))
    h.fail_for.add("Bad")
    h.tick(_claim("c1", station=1, name="Bad"), _claim("c2", station=2, name="Good"))
    assert [r[1] for r in h.registered] == ["Good"]
    assert h.processor.queue_length == 0


def test_fetch_returning_nothing_is_fine():
    h = Harness()
    assert h.tick() == 0


def test_choose_signup_station_prefers_first_unregistered_assigned():
    assert choose_signup_station([1, 2, 3], registered=[1]) == 2
    assert choose_signup_station([3, 1, 2], registered=[]) == 1
    # everyone registered: still point at the lowest assigned station so the
    # next person queues up for the following round.
    assert choose_signup_station([1, 2], registered=[1, 2]) == 1
    assert choose_signup_station([], registered=[]) is None


def test_build_cloud_signup_url_carries_venue_station_and_token():
    from urllib.parse import parse_qs, urlparse

    token = make_signup_token(SECRET, "gym a", 2, NOW + 300, "n1")
    url = build_cloud_signup_url("https://signup.example.app/", "gym a", 2, token)
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://signup.example.app/?")
    assert q["v"] == ["gym a"] and q["s"] == ["2"] and q["t"] == [token]


def test_a_token_is_accepted_for_only_the_first_claim():
    h = Harness(assigned=(1, 2))
    shared = make_signup_token(SECRET, VENUE, 1, NOW + 500, "n1")
    h.tick(_claim("c1", name="First", token=shared))
    h.open.add(1)
    h.tick(_claim("c2", name="Second", token=shared))
    assert [r[1] for r in h.registered] == ["First"]
    assert h.processor.queue_length == 0


def test_any_pulled_claim_triggers_token_rotation_even_if_it_is_invalid():
    h = Harness()
    h.tick()
    assert h.pulled == []
    h.tick(_claim("bad", venue="other"))
    assert h.pulled == [True]
    h.tick(_claim("good"))
    assert h.pulled == [True, True]
