from edge_node.main import resolve_mqtt_host


def test_auto_and_empty_resolve_to_localhost():
    assert resolve_mqtt_host("auto") == "localhost"
    assert resolve_mqtt_host("") == "localhost"
    assert resolve_mqtt_host(None) == "localhost"


def test_reachable_explicit_host_is_used_as_is():
    assert resolve_mqtt_host("fitrace-hub.local") == "fitrace-hub.local"


def test_explicit_host_never_silently_falls_back_to_localhost():
    # An explicitly selected remote Hub is authoritative. Falling back to a
    # different local broker can make the Edge appear healthy while publishing
    # to the wrong Central Hub.
    assert resolve_mqtt_host("192.168.0.130") == "192.168.0.130"


def test_explicit_host_is_kept_when_distributed_hub_is_down():
    # Reachability is handled by the MQTT client, not host resolution.
    assert resolve_mqtt_host("192.168.0.130") == "192.168.0.130"


def test_localhost_configured_is_untouched():
    assert resolve_mqtt_host("localhost") == "localhost"
    assert resolve_mqtt_host("127.0.0.1") == "127.0.0.1"
