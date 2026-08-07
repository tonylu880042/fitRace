"""Detect which equipment_binding node_ids disappeared between two configs.

Plain data in, plain data out -- no MQTT, no FastAPI. This single diff
covers every config-save entry point (POST /api/config, DELETE
/api/equipment-bindings, DELETE /api/equipment-bindings/{node_id}, the
pairing-session flow) because all of them funnel through the one
save_edge_config() call in edge_node/infrastructure/fastapi/app.py, which
is where this function is called from.
"""

from typing import Iterable


def diff_removed_binding_node_ids(
    previous_bindings: Iterable, new_bindings: Iterable
) -> list[str]:
    """Return node_ids present in `previous_bindings` but absent from
    `new_bindings`, sorted for deterministic ordering. Bindings that were
    only added are not reported -- this is a removal diff, not a symmetric
    change diff."""
    previous_node_ids = {binding.node_id for binding in previous_bindings}
    new_node_ids = {binding.node_id for binding in new_bindings}
    return sorted(previous_node_ids - new_node_ids)
