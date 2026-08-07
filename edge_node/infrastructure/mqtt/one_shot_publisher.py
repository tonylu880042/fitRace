"""Best-effort, short-lived MQTT publish for the bindings_removed event.

The config-save API (fitracestudio-edge-web-config.service, uvicorn on the
FastAPI app in edge_node/infrastructure/fastapi/app.py) is a DIFFERENT
process from the long-lived runtime (fitracestudio-edge.service,
`python -m edge_node.main`) that owns the persistent MQTT connection
(edge_node/infrastructure/mqtt/client.py + adapters/mqtt_publisher.py). The
API process has no MQTT client to borrow, so this opens and tears down its
own short connection for exactly one publish.

Best-effort by design: a technician clearing bindings on a bench with no
Central Hub present must not be blocked. Every failure is caught and
logged; this function must never raise.
"""

import json
import logging
import threading
import time

import paho.mqtt.client as mqtt

logger = logging.getLogger("edge_node.mqtt_one_shot_publisher")

CONNECT_TIMEOUT_SEC = 2.0
PUBLISH_TIMEOUT_SEC = 2.0


def publish_bindings_removed(
    mqtt_host: str,
    mqtt_port: int,
    edge_node_id: str,
    removed_node_ids: list[str],
) -> bool:
    """Publish fitrace/nodes/{edge_node_id}/bindings_removed with
    {"edge_node_id": ..., "removed_node_ids": [...]}.

    :return: True if the publish is believed to have been delivered (or
        there was nothing to publish), False on any failure. Never raises.
    """
    if not removed_node_ids:
        return True

    topic = f"fitrace/nodes/{edge_node_id}/bindings_removed"
    payload = json.dumps(
        {"edge_node_id": edge_node_id, "removed_node_ids": list(removed_node_ids)}
    )

    connected = threading.Event()
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"fitrace-edge-config-{edge_node_id}",
    )
    client.connect_timeout = CONNECT_TIMEOUT_SEC

    def _on_connect(c, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "value", reason_code) == 0:
            connected.set()

    client.on_connect = _on_connect

    try:
        client.connect(mqtt_host, mqtt_port, keepalive=10)
        client.loop_start()
        if not connected.wait(timeout=CONNECT_TIMEOUT_SEC):
            raise ConnectionError(
                f"Timed out connecting to MQTT broker at {mqtt_host}:{mqtt_port}"
            )

        info = client.publish(topic, payload, qos=1)
        deadline = time.monotonic() + PUBLISH_TIMEOUT_SEC
        while not info.is_published() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not info.is_published():
            raise ConnectionError("Timed out publishing bindings_removed event")
        return True
    except Exception as exc:
        logger.warning(
            "Failed to publish bindings_removed for edge_node_id=%s "
            "removed_node_ids=%s: %s",
            edge_node_id,
            removed_node_ids,
            exc,
        )
        return False
    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
