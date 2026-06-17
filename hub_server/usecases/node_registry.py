import time

from hub_server.domain.models import EdgeNodeStatus


class NodeRegistry:
    def __init__(self, now_ms=None, offline_timeout_ms: int = 10_000):
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._offline_timeout_ms = offline_timeout_ms
        self._nodes: dict[str, EdgeNodeStatus] = {}

    def update_status(
        self, payload: dict, edge_node_id: str | None = None
    ) -> EdgeNodeStatus:
        data = dict(payload)
        if edge_node_id:
            data["edge_node_id"] = edge_node_id
        data.setdefault("last_seen_epoch_ms", self._now_ms())
        status = EdgeNodeStatus.model_validate(data)
        self._nodes[status.edge_node_id] = status
        return status

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
