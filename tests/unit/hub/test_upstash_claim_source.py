import asyncio
import json

from hub_server.adapters.upstash_claim_source import UpstashClaimSource


class FakePost:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body, timeout))
        if self.error:
            raise self.error
        return self.response


def _source(post, clock):
    return UpstashClaimSource(
        base_url="https://redis.example.upstash.io",
        token="tok",
        venue="gym-a",
        post_json=post,
        now_s=lambda: clock[0],
    )


def test_pops_up_to_10_claims_from_the_venue_key_and_parses_them():
    claims = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    post = FakePost({"result": [json.dumps(c) for c in claims]})
    clock = [100.0]
    source = _source(post, clock)

    got = asyncio.run(source.fetch())

    assert got == claims
    url, headers, body, timeout = post.calls[0]
    assert url == "https://redis.example.upstash.io"
    assert body == ["LPOP", "fitrace:claims:gym-a", "10"]
    assert headers["Authorization"] == "Bearer tok"
    assert timeout == 5
    assert source.last_success_epoch_s == 100.0


def test_empty_queue_returns_empty_list_and_still_counts_as_success():
    clock = [50.0]
    source = _source(FakePost({"result": None}), clock)

    assert asyncio.run(source.fetch()) == []
    assert source.last_success_epoch_s == 50.0
    assert source.last_failure_epoch_s is None


def test_network_error_returns_empty_and_records_failure_time():
    clock = [10.0]
    post = FakePost({"result": []})
    source = _source(post, clock)
    asyncio.run(source.fetch())

    clock[0] = 20.0
    post.error = OSError("unreachable")
    assert asyncio.run(source.fetch()) == []

    assert source.last_failure_epoch_s == 20.0
    assert source.last_success_epoch_s == 10.0  # unchanged by the failure


def test_error_response_from_upstash_is_a_failure():
    clock = [5.0]
    source = _source(FakePost({"error": "WRONGTYPE"}), clock)

    assert asyncio.run(source.fetch()) == []
    assert source.last_failure_epoch_s == 5.0
    assert source.last_success_epoch_s is None


def test_malformed_entries_are_skipped_but_good_ones_kept():
    post = FakePost({"result": ["{not json", json.dumps({"id": "ok"}), "123"]})
    source = _source(post, [1.0])

    assert asyncio.run(source.fetch()) == [{"id": "ok"}]
