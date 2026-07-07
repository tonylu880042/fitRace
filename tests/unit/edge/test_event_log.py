from edge_node.usecases.event_log import EdgeEventLog


def test_edge_event_log_records_and_lists_recent_events(tmp_path):
    event_log = EdgeEventLog(tmp_path / "edge_monitor.jsonl")

    event_log.record("uart", "tx", channel="uart-1", message="PING;")
    event_log.record(
        "mqtt",
        "publish",
        topic="gym/telemetry/node-01",
        payload={"node_id": "node-01", "distance_m": 12.3},
    )

    events = event_log.list_events(limit=10)

    assert len(events) == 2
    assert events[0]["source"] == "uart"
    assert events[0]["direction"] == "tx"
    assert events[0]["message"] == "PING;"
    assert events[1]["source"] == "mqtt"
    assert events[1]["topic"] == "gym/telemetry/node-01"
    assert events[1]["payload"]["node_id"] == "node-01"


def test_edge_event_log_limits_payload_size(tmp_path):
    event_log = EdgeEventLog(tmp_path / "edge_monitor.jsonl", max_payload_chars=20)

    event_log.record("mqtt", "publish", payload={"large": "x" * 200})

    event = event_log.list_events(limit=1)[0]
    assert event["payload"]["truncated"] is True
    assert len(event["payload"]["text"]) == 20
