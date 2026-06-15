import asyncio
import logging
import time
from typing import Callable, Coroutine, Optional
from edge_node.domain.models import TelemetryData
from edge_node.usecases.ble_ftms_parser import parse_ftms

logger = logging.getLogger("edge_node.bleak_client")

try:
    from bleak import BleakClient, BleakScanner
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False
    logger.warning("Bleak library not installed. BLE connectivity will be unavailable.")

# Standard FTMS Characteristic UUIDs
CHAR_INDOOR_BIKE = "00002ad2-0000-1000-8000-00805f9b34fb"
CHAR_TREADMILL = "00002acd-0000-1000-8000-00805f9b34fb"
CHAR_ROWER = "00002ad1-0000-1000-8000-00805f9b34fb"

EQUIPMENT_TO_UUID = {
    "treadmill": CHAR_TREADMILL,
    "fan_bike": CHAR_INDOOR_BIKE,
    "indoor_bike": CHAR_INDOOR_BIKE,
    "rowing_machine": CHAR_ROWER,
    "rower": CHAR_ROWER,
    "ski_erg": CHAR_ROWER,  # Fallback to rower
}

class BleakTelemetryClient:
    def __init__(
        self,
        node_id: str,
        equipment_id: str,
        equipment_type: str,
        target_device: str,  # MAC address or Bluetooth Name
        on_telemetry: Callable[[TelemetryData], Coroutine[None, None, None]]
    ):
        self._node_id = node_id
        self._equipment_id = equipment_id
        self._equipment_type = equipment_type.lower()
        self._target_device = target_device
        self._on_telemetry = on_telemetry

        self._client: Optional[BleakClient] = None
        self._connected = False
        self._should_run = True
        self._reconnect_task: Optional[asyncio.Task] = None

        self._char_uuid = EQUIPMENT_TO_UUID.get(self._equipment_type, CHAR_INDOOR_BIKE)

    async def start(self):
        if not BLEAK_AVAILABLE:
            raise RuntimeError("Cannot start BLE client: bleak is not installed.")
        
        self._should_run = True
        logger.info(f"Starting BLE Client for device: {self._target_device}")
        asyncio.create_task(self._connect_loop())

    async def stop(self):
        self._should_run = False
        if self._reconnect_task:
            self._reconnect_task.cancel()
        if self._client:
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting BLE client: {e}")
        self._connected = False
        logger.info("BLE Client stopped")

    async def _connect_loop(self):
        while self._should_run:
            if not self._connected:
                try:
                    await self._connect()
                except Exception as e:
                    logger.error(f"Failed to connect to BLE device {self._target_device}: {e}. Retrying in 5 seconds...")
                    await asyncio.sleep(5.0)
            else:
                await asyncio.sleep(1.0)

    async def _connect(self):
        device_address = self._target_device
        
        # If it doesn't look like a MAC address, scan for device name
        # MAC format validation helper (rough check for colons or dashes)
        is_mac = ":" in device_address or "-" in device_address
        if not is_mac:
            logger.info(f"Scanning for BLE device with name: {device_address}")
            device = await BleakScanner.find_device_by_filter(
                lambda d, ad: d.name and device_address.lower() in d.name.lower()
            )
            if not device:
                raise ConnectionError(f"Could not find device named {device_address}")
            device_address = device.address
            logger.info(f"Found device address: {device_address}")

        logger.info(f"Connecting to {device_address}...")
        self._client = BleakClient(
            device_address,
            disconnected_callback=self._on_disconnected
        )
        await self._client.connect()
        logger.info("BLE Connected. Subscribing to notifications...")
        
        # Start notification
        await self._client.start_notify(self._char_uuid, self._notification_handler)
        self._connected = True
        logger.info(f"Successfully subscribed to {self._char_uuid}")

    def _on_disconnected(self, client):
        logger.warning(f"BLE Device {self._target_device} disconnected!")
        self._connected = False

    def _notification_handler(self, sender, data: bytes):
        try:
            parsed = parse_ftms(self._char_uuid, data)
            
            # Map values to TelemetryData structure
            telemetry = TelemetryData(
                node_id=self._node_id,
                equipment_id=self._equipment_id,
                equipment_type=self._equipment_type,
                instantaneous_speed_kph=parsed.get("speed_kph", 0.0),
                cadence_rpm=parsed.get("cadence_rpm", 0),
                power_watts=parsed.get("power_watts", 0),
                heart_rate_bpm=parsed.get("heart_rate_bpm", 0),
                distance_m=parsed.get("distance_m", 0.0),
                elapsed_time_ms=parsed.get("elapsed_time_sec", 0) * 1000,
                timestamp_epoch_ms=int(time.time() * 1000)
            )
            
            # Fire the callback (run asynchronously)
            asyncio.create_task(self._on_telemetry(telemetry))
        except Exception as e:
            logger.error(f"Error handling BLE notification data: {e}")
