import os
import json
import asyncio
import logging
import signal
import socket
import time
from edge_node.domain.models import EdgeNodeConfig, EquipmentBinding
from edge_node.infrastructure.mqtt.client import AsyncMqttClient
from edge_node.adapters.mqtt_publisher import MqttPublisher
from edge_node.usecases.mock_generator import generate_mock_telemetry
from edge_node.infrastructure.ble.bleak_client import BleakTelemetryClient, BLEAK_AVAILABLE
from edge_node.usecases.multi_ftms_manager import MultiFtmsManager

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("edge_node.main")
NODE_STATUS_INTERVAL_SEC = 5.0


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

async def execute_node_shutdown():
    logger.info("Initiating system shutdown for Edge Node...")
    await asyncio.sleep(0.5)
    dry_run = os.getenv("FITRACE_POWER_COMMANDS_ENABLED") != "1"
    if dry_run:
        logger.info("[Dry Run] sudo systemctl poweroff would be executed")
    else:
        try:
            import subprocess
            subprocess.run(["sudo", "systemctl", "poweroff"], check=True, timeout=15)
        except Exception as e:
            logger.error(f"Failed to execute sudo systemctl poweroff: {e}")


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

    edge_config = _build_edge_config(config)
    node_id = edge_config.node_id
    mqtt_host = edge_config.mqtt_host
    mqtt_port = edge_config.mqtt_port

    logger.info(
        "Starting Edge Node: %s with %s FTMS binding(s)",
        node_id,
        len(edge_config.equipment_bindings),
    )

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
            
            # Subscribe to command topics
            command_topic_broadcast = "fitrace/nodes/command"
            command_topic_specific = f"fitrace/nodes/{node_id}/command"

            def on_message(client, userdata, msg):
                try:
                    payload = json.loads(msg.payload.decode("utf-8"))
                    action = payload.get("action")
                    if action == "shutdown":
                        logger.info("Received MQTT shutdown command")
                        loop.call_soon_threadsafe(
                            lambda: asyncio.create_task(execute_node_shutdown())
                        )
                except Exception as ex:
                    logger.error(f"Error handling command message: {ex}")

            mqtt_client._client.on_message = on_message
            mqtt_client._client.subscribe(command_topic_broadcast)
            mqtt_client._client.subscribe(command_topic_specific)
            logger.info("Subscribed to MQTT command topics: %s and %s", command_topic_broadcast, command_topic_specific)

        except Exception as e:
            logger.error(
                f"MQTT connection failed: {e}. Running in standalone mode (no publish)."
            )
            # In standalone mode, we still generate logs to stdout
            mqtt_client = None

        publisher = MqttPublisher(mqtt_client) if mqtt_client else None
        heartbeat_task = None
        if publisher:
            heartbeat_task = asyncio.create_task(
                _run_node_status_heartbeat(publisher, edge_config)
            )

        try:
            if edge_config.equipment_bindings and BLEAK_AVAILABLE:
                async def on_telemetry(telemetry):
                    logger.info(
                        "BLE Telemetry [%s/%s]: Speed=%skph, Dist=%sm, Power=%sW, Cadence=%srpm, HR=%sbpm",
                        telemetry.edge_node_id,
                        telemetry.node_id,
                        telemetry.instantaneous_speed_kph,
                        telemetry.distance_m,
                        telemetry.power_watts,
                        telemetry.cadence_rpm,
                        telemetry.heart_rate_bpm,
                    )
                    if publisher:
                        try:
                            topic = f"gym/telemetry/{telemetry.node_id}"
                            await publisher.publish_telemetry(topic, telemetry)
                        except Exception as e:
                            logger.error(f"Failed to publish telemetry: {e}")

                def make_client(binding, telemetry_callback):
                    return BleakTelemetryClient(
                        node_id=binding.node_id,
                        edge_node_id=edge_config.node_id,
                        equipment_id=binding.equipment_id,
                        equipment_type=binding.equipment_type,
                        target_device=binding.ble_target,
                        on_telemetry=telemetry_callback,
                    )

                ftms_manager = MultiFtmsManager(
                    edge_node_id=edge_config.node_id,
                    bindings=edge_config.equipment_bindings,
                    client_factory=make_client,
                    on_telemetry=on_telemetry,
                    max_connections=edge_config.max_ftms_connections,
                )

                try:
                    await ftms_manager.start()
                    # Run forever until cancelled
                    while True:
                        await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    logger.info("Multi-FTMS telemetry task cancelled")
                finally:
                    await ftms_manager.stop()
            else:
                if edge_config.equipment_bindings and not BLEAK_AVAILABLE:
                    logger.warning("BLE configured but bleak is not installed. Falling back to Mock generator.")

                mock_bindings = edge_config.equipment_bindings or [
                    EquipmentBinding(
                        node_id=node_id,
                        equipment_id=config.get("equipment_id", "TREAD_01"),
                        equipment_type=config.get("equipment_type", "treadmill"),
                        ble_target="mock",
                    )
                ]
                logger.info(
                    "Mock telemetry generation started for %s stream(s)",
                    len(mock_bindings),
                )

                try:
                    await asyncio.gather(
                        *(
                            _run_mock_telemetry_stream(binding, publisher)
                            for binding in mock_bindings
                        )
                    )
                except asyncio.CancelledError:
                    logger.info("Telemetry generation tasks cancelled")
        finally:
            if heartbeat_task:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
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


