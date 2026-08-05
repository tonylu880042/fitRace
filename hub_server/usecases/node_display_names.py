from copy import deepcopy


def build_stream_display_catalog(nodes: list[dict]) -> dict[str, str]:
    return {
        stream["node_id"]: (
            stream.get("display_name")
            or stream.get("equipment_id")
            or stream["node_id"]
        )
        for edge_node in nodes
        for stream in edge_node.get("equipment_streams", [])
        if stream.get("node_id")
    }


def _build_stream_equipment_catalog(nodes: list[dict]) -> dict[str, str | None]:
    """Map node_id -> raw equipment_id (the BLE name), independent of the
    display_name rung, so callers can expose it as a middle fallback rung of
    their own (e.g. client-side rendering) without recomputing it."""
    return {
        stream["node_id"]: stream.get("equipment_id")
        for edge_node in nodes
        for stream in edge_node.get("equipment_streams", [])
        if stream.get("node_id")
    }


def enrich_progress_display_names(
    progress: dict,
    nodes: list[dict],
) -> dict:
    enriched = deepcopy(progress)
    node_labels = build_stream_display_catalog(nodes)
    for participant in enriched.values():
        node_id = participant.get("node_id")
        participant["node_display_name"] = node_labels.get(node_id, node_id)
    return enriched


def enrich_race_state_display_names(
    state_data: dict,
    nodes: list[dict],
) -> dict:
    enriched = deepcopy(state_data)
    node_labels = build_stream_display_catalog(nodes)

    for participant in enriched.get("leaderboard", {}).values():
        node_id = participant.get("node_id")
        participant["node_display_name"] = node_labels.get(node_id, node_id)

    for team in enriched.get("team_leaderboard", []):
        for member in team.get("members", []):
            node_id = member.get("node_id")
            member["node_display_name"] = node_labels.get(node_id, node_id)

    return enriched


def enrich_station_display_names(
    stations_status: dict,
    nodes: list[dict],
) -> dict:
    enriched = deepcopy(stations_status)
    node_labels = build_stream_display_catalog(nodes)
    equipment_ids = _build_stream_equipment_catalog(nodes)

    for station in enriched.get("stations", {}).values():
        node_id = station.get("node_id")
        station["node_display_name"] = node_labels.get(node_id, node_id)
        station["equipment_id"] = equipment_ids.get(node_id)

    enriched["unassigned_node_display_names"] = {
        node_id: node_labels.get(node_id, node_id)
        for node_id in enriched.get("unassigned_nodes", [])
    }
    enriched["unassigned_node_equipment_ids"] = {
        node_id: equipment_ids.get(node_id)
        for node_id in enriched.get("unassigned_nodes", [])
    }
    return enriched
