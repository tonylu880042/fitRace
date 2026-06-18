import asyncio
import os
import base64
import logging
import subprocess
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from hub_server.domain.models import RaceState, RaceConfig
from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.node_registry import NodeRegistry
from hub_server.usecases.race_event_engine import RaceEventEngine
from hub_server.adapters.websocket_manager import WebSocketManager
from hub_server.infrastructure.locales import DEFAULT_LOCALE, list_locales, load_locale
from hub_server.usecases.update_checker import UpdateChecker
from fitrace_common.power_manager import PowerActionError, PowerManager
from fitrace_common.version import APP_VERSION

logger = logging.getLogger("hub_server.fastapi")

DEFAULT_UPDATE_PUBLIC_KEY_PATH = str(
    Path(__file__).resolve().parents[3]
    / "fitrace_common"
    / "release-ed25519-public.pem"
)
HUB_UPDATER_SERVICE = os.getenv(
    "FITRACE_HUB_UPDATER_SERVICE", "fitracestudio-hub-updater.service"
)
MAX_AVATAR_BYTES = 256 * 1024


def run_systemctl(command: list[str]):
    subprocess.run(command, check=True, timeout=15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if (
        os.getenv("FITRACE_UPDATE_AUTO_CHECK", "1") != "0"
        and update_checker.manifest_url
    ):
        asyncio.create_task(asyncio.to_thread(update_checker.check))
    yield


app = FastAPI(title="FitRaceStudio Central Hub", lifespan=lifespan)


@app.middleware("http")
async def add_no_cache_header(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith(".html") or path == "/" or path.endswith("/"):
        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Global instances (Shared Context)
race_manager = RaceManager()
ws_manager = WebSocketManager()
node_registry = NodeRegistry()
race_event_engine = RaceEventEngine()
update_checker = UpdateChecker(
    manifest_url=os.getenv(
        "FITRACE_UPDATE_MANIFEST_URL",
        "https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json",
    ),
    signature_url=os.getenv(
        "FITRACE_UPDATE_SIGNATURE_URL",
        "https://dd9tnec1hh2ts.cloudfront.net/channels/stable/manifest.json.sig",
    ),
    current_version=APP_VERSION,
    public_key_path=os.getenv(
        "FITRACE_UPDATE_PUBLIC_KEY_PATH", DEFAULT_UPDATE_PUBLIC_KEY_PATH
    ),
    cache_dir=os.getenv("FITRACE_UPDATE_CACHE_DIR", "/tmp/fitrace-update-cache"),
)
power_manager = PowerManager(
    target="hub",
    service_name="fitracestudio-hub.service",
    action_allowed=lambda: race_manager.get_state() == RaceState.IDLE,
    blocked_message=lambda: (
        "Power action is allowed only when race state is IDLE; "
        f"current state is {race_manager.get_state().value}"
    ),
)


class ConfigurePayload(BaseModel):
    race_type: str
    target_value: float = 0.0
    duration_sec: int = 0


class AssignStationPayload(BaseModel):
    station_number: int
    node_id: Optional[str] = None


class RegisterAthletePayload(BaseModel):
    station_number: int
    athlete_name: str
    team_name: Optional[str] = None
    avatar_base64: Optional[str] = None


class PowerActionPayload(BaseModel):
    confirmation: Optional[str] = None


class DiagnosticTelemetryPayload(BaseModel):
    node_id: str = "diagnostic-bike-01"
    equipment_type: str = "fan_bike"
    distance_m: float = 25.0
    elapsed_time_ms: int = 5000
    instantaneous_speed_kph: float = 18.0
    cadence_rpm: int = 75
    power_watts: int = 180


def get_real_ip() -> Optional[str]:
    import socket

    # 1. Try UDP socket trick to get interface routing to external IP
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and ip != "127.0.0.1":
                return ip
    except Exception:
        pass

    # 2. Try socket.getaddrinfo / gethostbyname resolve loopback fallback
    try:
        hostname = socket.gethostname()
        for addr in socket.getaddrinfo(hostname, None):
            ip = addr[4][0]
            if ip and ip != "127.0.0.1" and not ip.startswith("127.") and ":" not in ip:
                return ip
    except Exception:
        pass

    # 3. Try fallback broadcast route trick
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            if ip and ip != "127.0.0.1":
                return ip
    except Exception:
        pass

    return None


@app.get("/api/system/ip")
def get_system_ip():
    ip = get_real_ip()
    return {"ip": ip or "127.0.0.1"}


@app.get("/health")
def health_check():
    return {"status": "ok", "version": APP_VERSION}


def require_admin(request: Request):
    expected_token = os.getenv("FITRACE_ADMIN_TOKEN")
    if not expected_token:
        return
    provided_token = request.headers.get("X-FitRace-Admin-Token")
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Admin token required")


def require_diagnostics_admin(request: Request):
    expected_token = os.getenv("FITRACE_DIAGNOSTICS_TOKEN") or os.getenv(
        "FITRACE_ADMIN_TOKEN"
    )
    if not expected_token:
        raise HTTPException(
            status_code=503, detail="Diagnostics token is not configured"
        )
    provided_token = request.headers.get("X-FitRace-Diagnostics-Token")
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Diagnostics token required")


def is_test_telemetry_enabled() -> bool:
    return (
        os.getenv("TESTING") == "1" or os.getenv("FITRACE_ENABLE_TEST_TELEMETRY") == "1"
    )


def is_diagnostics_enabled() -> bool:
    return os.getenv("FITRACE_ENABLE_DIAGNOSTICS") == "1"


def decode_avatar_webp(avatar_base64: str) -> bytes:
    if "," in avatar_base64:
        header, base64_data = avatar_base64.split(",", 1)
        if header.strip().lower() != "data:image/webp;base64":
            raise ValueError("Avatar must be a WebP data URL")
    else:
        base64_data = avatar_base64

    img_data = base64.b64decode(base64_data, validate=True)
    if not img_data:
        raise ValueError("Empty image data")
    if len(img_data) > MAX_AVATAR_BYTES:
        raise ValueError("Avatar image is too large")
    if len(img_data) < 12 or img_data[:4] != b"RIFF" or img_data[8:12] != b"WEBP":
        raise ValueError("Avatar must be a WebP image")
    return img_data


async def get_race_state_data() -> dict:
    return race_manager.get_state_snapshot()


async def broadcast_race_state():
    state_data = await get_race_state_data()
    ws_data = dict(state_data)
    ws_data["type"] = "state_change"
    await ws_manager.broadcast(ws_data)
    return state_data


@app.get("/api/race/state")
async def get_race_state():
    return await get_race_state_data()


@app.get("/api/nodes")
def get_nodes():
    return {"nodes": node_registry.list_nodes()}


@app.get("/api/system/power/status")
def get_power_status(request: Request):
    require_admin(request)
    status = power_manager.status()
    status["race_state"] = race_manager.get_state().value
    return status


@app.get("/api/updates/status")
def get_update_status(request: Request):
    require_admin(request)
    return update_checker.status()


@app.post("/api/updates/check")
async def check_updates(request: Request):
    require_admin(request)
    try:
        return await asyncio.to_thread(update_checker.check)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/updates/download")
async def download_updates(request: Request):
    require_admin(request)
    status = await asyncio.to_thread(update_checker.download)
    if status.get("state") == "error":
        raise HTTPException(status_code=409, detail=status.get("error"))
    return status


@app.post("/api/updates/install/hub")
async def install_hub_update(request: Request):
    require_admin(request)
    if race_manager.get_state() != RaceState.IDLE:
        raise HTTPException(
            status_code=409,
            detail="Hub update install is allowed only when race state is IDLE",
        )
    status = await asyncio.to_thread(update_checker.install_hub)
    if status.get("state") == "error":
        raise HTTPException(status_code=409, detail=status.get("error"))
    return status


@app.post("/api/updates/apply/hub")
async def apply_hub_update(request: Request):
    require_admin(request)
    if race_manager.get_state() != RaceState.IDLE:
        raise HTTPException(
            status_code=409,
            detail="Hub update apply is allowed only when race state is IDLE",
        )
    try:
        await asyncio.to_thread(
            run_systemctl, ["sudo", "systemctl", "start", HUB_UPDATER_SERVICE]
        )
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"state": "updater_started", "service": HUB_UPDATER_SERVICE}


async def run_hub_power_action(action):
    try:
        result = action()
        await ws_manager.broadcast(
            {
                "type": "system_power",
                "action": result.action,
                "target": result.target,
                "dry_run": result.dry_run,
                "message": result.message,
            }
        )
        return asdict(result)
    except PowerActionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/system/power/restart-service")
async def restart_hub_service(request: Request):
    require_admin(request)
    return await run_hub_power_action(power_manager.restart_service)


@app.post("/api/system/power/reboot")
async def reboot_hub(payload: PowerActionPayload, request: Request):
    require_admin(request)
    return await run_hub_power_action(
        lambda: power_manager.reboot(payload.confirmation)
    )


@app.post("/api/system/power/shutdown")
async def shutdown_hub(payload: PowerActionPayload, request: Request):
    require_admin(request)

    # 1. Notify all EdgeNodes via MQTT to shut down
    mqtt_client = getattr(request.app.state, "mqtt_client", None)
    if mqtt_client:
        try:
            import json

            await mqtt_client.publish(
                "fitrace/nodes/command", json.dumps({"action": "shutdown"})
            )
            logger.info("Published shutdown command to all EdgeNodes via MQTT")
        except Exception as e:
            logger.error(f"Failed to publish shutdown command to EdgeNodes: {e}")

    # 2. Wait a short moment to ensure MQTT messages are sent
    await asyncio.sleep(0.5)

    # 3. Shutdown the Central Hub itself
    return await run_hub_power_action(
        lambda: power_manager.shutdown(payload.confirmation)
    )


@app.post("/api/system/power/shutdown-system")
async def shutdown_system(request: Request):
    require_admin(request)

    # 1. Notify all EdgeNodes via MQTT to shut down
    mqtt_client = getattr(request.app.state, "mqtt_client", None)
    if mqtt_client:
        try:
            import json

            await mqtt_client.publish(
                "fitrace/nodes/command", json.dumps({"action": "shutdown"})
            )
            logger.info("Published shutdown command to all EdgeNodes via MQTT")
        except Exception as e:
            logger.error(f"Failed to publish shutdown command to EdgeNodes: {e}")

    # 2. Wait a short moment to ensure MQTT messages are sent
    await asyncio.sleep(0.5)

    # 3. Shutdown the Central Hub itself
    return await run_hub_power_action(lambda: power_manager.shutdown("SHUTDOWN"))


@app.post("/api/diagnostics/telemetry")
async def run_diagnostic_telemetry(
    payload: DiagnosticTelemetryPayload, request: Request
):
    if not is_diagnostics_enabled():
        raise HTTPException(status_code=404, detail="Not found")

    require_diagnostics_admin(request)

    if race_manager.get_state() == RaceState.RUNNING:
        raise HTTPException(
            status_code=409,
            detail="Diagnostics cannot run while a race is running",
        )

    diagnostic_manager = RaceManager()
    diagnostic_manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    diagnostic_manager.register_node(payload.node_id, "DIAGNOSTIC MODE")
    diagnostic_manager.start_race()
    progress = diagnostic_manager.update_telemetry(
        {
            "node_id": payload.node_id,
            "equipment_type": payload.equipment_type,
            "distance_m": payload.distance_m,
            "elapsed_time_ms": payload.elapsed_time_ms,
            "instantaneous_speed_kph": payload.instantaneous_speed_kph,
            "cadence_rpm": payload.cadence_rpm,
            "power_watts": payload.power_watts,
        }
    )
    if diagnostic_manager.get_state() == RaceState.RUNNING:
        diagnostic_manager.stop_race()

    await ws_manager.broadcast(progress)
    diagnostic_event = {
        "type": "diagnostic_telemetry",
        "status": "passed",
        "diagnostic": True,
        "node_id": payload.node_id,
        "progress": progress,
        "checks": {
            "api": "ok",
            "race_manager": "ok",
            "websocket_broadcast": "sent",
        },
    }
    await ws_manager.broadcast(diagnostic_event)

    logger.info(
        "diagnostic telemetry sent",
        extra={
            "client": request.client.host if request.client else None,
            "node_id": payload.node_id,
            "equipment_type": payload.equipment_type,
        },
    )

    return diagnostic_event


@app.get("/api/locales")
def get_locales():
    return {"default_locale": DEFAULT_LOCALE, "locales": list_locales()}


@app.get("/api/locales/{locale}")
def get_locale(locale: str):
    return load_locale(locale)


@app.post("/api/race/configure")
async def configure_race(payload: ConfigurePayload):
    try:
        config = RaceConfig(
            race_type=payload.race_type,
            target_value=payload.target_value,
            duration_sec=payload.duration_sec,
        )
        prev_state = race_manager.get_state()
        race_manager.configure(config)
        if prev_state == RaceState.STOPPED:
            race_event_engine.reset()
        return await broadcast_race_state()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/race/start")
async def start_race():
    try:
        race_manager.start_race()
        race_event_engine.reset()
        return await broadcast_race_state()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/race/stop")
async def stop_race():
    try:
        race_manager.stop_race()
        return await broadcast_race_state()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/race/close")
async def close_race():
    try:
        race_manager.close_race()
        return await broadcast_race_state()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/race/reset")
async def reset_race():
    race_manager.reset_race()
    race_event_engine.reset()
    return await broadcast_race_state()


@app.get("/api/stations")
def get_stations():
    return race_manager.get_stations_status()


@app.post("/api/stations/assign")
async def assign_station(payload: AssignStationPayload):
    try:
        race_manager.assign_station(payload.station_number, payload.node_id)
        # Broadcast the updated race state and leaderboard progress to all WebSocket clients
        await broadcast_race_state()
        return race_manager.get_stations_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/race/register")
async def register_athlete(payload: RegisterAthletePayload):
    try:
        has_avatar = False
        if payload.avatar_base64:
            try:
                img_data = decode_avatar_webp(payload.avatar_base64)
                avatar_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                    "static",
                    "avatars",
                )
                os.makedirs(avatar_dir, exist_ok=True)
                file_path = os.path.join(
                    avatar_dir, f"station_{payload.station_number}.webp"
                )
                with open(file_path, "wb") as f:
                    f.write(img_data)
                has_avatar = True
            except Exception as e:
                raise HTTPException(
                    status_code=400, detail=f"Invalid avatar image: {str(e)}"
                )
        else:
            avatar_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "static",
                "avatars",
            )
            file_path = os.path.join(
                avatar_dir, f"station_{payload.station_number}.webp"
            )
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass

        race_manager.register_athlete(
            payload.station_number,
            payload.athlete_name,
            team_name=payload.team_name,
            has_avatar=has_avatar,
        )

        # Broadcast registration success to the dashboard
        equipment_type = race_manager.get_station_equipment_type(payload.station_number)
        await ws_manager.broadcast(
            {
                "type": "registration_success",
                "athlete_name": payload.athlete_name,
                "station_number": payload.station_number,
                "team_name": payload.team_name,
                "equipment_type": equipment_type,
            }
        )

        return race_manager.get_stations_status()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/test/telemetry")
