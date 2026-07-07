"""Local Hub launcher used only by the browser demo recorder.

It supplies fresh Edge status messages so the same readiness checks used in
production can approve the simulated race before the recorder starts it.
"""

import asyncio
import contextlib
import os
import time

import uvicorn
from contextlib import asynccontextmanager

from hub_server.infrastructure.fastapi.app import app, node_registry


DEMO_EDGES = (
    ("fitrace-edge-01", "192.168.0.141", (("fitrace-edge-01-bike-01", "fan_bike"), ("fitrace-edge-01-bike-02", "fan_bike"))),
    ("fitrace-edge-02", "192.168.0.142", (("fitrace-edge-02-row-01", "rower"), ("fitrace-edge-02-ski-01", "skierg"))),
    ("fitrace-edge-03", "192.168.0.143", (("fitrace-edge-03-tread-01", "treadmill"), ("fitrace-edge-03-bike-01", "fan_bike"))),
)


def publish_demo_statuses():
    now_ms = int(time.time() * 1000)
    for edge_node_id, ip, streams in DEMO_EDGES:
        node_registry.update_status(
            {
                "edge_node_id": edge_node_id,
                "hostname": edge_node_id,
                "ip": ip,
                "status": "online",
                "software_version": "demo",
                "last_seen_epoch_ms": now_ms,
                "equipment_streams": [
                    {
                        "node_id": node_id,
                        "equipment_id": "_".join(node_id.rsplit("-", 2)[-2:]).upper(),
                        "equipment_type": equipment_type,
                        "status": "configured",
                        "last_telemetry_epoch_ms": now_ms,
                    }
                    for node_id, equipment_type in streams
                ],
            }
        )


async def refresh_demo_statuses():
    while True:
        publish_demo_statuses()
        await asyncio.sleep(1)


original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def demo_lifespan(application):
    async with original_lifespan(application):
        publish_demo_statuses()
        task = asyncio.create_task(refresh_demo_statuses())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app.router.lifespan_context = demo_lifespan


if __name__ == "__main__":
    if os.getenv("TESTING") != "1":
        raise SystemExit("demo_hub.py is only available with TESTING=1")
    uvicorn.run(app, host="127.0.0.1", port=8010)