def _build_edge_config(config: dict) -> EdgeNodeConfig:
    if "equipment_bindings" in config:
        return EdgeNodeConfig.model_validate(config)

    ble_target = config.get("ble_target") or config.get("ble_mac") or config.get("ble_name")
    bindings = []
    if ble_target:
        bindings.append(
            {
                "node_id": config.get("telemetry_node_id") or config.get("node_id", "treadmill-01"),
                "equipment_id": config.get("equipment_id", "TREAD_01"),
                "equipment_type": config.get("equipment_type", "treadmill"),
                "ble_target": ble_target,
            }
        )

    return EdgeNodeConfig(
        node_id=config.get("edge_node_id") or config.get("node_id", "treadmill-01"),
        mqtt_host=config.get("mqtt_host", "localhost"),
        mqtt_port=config.get("mqtt_port", 1883),
        max_ftms_connections=config.get("max_ftms_connections", 5),
        available_channels=config.get("available_channels", 2),
        software_version=config.get("software_version"),
        antenna_protocol_version=config.get("antenna_protocol_version"),
        equipment_bindings=[EquipmentBinding.model_validate(binding) for binding in bindings],
    )


def _build_node_status(
    edge_config: EdgeNodeConfig,
    now_ms=None,
    hostname: str | None = None,
    ip_address: str | None = None,
) -> dict:
    now = now_ms or (lambda: int(time.time() * 1000))
    return {
        "edge_node_id": edge_config.node_id,
        "hostname": hostname or socket.gethostname(),
        "ip": ip_address or _get_lan_ip(),
        "status": "online",
        "software_version": edge_config.software_version,
        "antenna_protocol_version": edge_config.antenna_protocol_version,
        "max_ftms_connections": edge_config.max_ftms_connections,
        "available_channels": edge_config.available_channels,
        "last_seen_epoch_ms": now(),
        "equipment_streams": [
            {
                "node_id": binding.node_id,
                "equipment_id": binding.equipment_id,
                "equipment_type": binding.equipment_type,
                "status": "configured",
                "antenna_channel": binding.antenna_channel,
                "rssi": None,
                "last_telemetry_epoch_ms": None,
                "error_code": None,
            }
            for binding in edge_config.equipment_bindings
        ],
    }


async def _run_node_status_heartbeat(
    publisher: MqttPublisher,
    edge_config: EdgeNodeConfig,
    interval_sec: float = NODE_STATUS_INTERVAL_SEC,
):
    while True:
        status = _build_node_status(edge_config)
        try:
            await publisher.publish_node_status(edge_config.node_id, status)
        except Exception as e:
            logger.error("Failed to publish node status heartbeat: %s", e)
        await asyncio.sleep(interval_sec)


async def _run_mock_telemetry_stream(binding: EquipmentBinding, publisher: MqttPublisher | None):
    topic = f"gym/telemetry/{binding.node_id}"
    generator = generate_mock_telemetry(
        node_id=binding.node_id,
        equipment_id=binding.equipment_id,
        equipment_type=binding.equipment_type,
        interval_sec=0.5,
    )

    async for telemetry in generator:
        logger.info(
            "Telemetry [%s]: Speed=%skph, Dist=%sm, Power=%sW, Cadence=%srpm, HR=%sbpm",
            telemetry.node_id,
            telemetry.instantaneous_speed_kph,
            telemetry.distance_m,
            telemetry.power_watts,
            telemetry.cadence_rpm,
            telemetry.heart_rate_bpm,
        )
        if publisher:
            try:
                await publisher.publish_telemetry(topic, telemetry)
            except Exception as e:
                logger.error("Failed to publish telemetry for %s: %s", binding.node_id, e)


def _get_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


if __name__ == "__main__":
    main()
