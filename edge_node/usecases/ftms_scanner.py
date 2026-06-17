from edge_node.domain.models import FtmsDevice


FTMS_SERVICE_UUID = "00001826-0000-1000-8000-00805f9b34fb"
FTMS_SERVICE_SHORT_UUID = "1826"


def is_ftms_device(device: FtmsDevice) -> bool:
    return any(
        FTMS_SERVICE_UUID in service_uuid.lower()
        or service_uuid.lower().startswith(FTMS_SERVICE_SHORT_UUID)
        for service_uuid in device.service_uuids
    )


async def scan_ftms_devices(
    scanner,
    timeout_sec: float = 5.0,
    adapter: str | None = None,
    include_all: bool = False,
) -> list[FtmsDevice]:
    if timeout_sec <= 0:
        raise ValueError("timeout_sec must be greater than 0")
    if timeout_sec > 30:
        raise ValueError("timeout_sec must be less than or equal to 30")
    if adapter is not None and not adapter.strip():
        raise ValueError("adapter cannot be blank")

    devices = await scanner.scan(timeout_sec=timeout_sec, adapter=adapter)
    if include_all:
        return devices
    return [device for device in devices if is_ftms_device(device)]
