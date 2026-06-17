import pytest

from edge_node.domain.models import EdgeNodeConfig, EquipmentBinding, TelemetryData
from edge_node.usecases.multi_ftms_manager import MultiFtmsManager


class FakeTelemetryClient:
    started = []
    stopped = []

    def __init__(self, binding, callback):
        self.binding = binding
        self.callback = callback

    async def start(self):
        self.started.append(self.binding.node_id)

    async def stop(self):
        self.stopped.append(self.binding.node_id)


def make_binding(index: int) -> EquipmentBinding:
    return EquipmentBinding(
        node_id=f"fitrace-edge-01-{index:02d}",
        equipment_id=f"BIKE_{index:02d}",
        equipment_type="fan_bike",
        ble_target=f"BIKE_{index:02d}_TARGET",
    )


def test_edge_node_config_allows_up_to_ten_ftms_bindings():
    config = EdgeNodeConfig(
        node_id="fitrace-edge-01",
        max_ftms_connections=10,
        equipment_bindings=[make_binding(index) for index in range(1, 11)],
    )

    assert config.max_ftms_connections == 10
    assert len(config.equipment_bindings) == 10


def test_edge_node_config_rejects_more_than_ten_ftms_bindings():
    with pytest.raises(ValueError, match="equipment_bindings"):
        EdgeNodeConfig(
            node_id="fitrace-edge-01",
            max_ftms_connections=10,
            equipment_bindings=[make_binding(index) for index in range(1, 12)],
        )


@pytest.mark.asyncio
async def test_multi_ftms_manager_starts_one_client_per_binding():
    FakeTelemetryClient.started = []
    FakeTelemetryClient.stopped = []
    received = []
    bindings = [make_binding(1), make_binding(2)]

    async def on_telemetry(telemetry):
        received.append(telemetry)

    manager = MultiFtmsManager(
        edge_node_id="fitrace-edge-01",
        bindings=bindings,
        client_factory=lambda binding, callback: FakeTelemetryClient(binding, callback),
        on_telemetry=on_telemetry,
    )

    await manager.start()
    await manager._handle_telemetry(
        TelemetryData(
            node_id="fitrace-edge-01-01",
            equipment_id="BIKE_01",
            equipment_type="fan_bike",
            timestamp_epoch_ms=1,
        )
    )
    await manager.stop()

    assert FakeTelemetryClient.started == ["fitrace-edge-01-01", "fitrace-edge-01-02"]
    assert FakeTelemetryClient.stopped == ["fitrace-edge-01-01", "fitrace-edge-01-02"]
    assert received[0].edge_node_id == "fitrace-edge-01"
