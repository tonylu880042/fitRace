import os
import json
import asyncio
import logging
import signal
from edge_node.infrastructure.mqtt.client import AsyncMqttClient
from edge_node.adapters.mqtt_publisher import MqttPublisher
from edge_node.usecases.mock_generator import generate_mock_telemetry
from edge_node.infrastructure.ble.bleak_client import BleakTelemetryClient, BLEAK_AVAILABLE

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("edge_node.main")


async def shutdown(loop, signal=None):
    """Cleanup tasks on shutdown."""
    if signal:
        logger.info(f"Received exit signal {signal.name}...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    logger.info(f"Cancelling {len(tasks)} outstanding tasks")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()


def main():
    # Load configuration
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = json.load(f)
    else:
        config = {
            "node_id": "treadmill-01",
            "equipment_id": "TREAD_01",
            "equipment_type": "treadmill",
            "mqtt_host": "localhost",
            "mqtt_port": 1883,
        }

    node_id = config.get("node_id", "treadmill-01")
    equipment_id = config.get("equipment_id", "TREAD_01")
    equipment_type = config.get("equipment_type", "treadmill")
    mqtt_host = config.get("mqtt_host", "localhost")
    mqtt_port = config.get("mqtt_port", 1883)

    logger.info(f"Starting Edge Node: {node_id} ({equipment_type})")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Register signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                sig, lambda s=sig: asyncio.create_task(shutdown(loop, s))
            )
        except NotImplementedError:
            # Not supported on Windows, but this project runs on Linux/macOS
            pass

    async def run_app():
        client_id = f"fitrace-edge-{node_id}"
        mqtt_client = AsyncMqttClient(
            host=mqtt_host, port=mqtt_port, client_id=client_id
        )

        try:
            await mqtt_client.connect()
        except Exception as e:
            logger.error(
                f"MQTT connection failed: {e}. Running in standalone mode (no publish)."
            )
            # In standalone mode, we still generate logs to stdout
            mqtt_client = None

        publisher = MqttPublisher(mqtt_client) if mqtt_client else None

        # Topic
        topic = f"gym/telemetry/{node_id}"

        ble_target = config.get("ble_mac") or config.get("ble_name")

        if ble_target and BLEAK_AVAILABLE:
            logger.info(f"Configuring Edge Node to connect to BLE machine: {ble_target}")
            
            async def on_telemetry(telemetry):
                logger.info(
                    f"BLE Telemetry: Speed={telemetry.instantaneous_speed_kph}kph, "
                    f"Dist={telemetry.distance_m}m, Power={telemetry.power_watts}W, "
                    f"Cadence={telemetry.cadence_rpm}rpm, HR={telemetry.heart_rate_bpm}bpm"
                )
                if publisher:
                    try:
                        await publisher.publish_telemetry(topic, telemetry)
                    except Exception as e:
                        logger.error(f"Failed to publish telemetry: {e}")
            
            ble_client = BleakTelemetryClient(
                node_id=node_id,
                equipment_id=equipment_id,
                equipment_type=equipment_type,
                target_device=ble_target,
                on_telemetry=on_telemetry
            )
            
            try:
                await ble_client.start()
                # Run forever until cancelled
                while True:
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                logger.info("BLE telemetry task cancelled")
            finally:
                await ble_client.stop()
                if mqtt_client:
                    await mqtt_client.disconnect()
        else:
            if ble_target and not BLEAK_AVAILABLE:
                logger.warning("BLE configured but bleak is not installed. Falling back to Mock generator.")
            
            # Generator
            generator = generate_mock_telemetry(
                node_id=node_id,
                equipment_id=equipment_id,
                equipment_type=equipment_type,
                interval_sec=0.5,
            )

            logger.info("Mock telemetry generation task started")

            try:
                async for telemetry in generator:
                    logger.info(
                        f"Telemetry: Speed={telemetry.instantaneous_speed_kph}kph, "
                        f"Dist={telemetry.distance_m}m, Power={telemetry.power_watts}W, "
                        f"Cadence={telemetry.cadence_rpm}rpm, HR={telemetry.heart_rate_bpm}bpm"
                    )
                    if publisher:
                        try:
                            await publisher.publish_telemetry(topic, telemetry)
                        except Exception as e:
                            logger.error(f"Failed to publish telemetry: {e}")
            except asyncio.CancelledError:
                logger.info("Telemetry generation loop cancelled")
            finally:
                if mqtt_client:
                    await mqtt_client.disconnect()

    try:
        loop.create_task(run_app())
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Application interrupted by KeyboardInterrupt")
    finally:
        loop.close()
        logger.info("Edge Node stopped")


if __name__ == "__main__":
    main()
