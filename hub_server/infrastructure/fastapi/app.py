import os
import sys
import base64
from typing import Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from hub_server.domain.models import RaceState, RaceConfig
from hub_server.usecases.race_manager import RaceManager
from hub_server.adapters.websocket_manager import WebSocketManager
from hub_server.infrastructure.network.configurator import MockWiFiConfigurator, LinuxWiFiConfigurator
from hub_server.usecases.network_manager import NetworkManager

app = FastAPI(title="FitRaceStudio Central Hub")


@app.middleware("http")
async def add_no_cache_header(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.endswith(".html") or path == "/" or path.endswith("/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Global instances (Shared Context)
race_manager = RaceManager()
ws_manager = WebSocketManager()

is_testing = "pytest" in sys.modules or os.environ.get("TESTING") == "1"

if sys.platform.startswith("linux") and not is_testing:
    configurator = LinuxWiFiConfigurator()
else:
    configurator = MockWiFiConfigurator()

network_manager = NetworkManager(configurator)


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


class WiFiPayload(BaseModel):
    ssid: str
    password: str


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/race/state")
def get_race_state():
    config = race_manager.get_config()
    return {
        "state": race_manager.get_state().value,
        "config": config.model_dump() if config else None,
        "registered_nodes": race_manager.get_registered_nodes(),
        "start_time_epoch_ms": race_manager._start_time_epoch_ms,
        "end_time_epoch_ms": getattr(race_manager, "_end_time_epoch_ms", None),
        "leaderboard": race_manager.get_leaderboard_progress(),
    }


@app.post("/api/race/configure")
def configure_race(payload: ConfigurePayload):
    try:
        config = RaceConfig(
            race_type=payload.race_type,
            target_value=payload.target_value,
            duration_sec=payload.duration_sec,
        )
        race_manager.configure(config)
        return get_race_state()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/race/start")
def start_race():
    try:
        race_manager.start_race()
        return get_race_state()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/race/stop")
def stop_race():
    try:
        race_manager.stop_race()
        return get_race_state()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/race/reset")
def reset_race():
    race_manager.reset_race()
    return get_race_state()


@app.get("/api/stations")
def get_stations():
    return race_manager.get_stations_status()


@app.post("/api/stations/assign")
async def assign_station(payload: AssignStationPayload):
    try:
        race_manager.assign_station(payload.station_number, payload.node_id)
        # Broadcast the updated race state and leaderboard progress to all WebSocket clients
        await ws_manager.broadcast({
            "type": "state_change",
            "state": race_manager.get_state().value,
            "config": race_manager.get_config().model_dump() if race_manager.get_config() else None,
            "registered_nodes": race_manager.get_registered_nodes(),
            "start_time_epoch_ms": race_manager._start_time_epoch_ms,
            "end_time_epoch_ms": getattr(race_manager, "_end_time_epoch_ms", None),
            "leaderboard": race_manager.get_leaderboard_progress(),
        })
        return race_manager.get_stations_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/race/register")
async def register_athlete(payload: RegisterAthletePayload):
    try:
        has_avatar = False
        if payload.avatar_base64:
            try:
                if "," in payload.avatar_base64:
                    header, base64_data = payload.avatar_base64.split(",", 1)
                else:
                    base64_data = payload.avatar_base64
                
                img_data = base64.b64decode(base64_data)
                if not img_data:
                    raise ValueError("Empty image data")
                
                avatar_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                    "static",
                    "avatars",
                )
                os.makedirs(avatar_dir, exist_ok=True)
                file_path = os.path.join(avatar_dir, f"station_{payload.station_number}.webp")
                with open(file_path, "wb") as f:
                    f.write(img_data)
                has_avatar = True
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid avatar image: {str(e)}")
        else:
            avatar_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "static",
                "avatars",
            )
            file_path = os.path.join(avatar_dir, f"station_{payload.station_number}.webp")
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
        station_node_id = race_manager._stations.get(payload.station_number)
        equipment_type = race_manager._active_nodes.get(station_node_id, "unknown") if station_node_id else "unknown"
        await ws_manager.broadcast({
            "type": "registration_success",
            "athlete_name": payload.athlete_name,
            "station_number": payload.station_number,
            "team_name": payload.team_name,
            "equipment_type": equipment_type,
        })
        
        return race_manager.get_stations_status()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/setup/status")
def get_setup_status():
    return network_manager.get_status()


@app.post("/api/setup/wifi")
def setup_wifi(payload: WiFiPayload):
    if not payload.ssid:
        raise HTTPException(status_code=400, detail="SSID is required")
    success = network_manager.configure_wifi(payload.ssid, payload.password)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to connect to WiFi")
    return network_manager.get_status()


@app.post("/api/setup/reset")
def reset_wifi():
    network_manager.reset_to_ap()
    return network_manager.get_status()


@app.post("/api/test/telemetry")
async def post_test_telemetry(payload: Dict[str, Any]):
    try:
        node_id = payload.get("node_id")
        if not node_id:
            raise HTTPException(status_code=400, detail="Missing node_id")

        eq_type = payload.get("equipment_type", "unknown")
        race_manager.update_active_node(node_id, eq_type)

        # Only update telemetry progress and broadcast if the race is actively RUNNING
        if race_manager.get_state() == RaceState.RUNNING:
            # TDD Compatibility: Auto-register node if not registered
            if node_id not in race_manager.get_registered_nodes():
                race_manager._registered_nodes[node_id] = f"Athlete {node_id}"
                # Add to progress dict since start_race has already been executed
                race_manager._progress[node_id] = {
                    "node_id": node_id,
                    "athlete_name": f"Athlete {node_id}",
                    "distance_m": 0.0,
                    "elapsed_time_ms": 0,
                    "instantaneous_speed_kph": 0.0,
                    "progress_percent": 0.0,
                    "calories": 0.0,
                    "power_watts": 0,
                    "max_power_watts": 0,
                    "finished_time_ms": None,
                }

            progress = race_manager.update_telemetry(payload)
            await ws_manager.broadcast(progress)
            
            # If the race state just transitioned to STOPPED, broadcast state change
            if race_manager.get_state() == RaceState.STOPPED:
                config = race_manager.get_config()
                await ws_manager.broadcast({
                    "type": "state_change",
                    "state": race_manager.get_state().value,
                    "config": config.model_dump() if config else None,
                    "registered_nodes": race_manager.get_registered_nodes(),
                    "start_time_epoch_ms": race_manager._start_time_epoch_ms,
                    "end_time_epoch_ms": getattr(race_manager, "_end_time_epoch_ms", None),
                })
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
