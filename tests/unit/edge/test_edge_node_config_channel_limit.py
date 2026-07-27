import pytest
from pydantic import ValidationError

from edge_node.domain.models import (
    EdgeNodeConfig,
    EquipmentBinding,
    MAX_CONNECTIONS_PER_CHANNEL,
)


def make_binding(index: int, antenna_channel: str) -> EquipmentBinding:
    return EquipmentBinding(
        node_id=f"fitrace-edge-01-{index:02d}",
        equipment_id=f"BIKE_{index:02d}",
        equipment_type="fan_bike",
        ble_target=f"AA:BB:CC:DD:EE:{index:02d}",
        antenna_channel=antenna_channel,
    )


def test_max_connections_per_channel_constant_matches_nrf52832_board_limit():
    assert MAX_CONNECTIONS_PER_CHANNEL == 3


def test_config_rejects_four_bindings_on_a_single_channel():
    bindings = [make_binding(i, "uart-1") for i in range(1, 5)]

    with pytest.raises(ValidationError, match="uart-1"):
        EdgeNodeConfig(
            node_id="fitrace-edge-01",
            max_ftms_connections=10,
            equipment_bindings=bindings,
        )


def test_config_accepts_three_bindings_on_a_single_channel():
    bindings = [make_binding(i, "uart-1") for i in range(1, 4)]

    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        max_ftms_connections=10,
        equipment_bindings=bindings,
    )

    assert len(config.equipment_bindings) == 3


def test_config_accepts_four_bindings_spread_across_two_channels():
    bindings = [make_binding(i, "uart-1") for i in range(1, 3)] + [
        make_binding(i, "uart-2") for i in range(3, 5)
    ]

    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        max_ftms_connections=10,
        equipment_bindings=bindings,
    )

    assert len(config.equipment_bindings) == 4


def test_bindings_without_antenna_channel_are_not_counted_toward_the_limit():
    bindings = [make_binding(i, "uart-1") for i in range(1, 4)]
    unassigned = EquipmentBinding(
        node_id="fitrace-edge-01-unassigned",
        equipment_id="BIKE_UNASSIGNED",
        equipment_type="fan_bike",
        ble_target="AA:BB:CC:DD:EE:99",
        antenna_channel=None,
    )

    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        max_ftms_connections=10,
        equipment_bindings=bindings + [unassigned],
    )

    assert len(config.equipment_bindings) == 4
