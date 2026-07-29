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


def test_edge_event_log_predicate_filters_before_applying_the_trailing_window(
    tmp_path,
):
    # Regression for the scan-flood risk: a pairing scan can write dozens of
    # device-discovery "uart"/"rx" lines in a burst. Without server-side
    # filtering, those noise lines occupy slots in the trailing `limit`
    # window and can push a still-connected machine's telemetry event out of
    # it even though it is recent. A predicate must be applied while
    # scanning the log, before the maxlen window is filled, so only
    # matching events compete for the window's slots.
    event_log = EdgeEventLog(tmp_path / "edge_monitor.jsonl")

    event_log.record(
        "uart",
        "rx",
        channel="uart-1",
        message="FTMS:AA:BB:CC:DD:EE:01,BIKE,{}",
        parsed={"type": "telemetry", "address": "AA:BB:CC:DD:EE:01"},
    )
    for index in range(5):
        event_log.record(
            "uart",
            "rx",
            channel="uart-1",
            message=f"DEVICE:AA:BB:CC:DD:EE:{index:02d},-40,Bike,BIKE",
            parsed={"type": "device", "address": f"AA:BB:CC:DD:EE:{index:02d}"},
        )

    def telemetry_only(event: dict) -> bool:
        parsed = event.get("parsed") or {}
        return parsed.get("type") == "telemetry"

    events = event_log.list_events(limit=1, predicate=telemetry_only)

    assert len(events) == 1
    assert events[0]["parsed"]["type"] == "telemetry"
    assert events[0]["parsed"]["address"] == "AA:BB:CC:DD:EE:01"
