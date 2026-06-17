import logging

from edge_node.domain.models import FtmsDevice
from edge_node.usecases.ftms_scanner import FTMS_SERVICE_UUID, FTMS_SERVICE_SHORT_UUID

logger = logging.getLogger("edge_node.ftms_scanner")

try:
    from bleak import BleakScanner

    BLEAK_SCANNER_AVAILABLE = True
except ImportError:
    BleakScanner = None
    BLEAK_SCANNER_AVAILABLE = False


class BleakFtmsScanner:
    async def scan(self, timeout_sec: float, adapter: str | None = None) -> list[FtmsDevice]:
        if not BLEAK_SCANNER_AVAILABLE:
            raise RuntimeError("Cannot scan BLE devices: bleak is not installed.")

        kwargs = {"timeout": timeout_sec, "return_adv": True}
        if adapter:
            kwargs["bluez"] = {"adapter": adapter}

        try:
            discovered = await BleakScanner.discover(**kwargs)
        except TypeError:
            if adapter:
                logger.warning(
                    "Bleak scanner does not support adapter selection in this environment; scanning with default adapter."
                )
            discovered = await BleakScanner.discover(timeout=timeout_sec, return_adv=True)
        except Exception as e:
            raise RuntimeError(f"BLE scan failed on adapter {adapter or 'default'}: {e}") from e

        devices: list[FtmsDevice] = []
        for device, advertisement in _iter_discovered(discovered):
            service_uuids = _normalize_service_uuids(
                getattr(advertisement, "service_uuids", None)
                or getattr(device, "metadata", {}).get("uuids", [])
            )
            matched_services = [
                uuid
                for uuid in service_uuids
                if FTMS_SERVICE_UUID in uuid or uuid.startswith(FTMS_SERVICE_SHORT_UUID)
            ]
            rssi = getattr(advertisement, "rssi", None)
            if rssi is None:
                rssi = getattr(device, "rssi", None)

            devices.append(
                FtmsDevice(
                    address=getattr(device, "address", ""),
                    name=getattr(device, "name", None),
                    rssi=rssi,
                    service_uuids=service_uuids,
                    matched_services=matched_services,
                )
            )

        return devices


def _iter_discovered(discovered):
    if isinstance(discovered, dict):
        for value in discovered.values():
            if isinstance(value, tuple) and len(value) == 2:
                yield value
        return

    for device in discovered:
        yield device, None


def _normalize_service_uuids(service_uuids) -> list[str]:
    return sorted({str(uuid).lower() for uuid in service_uuids or [] if uuid})