async def post_test_telemetry(payload: Dict[str, Any]):
    if not is_test_telemetry_enabled():
        raise HTTPException(status_code=404, detail="Not found")

    try:
        node_id = payload.get("node_id")
        if not node_id:
            raise HTTPException(status_code=400, detail="Missing node_id")

        progress = race_manager.ingest_telemetry(payload)
        if progress is not None:
            await ws_manager.broadcast(progress)

            # Check for race events
            events = race_event_engine.evaluate(race_manager, progress)
            for event in events:
                await ws_manager.broadcast(
                    {
                        "type": "race_event",
                        "event": event,
                    }
                )

            # If the race state just transitioned to STOPPED, broadcast state change
            if race_manager.get_state() == RaceState.STOPPED:
                state_change = race_manager.get_state_snapshot()
                state_change["type"] = "state_change"
                await ws_manager.broadcast(state_change)
            return progress

        # Broadcast empty progress to trigger frontend fetchStations() refresh
        await ws_manager.broadcast({})
        return {"status": "node_registered", "node_id": node_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # Keep connection open, wait for client close or check heartbeats
        while True:
            # We don't expect messages from dashboard client, but we wait to detect disconnection
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


static_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static"
)
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def read_root():
    return RedirectResponse(url="/static/index.html")


@app.get("/admin")
def read_admin():
    return RedirectResponse(url="/static/admin.html")


@app.get("/gameAdmin")
def read_game_admin():
    return RedirectResponse(url="/static/gameAdmin.html")


@app.get("/systemAdmin")
def read_system_admin():
    return RedirectResponse(url="/static/systemAdmin.html")
