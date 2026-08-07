from edge_node.domain.models import EquipmentBinding
from edge_node.usecases.binding_removal import diff_removed_binding_node_ids


def _binding(node_id: str, ble_target: str = "AA:BB:CC:DD:EE:01") -> EquipmentBinding:
    return EquipmentBinding(
        node_id=node_id,
        equipment_id=f"{node_id}-eq",
        equipment_type="fan_bike",
        ble_target=ble_target,
        antenna_channel="uart-1",
    )


def test_diff_returns_node_ids_present_before_but_missing_after():
    previous = [_binding("fitrace-edge-01-01"), _binding("fitrace-edge-01-02")]
    new = [_binding("fitrace-edge-01-01")]

    removed = diff_removed_binding_node_ids(previous, new)

    assert removed == ["fitrace-edge-01-02"]


def test_diff_is_empty_when_a_binding_is_added_not_removed():
    previous = [_binding("fitrace-edge-01-01")]
    new = [_binding("fitrace-edge-01-01"), _binding("fitrace-edge-01-02")]

    removed = diff_removed_binding_node_ids(previous, new)

    assert removed == []


def test_diff_is_empty_when_bindings_are_unchanged():
    previous = [_binding("fitrace-edge-01-01")]
    new = [_binding("fitrace-edge-01-01")]

    removed = diff_removed_binding_node_ids(previous, new)

    assert removed == []


def test_diff_reports_every_node_id_when_all_bindings_are_cleared():
    previous = [_binding("fitrace-edge-01-01"), _binding("fitrace-edge-01-02")]
    new = []

    removed = diff_removed_binding_node_ids(previous, new)

    assert sorted(removed) == ["fitrace-edge-01-01", "fitrace-edge-01-02"]


def test_diff_of_empty_previous_and_new_is_empty():
    assert diff_removed_binding_node_ids([], []) == []
