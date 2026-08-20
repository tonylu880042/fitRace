"""The edge node has to find its way back to the hub without a reboot.

Three defects behind "the edge page cannot reach the Central Hub again, only
a reboot fixes it", all pinned here:

A. A broker that is not up yet when the edge boots used to be fatal --
   `connect()` raised on a 10s timeout and main.py latched the node into
   standalone mode with `mqtt_client = None` for the rest of the process.
   paho retries in the background on its own; the connect step must not
   throw that away.
B. Subscriptions were registered once, straight on the paho client. paho does
   not restore them after a reconnect, so one broker blip left the node deaf
   to `fitrace/nodes/+/command` while looking perfectly healthy.
C. The connect/disconnect callbacks run on the paho network thread but poked
   an `asyncio.Event` directly. That is not thread-safe: the awaiting
   coroutine is never scheduled, so a publish blocked on the connection stays
   blocked even though MQTT is back. The venue log shows the startup shape of
   this exactly -- "MQTT Connected" at 05:57:02.028, and the coroutine waiting
   on it only waking at 05:57:12.029 when the 10s timeout timer happened to
   run the loop.
"""

import asyncio
import threading
import time

import pytest

from edge_node.infrastructure.mqtt import client as client_module
from edge_node.infrastructure.mqtt.client import AsyncMqttClient


class FakePahoClient:
    """Just enough paho to drive the wrapper: records calls, fires callbacks
    only when a test asks it to."""

    def __init__(self, *args, **kwargs):
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.subscribed = []
        self.published = []
        self.loop_started = False
        self.connect_async_calls = []

    def connect_async(self, host, port):
        self.connect_async_calls.append((host, port))

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        pass

    def subscribe(self, topic):
        self.subscribed.append(topic)

    def publish(self, topic, payload, qos=1):
        self.published.append((topic, payload, qos))

        class _Info:
            def is_published(self):
                return True

        return _Info()

    # -- test-side helpers --------------------------------------------------
    def fire_connect(self):
        self.on_connect(self, None, {}, 0, None)

    def fire_disconnect(self):
        self.on_disconnect(self, None, {}, 0, None)


@pytest.fixture
def fake_paho(monkeypatch):
    created = []

    class _Factory:
        CallbackAPIVersion = client_module.mqtt.CallbackAPIVersion

        @staticmethod
        def Client(*args, **kwargs):  # noqa: N802 - mirrors the paho name
            fake = FakePahoClient()
            created.append(fake)
            return fake

    monkeypatch.setattr(client_module, "mqtt", _Factory)
    return created


def _client():
    return AsyncMqttClient(host="127.0.0.1", port=1883, client_id="edge-test")


# -- A. a broker that is down at boot must not be fatal ----------------------


async def test_connect_does_not_raise_when_the_broker_is_not_up_yet(fake_paho):
    """Nothing here may throw: main.py used to turn the exception into a
    permanent standalone mode that only a process restart cleared."""
    client = _client()
    await client.connect(timeout_sec=0.05)  # no on_connect ever fires

    assert fake_paho[0].loop_started, "paho retry loop must be left running"
    assert fake_paho[0].connect_async_calls == [("127.0.0.1", 1883)]


async def test_a_late_broker_still_gets_the_node_publishing(fake_paho):
    """The whole point of not raising: the node picks the hub up whenever it
    finally appears."""
    client = _client()
    await client.connect(timeout_sec=0.05)

    fake_paho[0].fire_connect()
    await client.publish("gym/telemetry/n1", "{}")

    assert fake_paho[0].published == [("gym/telemetry/n1", "{}", 1)]


# -- B. subscriptions have to survive a reconnect ---------------------------


async def test_subscriptions_are_replayed_on_every_connect(fake_paho):
    client = _client()
    client.subscribe("fitrace/nodes/command")
    await client.connect(timeout_sec=0.05)

    fake_paho[0].fire_connect()
    after_first = fake_paho[0].subscribed.count("fitrace/nodes/command")
    fake_paho[0].fire_disconnect()
    fake_paho[0].fire_connect()
    after_second = fake_paho[0].subscribed.count("fitrace/nodes/command")

    assert (
        after_second > after_first
    ), "a reconnect left the node deaf to its command topic"


async def test_a_topic_is_subscribed_the_moment_it_is_asked_for(fake_paho):
    client = _client()
    await client.connect(timeout_sec=0.05)

    client.subscribe("fitrace/nodes/edge-01/command")

    assert "fitrace/nodes/edge-01/command" in fake_paho[0].subscribed


# -- C. callbacks arrive on the paho thread ---------------------------------


async def test_a_connect_from_the_paho_thread_wakes_a_waiting_publish_at_once(
    fake_paho,
):
    """The regression that cost a reboot, pinned by latency rather than by
    completion.

    Poking the asyncio.Event straight from the paho thread does schedule the
    waiter -- but it never wakes the loop out of select(), so the publish only
    resumes when some other timer happens to run the loop. With nothing else
    pending here, that is the wait_for deadline below; on the venue node at
    startup it was the 10s connect timeout, which is why the log shows
    "MQTT Connected" ten seconds before the coroutine waiting on it moved.
    """
    client = _client()
    await client.connect(timeout_sec=0.05)

    publishing = asyncio.ensure_future(
        client.publish("gym/telemetry/n1", "{}", timeout_sec=30.0)
    )
    await asyncio.sleep(0.05)
    assert not publishing.done(), "test is not exercising the disconnected path"

    # The callback has to land while the loop is genuinely idle in select().
    # Firing it immediately would usually beat the loop into its sleep, and
    # then even a non-thread-safe wake-up looks fine.
    def connect_after_the_loop_has_settled():
        time.sleep(0.3)
        fake_paho[0].fire_connect()

    started = time.monotonic()
    threading.Thread(target=connect_after_the_loop_has_settled).start()
    await asyncio.wait_for(publishing, timeout=10.0)
    woke_after = time.monotonic() - started

    assert woke_after < 1.0, (
        f"publish resumed only after {woke_after:.1f}s -- the connect callback "
        "did not wake the event loop"
    )
    assert fake_paho[0].published == [("gym/telemetry/n1", "{}", 1)]


async def test_a_disconnect_from_the_paho_thread_blocks_the_next_publish(fake_paho):
    client = _client()
    await client.connect(timeout_sec=0.05)
    fake_paho[0].fire_connect()

    thread = threading.Thread(target=fake_paho[0].fire_disconnect)
    thread.start()
    thread.join()
    await asyncio.sleep(0.05)

    with pytest.raises(ConnectionError):
        await client.publish("gym/telemetry/n1", "{}", timeout_sec=0.1)


# -- publishing must fail fast rather than pile up --------------------------


async def test_publish_gives_up_instead_of_waiting_for_the_broker_forever(fake_paho):
    """One awaited publish per telemetry sample: if each one waits forever
    while the hub is away, they pile up until the node dies of memory rather
    than of MQTT."""
    client = _client()
    await client.connect(timeout_sec=0.05)

    with pytest.raises(ConnectionError):
        await client.publish("gym/telemetry/n1", "{}", timeout_sec=0.1)

    assert fake_paho[0].published == []
