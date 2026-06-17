import pytest

from edge_node.domain.models import FtmsDevice
from edge_node.usecases.ftms_scanner import scan_ftms_devices


class FakeScanner:
    def __init__(self, devices):
        self.devices = devices
        self.calls = []

    async def scan(self, timeout_sec: float, adapter: str | None = None):
        self.calls.append({"timeout_sec": timeout_sec, "adapter": adapter})
        return self.devices


@pytest.mark.asyncio
async def test_scan_ftms_devices_filters_non_ftms_devices():
    ftms = FtmsDevice(
        address="AA:BB:CC:DD:EE:01",
        name="Bike 01",
        rssi=-52,
        service_uuids=["00001826-0000-1000-8000-00805f9b34fb"],
        matched_services=["00001826-0000-1000-8000-00805f9b34fb"],
    )
    non_ftms = FtmsDevice(
        address="AA:BB:CC:DD:EE:02",
        name="Speaker",
        rssi=-61,
        service_uuids=["0000180a-0000-1000-8000-00805f9b34fb"],
    )
    scanner = FakeScanner([ftms, non_ftms])

    devices = await scan_ftms_devices(scanner, timeout_sec=4.0, adapter="hci1")

    assert devices == [ftms]
    assert scanner.calls == [{"timeout_sec": 4.0, "adapter": "hci1"}]


@pytest.mark.asyncio
async def test_scan_ftms_devices_can_include_all_ble_devices():
    devices = [
        FtmsDevice(address="AA:BB:CC:DD:EE:01", name="Bike 01"),
        FtmsDevice(address="AA:BB:CC:DD:EE:02", name="Speaker"),
    ]
    scanner = FakeScanner(devices)

    result = await scan_ftms_devices(scanner, include_all=True)

    assert result == devices


@pytest.mark.asyncio
async def test_scan_ftms_devices_rejects_invalid_timeout():
    scanner = FakeScanner([])

    with pytest.raises(ValueError, match="timeout_sec"):
        await scan_ftms_devices(scanner, timeout_sec=0)
