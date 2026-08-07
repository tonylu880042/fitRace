import json
import socket
import time

from edge_node.infrastructure.mqtt import one_shot_publisher


class _FakeMessageInfo:
    def is_published(self):
        return True


class _FakeMqttClient:
    """Records connect/publish calls; simulates an immediate successful
    CONNACK so tests never touch a real socket."""

    instances: list["_FakeMqttClient"] = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.published = []
        self.connect_timeout = None
        self.on_connect = None
        self.disconnected = False
        self.loop_started = False
        _FakeMqttClient.instances.append(self)

    def connect(self, host, port, keepalive=60):
        self.connected_to = (host, port)
        if self.on_connect:
            self.on_connect(self, None, None, 0)
        return 0

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        self.disconnected = True

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload, qos))
        return _FakeMessageInfo()


def test_publish_bindings_removed_is_noop_and_never_touches_mqtt_when_no_removals(
    monkeypatch,
):
    def _boom(*args, **kwargs):
        raise AssertionError("mqtt.Client must not be constructed for an empty diff")

    monkeypatch.setattr(one_shot_publisher.mqtt, "Client", _boom)

    result = one_shot_publisher.publish_bindings_removed(
        "localhost", 1883, "fitrace-edge-01", []
    )

    assert result is True


def test_publish_bindings_removed_sends_the_documented_topic_and_payload(monkeypatch):
    _FakeMqttClient.instances = []
    monkeypatch.setattr(one_shot_publisher.mqtt, "Client", _FakeMqttClient)

    result = one_shot_publisher.publish_bindings_removed(
        "192.168.0.130",
        1883,
        "fitrace-edge-01",
        ["fitrace-edge-01-01", "fitrace-edge-01-02"],
    )

    assert result is True
    client = _FakeMqttClient.instances[0]
    assert client.connected_to == ("192.168.0.130", 1883)
    assert len(client.published) == 1
    topic, payload, qos = client.published[0]
    assert topic == "fitrace/nodes/fitrace-edge-01/bindings_removed"
    assert qos == 1
    assert json.loads(payload) == {
        "edge_node_id": "fitrace-edge-01",
        "removed_node_ids": ["fitrace-edge-01-01", "fitrace-edge-01-02"],
    }
    assert client.disconnected is True


def test_publish_bindings_removed_returns_false_and_never_raises_when_unreachable():
    # Bind a socket to grab a free local port, then close it immediately so
    # nothing is listening there -- connecting to it refuses fast (no need
    # to wait out a real timeout window), proving the failure path returns
    # False instead of raising and blocking the caller (a local config
    # save on a bench with no hub present must still succeed).
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()

    started = time.monotonic()
    result = one_shot_publisher.publish_bindings_removed(
        "127.0.0.1", free_port, "fitrace-edge-01", ["fitrace-edge-01-01"]
    )
    elapsed = time.monotonic() - started

    assert result is False
    # Bounded by connect_timeout: must not hang anywhere close to the
    # OS-default multi-minute TCP connect timeout.
    assert elapsed < 5.0
