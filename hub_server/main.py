import uvicorn
import asyncio
import logging
from hub_server.infrastructure.mqtt.client import AsyncMqttClient
from hub_server.adapters.mqtt_subscriber import MqttSubscriber
from hub_server.infrastructure.fastapi.app import (
    app,
    race_manager,
    ws_manager,
    node_registry,
    race_event_engine,
    race_result_store,
    hyrox_manager,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("hub_server.main")


async def main_async():
    mqtt_host = "localhost"
    mqtt_port = 1883
    app_host = "0.0.0.0"
    app_port = 8000

    # Initialize and connect MQTT
    mqtt_client = AsyncMqttClient(
        host=mqtt_host, port=mqtt_port, client_id="fitrace-hub-server"
    )
    app.state.mqtt_client = mqtt_client
    subscriber = None
    try:
        await mqtt_client.connect()
        subscriber = MqttSubscriber(
            mqtt_client,
            race_manager,
            ws_manager,
            node_registry,
            race_event_engine,
            race_result_store,
            hyrox_manager=hyrox_manager,
        )
        subscriber.start_listening()
    except Exception as e:
        logger.error(
            f"Failed to connect to MQTT broker: {e}. Running in standalone mode (WebSocket/REST API only)."
        )
        mqtt_client = None

    # Run Uvicorn server concurrently in the same asyncio event loop
    config = uvicorn.Config(app, host=app_host, port=app_port, log_level="info")
    server = uvicorn.Server(config)

    logger.info(f"Starting Hub Web Server on http://{app_host}:{app_port}")
    server_task = asyncio.create_task(server.serve())

    try:
        await server_task
    except asyncio.CancelledError:
        logger.info("Server execution cancelled")
    finally:
        if mqtt_client:
            try:
                await mqtt_client.disconnect()
            except Exception as e:
                logger.error(f"Error during MQTT disconnect: {e}")


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Hub Server stopped by user interrupt")
    except Exception as e:
        logger.critical(f"Hub Server crashed: {e}")


if __name__ == "__main__":
    main()
