"""Reconciliation decision for an Edge's `bindings_removed` MQTT event.

Deliberately broker-free and framework-free: this module takes plain data
(a list of removed node_ids, the current station -> node_id mapping, and
the current race state) and returns which station numbers should be
unassigned. The MQTT subscription/parsing lives in
hub_server/adapters/mqtt_subscriber.py; that adapter is the only caller
that owns a broker connection or talks to RaceManager/WebSocketManager.

The trigger is deliberately an explicit one-shot event from an operator
action on the Edge (see edge_node's config-save hook), never inferred from
a missing/empty heartbeat -- field conditions cause edges to drop off
constantly, and treating a dropped heartbeat as "equipment removed" would
wipe venue setup on every Wi-Fi hiccup.
"""

from hub_server.domain.models import RaceState


def stations_to_unassign_for_removed_bindings(
    removed_node_ids: list[str],
    stations: dict[int, str],
    race_state: RaceState,
) -> list[int]:
    """Decide which station numbers to unassign after an equipment removal.

    :param removed_node_ids: node_ids whose Edge-side bindings were just
        removed by an explicit operator action.
    :param stations: current station_number -> node_id mapping.
    :param race_state: the Hub's current race state.
    :return: sorted station numbers to unassign. Empty while RUNNING --
        mid-race unassignment would corrupt the leaderboard, and
        RaceManager.assign_station already refuses to mutate stations in
        that state. A removed node_id with no matching station is silently
        skipped: that is a no-op, not an error.
    """
    if race_state == RaceState.RUNNING:
        return []

    removed = set(removed_node_ids)
    return sorted(
        station_number
        for station_number, node_id in stations.items()
        if node_id in removed
    )
