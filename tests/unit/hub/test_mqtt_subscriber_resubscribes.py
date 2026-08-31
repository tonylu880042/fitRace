"""The hub's subscriptions have to survive a broker restart.

Proven in the field on 2026-08-21: restarting mosquitto left the hub deaf --
the edge reconnected and kept publishing within a second, while the hub still
showed it `offline` 44s later and only a hub restart brought the telemetry
back. Every station on the dashboard goes blank in that state, and nothing in
/health or the page says so.

The cause is that start_listening() subscribed straight on the paho client,
which does not restore subscriptions after a reconnect. Registering through
the wrapper instead lets it replay them on every connect.
"""

from hub_server.adapters.mqtt_subscriber import MqttSubscriber

HUB_TOPICS = {
    "gym/telemetry/#",
    "fitrace/nodes/+/status",
    "fitrace/nodes/+/bindings_removed",
}


class FakePahoClient:
    def __init__(self):
        self.on_message = None
        self.subscribed = []

    def subscribe(self, topic):
        self.subscribed.append(topic)


class FakeAsyncMqttClient:
    """Mirrors the wrapper surface start_listening is allowed to use."""

    def __init__(self):
        self._client = FakePahoClient()
        self.replayable_topics = []

    def subscribe(self, topic):
        self.replayable_topics.append(topic)
        self._client.subscribe(topic)


# async so a running event loop exists: MqttSubscriber grabs one in its
# constructor (asyncio.get_event_loop), which raises once an earlier test
# module has closed the loop it created.
def _subscriber(client):
    return MqttSubscriber(
        client,
        race_manager=None,
        ws_manager=None,
        node_registry=None,
    )


async def test_start_listening_registers_topics_through_the_wrapper():
    client = FakeAsyncMqttClient()

    _subscriber(client).start_listening()

    assert set(client.replayable_topics) == HUB_TOPICS, (
        "topics registered straight on the paho client are lost on reconnect, "
        "which leaves the hub deaf after any broker blip"
    )


async def test_start_listening_still_installs_the_message_handler():
    client = FakeAsyncMqttClient()

    subscriber = _subscriber(client)
    subscriber.start_listening()

    assert client._client.on_message == subscriber._on_message
