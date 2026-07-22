import time

from hub_server.domain.models import EdgeNodeStatus


class NodeRegistry:
    def __init__(self, now_ms=None, offline_timeout_ms: int = 10_000):
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._offline_timeout_ms = offline_timeout_ms
        self._nodes: dict[str, EdgeNodeStatus] = {}

    # Live per-stream fields come from telemetry, not the heartbeat, so a
    # heartbeat's config-only stream list must not clobber them.
    _LIVE_STREAM_FIELDS = ("last_telemetry_epoch_ms", "rssi", "ftms_type")

    def update_status(
        self, payload: dict, edge_node_id: str | None = None
    ) -> EdgeNodeStatus:
        data = dict(payload)
        if edge_node_id:
            data["edge_node_id"] = edge_node_id
        data.setdefault("last_seen_epoch_ms", self._now_ms())
        self._preserve_live_stream_fields(data)
        status = EdgeNodeStatus.model_validate(data)
        self._nodes[status.edge_node_id] = status
        return status

    def _preserve_live_stream_fields(self, data: dict) -> None:
        """Carry forward telemetry-derived stream fields across a heartbeat.

        A status heartbeat replaces the whole node, and its equipment_streams
        are the configured bindings only (no last_telemetry_epoch_ms). Without
        this merge every heartbeat would blank the streams' live fields, making
        the console flicker between "connected" and "no data".
        """
        streams = data.get("equipment_streams")
        if not isinstance(streams, list):
            return
        existing = self._nodes.get(data.get("edge_node_id"))
        if existing is None:
            return
        previous_by_id = {
            s.get("node_id"): s
            for s in existing.model_dump().get("equipment_streams", [])
            if isinstance(s, dict) and s.get("node_id")
        }
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            prev = previous_by_id.get(stream.get("node_id"))
            if not prev:
                continue
            for field in self._LIVE_STREAM_FIELDS:
                if stream.get(field) is None and prev.get(field) is not None:
                    stream[field] = prev[field]

    def update_telemetry(self, payload: dict) -> EdgeNodeStatus | None:
        node_id = payload.get("node_id")
        edge_node_id = payload.get("edge_node_id")
        if not node_id or not edge_node_id:
            return None

        now_ms = self._now_ms()
        status = self._nodes.get(edge_node_id)
        if status is None:
            status = EdgeNodeStatus(
                edge_node_id=edge_node_id,
                status="online",
                last_seen_epoch_ms=now_ms,
                equipment_streams=[],
            )

        data = status.model_dump()
        data["status"] = "online"
        data["last_seen_epoch_ms"] = now_ms
        streams = data.setdefault("equipment_streams", [])
        stream = next(
            (item for item in streams if item.get("node_id") == node_id),
            None,
        )
        if stream is None:
            stream = {"node_id": node_id}
            streams.append(stream)

        stream.update(
            {
                "node_id": node_id,
                "equipment_id": payload.get("equipment_id") or stream.get("equipment_id"),
                "equipment_type": payload.get("equipment_type") or stream.get("equipment_type"),
                "mac_address": payload.get("mac_address") or stream.get("mac_address"),
                "rssi": payload.get("rssi"),
                "status": "online",
                "last_telemetry_epoch_ms": payload.get("timestamp_epoch_ms") or now_ms,
            }
        )
        if payload.get("ftms_type"):
            stream["ftms_type"] = payload.get("ftms_type")

        updated = EdgeNodeStatus.model_validate(data)
        self._nodes[updated.edge_node_id] = updated
        return updated

    def list_nodes(self) -> list[dict]:
        now_ms = self._now_ms()
        nodes = []
        for status in sorted(self._nodes.values(), key=lambda item: item.edge_node_id):
            data = status.model_dump()
            if now_ms - status.last_seen_epoch_ms > self._offline_timeout_ms:
                data["status"] = "offline"
            nodes.append(data)
        return nodes

    def clear(self):
        self._nodes.clear()
