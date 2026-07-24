from edge_node.main import resolve_mqtt_host


def test_auto_and_empty_resolve_to_localhost():
    assert resolve_mqtt_host("auto", 1883, probe=lambda h, p: False) == "localhost"
    assert resolve_mqtt_host("", 1883, probe=lambda h, p: False) == "localhost"
    assert resolve_mqtt_host(None, 1883, probe=lambda h, p: False) == "localhost"


def test_reachable_explicit_host_is_used_as_is():
    assert resolve_mqtt_host("fitrace-hub.local", 1883, probe=lambda h, p: True) == "fitrace-hub.local"


def test_unreachable_host_self_heals_to_localhost_when_local_broker_up():
    # configured host down, localhost up -> localhost
    def probe(host, port):
        return host in ("localhost", "127.0.0.1")

    assert resolve_mqtt_host("192.168.0.130", 1883, probe=probe) == "localhost"


def test_unreachable_host_kept_when_no_local_broker():
    # nothing reachable -> keep the configured host (distributed hub is just down)
    assert resolve_mqtt_host("192.168.0.130", 1883, probe=lambda h, p: False) == "192.168.0.130"


def test_localhost_configured_is_untouched():
    assert resolve_mqtt_host("localhost", 1883, probe=lambda h, p: False) == "localhost"
    assert resolve_mqtt_host("127.0.0.1", 1883, probe=lambda h, p: False) == "127.0.0.1"
