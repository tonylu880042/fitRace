import ipaddress
import json
import logging
import os
import socket
import time
from dataclasses import asdict
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from edge_node.domain.models import EdgeNodeConfig
from edge_node.infrastructure import config_store as _config_store
from edge_node.infrastructure.antenna.command_runner import (
    AntennaCommandRequest,
    AntennaCommandRunner,
)
from edge_node.infrastructure.ble.ftms_scanner import BleakFtmsScanner
from edge_node.infrastructure.mqtt.one_shot_publisher import publish_bindings_removed
from fitrace_common.wifi_status import (
    LinuxWifiStatusReader,
    WifiStatus,  # noqa: F401 -- re-exported: tests build fakes via edge_app_module.WifiStatus(...)
)
from edge_node.usecases.antenna_reconnect import reconnect_configured_devices
from edge_node.usecases.binding_removal import diff_removed_binding_node_ids
from edge_node.usecases.event_log import EdgeEventLog
from edge_node.usecases.ftms_scanner import scan_ftms_devices
from edge_node.usecases.pairing_session import PairingSession, PairingSessionError
from fitrace_common import wifi_manager
from fitrace_common.power_manager import PowerActionError, PowerManager

logger = logging.getLogger("edge_node.fastapi")

app = FastAPI(title="FitRaceStudio Edge Node")
ftms_scanner = BleakFtmsScanner()
wifi_status_reader = LinuxWifiStatusReader()
edge_event_log = EdgeEventLog.from_env()
antenna_command_runner = AntennaCommandRunner(event_log=edge_event_log)


def _edge_service_restart_enabled() -> bool:
    return (
        os.getenv("FITRACE_EDGE_SERVICE_RESTART_ENABLED") == "1"
        or os.getenv("FITRACE_POWER_COMMANDS_ENABLED") == "1"
    )


power_manager = PowerManager(
    target="edge",
    service_name="fitracestudio-edge.service",
)
service_restart_manager = PowerManager(
    target="edge",
    service_name="fitracestudio-edge.service",
    dry_run=not _edge_service_restart_enabled(),
)
CONFIG_PATH = _config_store.CONFIG_PATH
FALLBACK_ANTENNA_PORT = "/dev/serial0"


class PowerActionPayload(BaseModel):
    confirmation: str | None = None


class AntennaCommandPayload(BaseModel):
    port: str | None = None
    baudrate: int = 115200
    rtscts: bool = False
    command: str
    timeout_sec: float = 5.0
    scan_duration_sec: float = 5.0
    macs: list[str] = Field(default_factory=list)
    report_interval_ms: int | None = None
    raw_command: str | None = None


class AntennaReconnectPayload(BaseModel):
    timeout_sec: float = 5.0
    report_interval_ms: int = 250
    disconnect_first: bool = False


class WifiConnectPayload(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=32)
    password: str | None = Field(None, max_length=64)
    interface: str = "wlan0"


class CentralHubPayload(BaseModel):
    host: str = Field(..., min_length=1, max_length=253)
    port: int = Field(1883, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        host = value.strip()
        if (
            not host
            or "://" in host
            or "/" in host
            or any(character.isspace() for character in host)
        ):
            raise ValueError("enter a host name or IP address without a URL path")
        return host


class EdgeConfigPayload(EdgeNodeConfig):
    @model_validator(mode="after")
    def reject_duplicate_binding_identities(self):
        seen_node_ids: set[str] = set()
        seen_targets: set[str] = set()
        for binding in self.equipment_bindings:
            if binding.node_id in seen_node_ids:
                raise ValueError(f"duplicate binding node_id: {binding.node_id}")
            seen_node_ids.add(binding.node_id)

            normalized_target = binding.ble_target.strip().upper()
            if normalized_target in seen_targets:
                raise ValueError(f"duplicate binding ble_target: {binding.ble_target}")
            seen_targets.add(normalized_target)
        return self


class PairingStartPayload(BaseModel):
    scan_duration_sec: float | None = None
    temp_connect: bool = True


class PairingConfirmPayload(BaseModel):
    mac: str
    equipment_type: str
    display_name: str | None = None


class PairingBindPayload(BaseModel):
    mac: str
    equipment_type: str
    display_name: str | None = None
    channel: str | None = None


class PairingConnectPayload(BaseModel):
    mac: str


class PairingFinishPayload(BaseModel):
    restart: bool = True


def require_admin(request: Request):
    expected_token = os.getenv("FITRACE_ADMIN_TOKEN")
    if not expected_token:
        return
    provided_token = request.headers.get("X-FitRace-Admin-Token")
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Admin token required")


def load_edge_config() -> EdgeNodeConfig:
    # Reads the module-level CONFIG_PATH by name (not a value captured at
    # def-time) so tests that monkeypatch this module's CONFIG_PATH attribute
    # still take effect -- see infrastructure/config_store.py for the actual
    # atomic load/save implementation shared with the runtime.
    try:
        return _config_store.load_edge_config(CONFIG_PATH)
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Invalid Edge Node config: {e}")


def save_edge_config(config: EdgeNodeConfig):
    # Hooked here (not in each individual endpoint) because every removal
    # entry point -- POST /api/config, DELETE /api/equipment-bindings,
    # DELETE /api/equipment-bindings/{node_id}, and the pairing-session
    # flow -- funnels through this single function. Diffing against the
    # config already on disk, in this one place, covers all of them.
    previous_config = load_edge_config()
    removed_node_ids = diff_removed_binding_node_ids(
        previous_config.equipment_bindings, config.equipment_bindings
    )

    _config_store.save_edge_config(config, CONFIG_PATH)

    if removed_node_ids:
        # Best-effort: this API process has no long-lived MQTT client of
        # its own (that belongs to the separate fitracestudio-edge.service
        # runtime), and the local binding removal above has already
        # succeeded on disk. A broker that is unreachable -- e.g. a
        # technician working on a bench with no Central Hub present --
        # must never turn into a failed config save.
        try:
            publish_bindings_removed(
                config.mqtt_host,
                config.mqtt_port,
                config.node_id,
                removed_node_ids,
            )
        except Exception as exc:
            logger.warning(
                "publish_bindings_removed raised for node_id=%s removed=%s: %s",
                config.node_id,
                removed_node_ids,
                exc,
            )


def default_antenna_port() -> str:
    config = load_edge_config()
    if config.antenna_channels:
        return config.antenna_channels[0].port
    return FALLBACK_ANTENNA_PORT


def central_hub_reachable(
    host: str,
    port: int,
    timeout_sec: float = 2.0,
) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


def _pairing_restore_configured_devices(config: EdgeNodeConfig) -> dict:
    return reconnect_configured_devices(
        config,
        antenna_command_runner,
        disconnect_first=True,
        timeout_sec=5.0,
        report_interval_ms=250,
    )


def _pairing_restart_service() -> dict:
    return asdict(service_restart_manager.restart_service())


pairing_session = PairingSession(
    command_runner=antenna_command_runner,
    event_log=edge_event_log,
    load_config=load_edge_config,
    save_config=save_edge_config,
    restore_configured_devices=_pairing_restore_configured_devices,
    restart_service=_pairing_restart_service,
)


@app.get("/health")
def health_check():
    return {"status": "ok", "role": "edge"}


@app.get("/api/i18n")
def get_i18n_dictionaries():
    return _I18N


@app.get("/api/system/power/status")
def get_power_status(request: Request):
    require_admin(request)
    status = power_manager.status()
    restart_status = service_restart_manager.status()
    return {
        **status,
        "restart_service_dry_run": restart_status["dry_run"],
        "restart_service_enabled": not restart_status["dry_run"],
    }


def run_power_action(action):
    try:
        return asdict(action())
    except PowerActionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/system/power/restart-service")
def restart_edge_service(request: Request):
    require_admin(request)
    return run_power_action(service_restart_manager.restart_service)


@app.post("/api/system/power/reboot")
def reboot_edge(payload: PowerActionPayload, request: Request):
    require_admin(request)
    return run_power_action(lambda: power_manager.reboot(payload.confirmation))


@app.post("/api/system/power/shutdown")
def shutdown_edge(payload: PowerActionPayload, request: Request):
    require_admin(request)
    return run_power_action(lambda: power_manager.shutdown(payload.confirmation))


def local_ip_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(
                ("8.8.8.8", 80)
            )  # no packet sent; just selects the outbound interface
            return s.getsockname()[0]
    except OSError:
        return ""


def edge_node_label(ip: str) -> str:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return "Node"
    if address.version != 4:
        return "Node"
    return f"Node{address.packed[-1]}"


@app.get("/", response_class=HTMLResponse)
def operator_page():
    node_label = edge_node_label(local_ip_address())
    html = OPERATOR_HTML_RENDERED.replace("__EDGE_NODE_LABEL__", node_label)
    html = html.replace("__EDGE_NODE_TITLE__", f"{node_label}-Edge Node Setup")
    return HTMLResponse(html)


@app.get("/maintenance", response_class=HTMLResponse)
def edge_setup_page():
    ip = local_ip_address()
    badge = f"{ip}:8001" if ip else "Local web setup :8001"
    html = EDGE_SETUP_HTML_RENDERED.replace("__EDGE_HOST_BADGE__", badge)
    html = html.replace("__EDGE_HOST_IP__", ip or "--")
    return HTMLResponse(html)


@app.get("/api/config")
def get_edge_config(request: Request):
    require_admin(request)
    return load_edge_config().model_dump()


@app.post("/api/config")
def update_edge_config(payload: EdgeConfigPayload, request: Request):
    require_admin(request)
    save_edge_config(payload)
    return {"status": "saved", "config": payload.model_dump()}


@app.delete("/api/equipment-bindings")
def clear_equipment_bindings(request: Request):
    """Clear persistent bindings, clear every antenna board, then restart.

    Saving the empty binding list first prevents the running Edge process from
    reconnecting a stale MAC while the UART commands are being sent.
    """
    require_admin(request)
    config = load_edge_config()
    cleared_count = len(config.equipment_bindings)
    cleared_config = config.model_copy(
        update={"equipment_bindings": []},
        deep=True,
    )
    save_edge_config(cleared_config)

    channels = []
    warnings = []
    for channel in cleared_config.antenna_channels:
        try:
            result = antenna_command_runner.run(
                AntennaCommandRequest(
                    port=channel.port,
                    baudrate=channel.baudrate,
                    rtscts=channel.rtscts,
                    command="disconnect_all",
                    timeout_sec=5.0,
                )
            )
            channels.append(
                {
                    "channel_id": channel.id,
                    "port": channel.port,
                    "disconnected": True,
                    "result": result,
                }
            )
        except (ValueError, RuntimeError) as exc:
            warning = f"{channel.id}: {exc}"
            warnings.append(warning)
            channels.append(
                {
                    "channel_id": channel.id,
                    "port": channel.port,
                    "disconnected": False,
                    "error": str(exc),
                }
            )

    restart = None
    try:
        restart = _pairing_restart_service()
        if restart.get("dry_run"):
            warnings.append("runtime restart is not enabled")
    except (PowerActionError, RuntimeError, OSError) as exc:
        warnings.append(f"runtime restart: {exc}")

    return {
        "status": "cleared" if not warnings else "cleared_with_warnings",
        "cleared_count": cleared_count,
        "config": cleared_config.model_dump(),
        "channels": channels,
        "restart": restart,
        "warnings": warnings,
    }


@app.delete("/api/equipment-bindings/{node_id}")
def remove_equipment_binding(node_id: str, request: Request):
    """Remove exactly ONE binding, identified by its node_id, without
    touching any other channel.

    Unlike DELETE /api/equipment-bindings (which wipes every binding on
    every channel with a DISCONNECT:ALL per channel), this sends a single
    per-MAC DISCONNECT for this binding's own ble_target on its own
    antenna_channel -- never disconnect_all, never a batch connect, and
    never any command on a different channel -- so every other bound device
    keeps streaming the whole time. This is the operator's way out of a
    full antenna board (ERROR:FULL) that used to require wiping every
    binding on the node just to free one slot.

    The runtime is deliberately not restarted here: AntennaFtmsManager's
    reconnect watchdog (_reconnect_missing_targets ->
    _refresh_configured_bindings) reloads config.json from disk on every
    pass, so it picks up the removed binding on its own well within one
    watchdog interval. Restarting the service would tear down and rebuild
    every other channel's live GATT sessions just to remove one binding,
    which is exactly the disruption this endpoint exists to avoid.

    If the UART DISCONNECT fails (or the binding's channel is no longer
    configured at all), the binding is still removed from config and the
    failure is reported as a warning rather than blocking the removal: the
    operator's intent is to free the slot, and a board that did not answer
    must not leave them stuck.
    """
    require_admin(request)
    try:
        config = load_edge_config()
        binding = next(
            (b for b in config.equipment_bindings if b.node_id == node_id), None
        )
        if binding is None:
            raise HTTPException(
                status_code=404, detail=f"No binding with node_id {node_id!r}"
            )

        channel = next(
            (ch for ch in config.antenna_channels if ch.id == binding.antenna_channel),
            None,
        )

        warnings = []
        command_result = None
        if channel is None:
            warnings.append(
                f"antenna channel {binding.antenna_channel!r} is not configured; "
                "binding removed without sending DISCONNECT"
            )
        else:
            try:
                command_result = antenna_command_runner.run(
                    AntennaCommandRequest(
                        port=channel.port,
                        baudrate=channel.baudrate,
                        rtscts=channel.rtscts,
                        command="disconnect",
                        macs=[binding.ble_target],
                        timeout_sec=5.0,
                    )
                )
            except (ValueError, RuntimeError) as exc:
                warnings.append(f"disconnect failed: {exc}")

        remaining_bindings = [
            b for b in config.equipment_bindings if b.node_id != node_id
        ]
        updated_config = config.model_copy(
            update={"equipment_bindings": remaining_bindings},
            deep=True,
        )
        save_edge_config(updated_config)

        return {
            "status": "removed" if not warnings else "removed_with_warnings",
            "binding": binding.model_dump(),
            "command": command_result,
            "warnings": warnings,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/ble/scan")
async def scan_ble_ftms_devices(
    request: Request,
    adapter: str = Query(
        "hci1",
        description="Linux BLE adapter to scan with. Use hci1 for the USB dongle by default.",
    ),
    timeout_sec: float = Query(5.0, gt=0, le=30),
    include_all: bool = Query(
        False,
        description="Return all BLE devices, not only devices advertising the FTMS service UUID.",
    ),
):
    require_admin(request)
    try:
        devices = await scan_ftms_devices(
            ftms_scanner,
            timeout_sec=timeout_sec,
            adapter=adapter,
            include_all=include_all,
        )
        return {
            "adapter": adapter,
            "timeout_sec": timeout_sec,
            "include_all": include_all,
            "devices": [device.model_dump() for device in devices],
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/wifi/status")
def get_wifi_status(
    request: Request,
    interface: str = Query(
        "wlan0",
        description="Linux Wi-Fi interface to inspect for RSSI.",
    ),
):
    require_admin(request)
    return wifi_status_reader.read(interface=interface).model_dump()


@app.get("/api/wifi/networks")
def list_wifi_networks(request: Request, interface: str = Query("wlan0")):
    require_admin(request)
    try:
        return {
            "interface": interface,
            "networks": wifi_manager.list_networks(interface),
        }
    except wifi_manager.WifiError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))


@app.post("/api/wifi/connect")
def connect_wifi(payload: WifiConnectPayload, request: Request):
    require_admin(request)
    try:
        detail = wifi_manager.connect(payload.ssid, payload.password, payload.interface)
    except wifi_manager.WifiError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"status": "connected", "detail": detail, "ip": local_ip_address()}


@app.get("/api/central-hub")
def get_central_hub(request: Request):
    require_admin(request)
    config = load_edge_config()
    reachable = central_hub_reachable(config.mqtt_host, config.mqtt_port)
    return {
        "host": config.mqtt_host,
        "port": config.mqtt_port,
        "status": "connected" if reachable else "unreachable",
        "reachable": reachable,
    }


@app.post("/api/central-hub")
def configure_central_hub(payload: CentralHubPayload, request: Request):
    require_admin(request)
    if not central_hub_reachable(payload.host, payload.port):
        raise HTTPException(
            status_code=503,
            detail=f"Central Hub is not reachable at {payload.host}:{payload.port}",
        )

    config = load_edge_config()
    changed = config.mqtt_host != payload.host or config.mqtt_port != payload.port
    if not changed:
        return {
            "status": "saved",
            "reachable": True,
            "config": config.model_dump(),
            "restart": None,
            "warnings": [],
        }

    updated_config = config.model_copy(
        update={"mqtt_host": payload.host, "mqtt_port": payload.port},
        deep=True,
    )
    save_edge_config(updated_config)

    try:
        restart = _pairing_restart_service()
        if not restart.get("executed"):
            raise RuntimeError("runtime restart was not executed")
    except (PowerActionError, RuntimeError, OSError) as exc:
        try:
            save_edge_config(config)
        except Exception as rollback_error:
            raise HTTPException(
                status_code=500,
                detail=f"Central Hub update failed and rollback failed: {rollback_error}",
            ) from rollback_error
        raise HTTPException(
            status_code=503,
            detail=f"Central Hub update was not applied: {exc}",
        ) from exc

    return {
        "status": "saved",
        "reachable": True,
        "config": updated_config.model_dump(),
        "restart": restart,
        "warnings": [],
    }


@app.get("/api/antenna/config")
def get_antenna_config(request: Request):
    require_admin(request)
    config = load_edge_config()
    channels = [channel.model_dump() for channel in config.antenna_channels]
    return {
        "protocol_version": config.antenna_protocol_version,
        "default_port": channels[0]["port"] if channels else FALLBACK_ANTENNA_PORT,
        "channels": channels,
    }


def _is_telemetry_event(event: dict) -> bool:
    # A pairing scan can flood the raw event log with dozens of device-
    # discovery "uart"/"rx" lines in a burst (a real scan on this hardware
    # produced 40 devices). Without this filter applied server-side, before
    # the trailing `limit` window is filled, that noise alone can evict a
    # still-connected machine's telemetry event from the window even though
    # it is recent -- see /api/monitor/events's `kind` parameter below.
    source = event.get("source")
    direction = event.get("direction")
    if source in ("mqtt", "local") and direction == "publish":
        topic = event.get("topic") or ""
        return topic.startswith("gym/telemetry/")
    if source == "uart" and direction == "rx":
        parsed = event.get("parsed") or {}
        return isinstance(parsed, dict) and parsed.get("type") == "telemetry"
    return False


@app.get("/api/monitor/events")
def get_monitor_events(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    kind: str | None = Query(None),
):
    require_admin(request)
    predicate = _is_telemetry_event if kind == "telemetry" else None
    return {
        "path": str(edge_event_log.path),
        "server_now_epoch_ms": int(time.time() * 1000),
        "events": edge_event_log.list_events(limit=limit, predicate=predicate),
    }


@app.post("/api/antenna/command")
def run_antenna_command(payload: AntennaCommandPayload, request: Request):
    require_admin(request)
    try:
        return antenna_command_runner.run(
            AntennaCommandRequest(
                port=payload.port or default_antenna_port(),
                baudrate=payload.baudrate,
                rtscts=payload.rtscts,
                command=payload.command,
                timeout_sec=payload.timeout_sec,
                scan_duration_sec=payload.scan_duration_sec,
                macs=payload.macs,
                report_interval_ms=payload.report_interval_ms,
                raw_command=payload.raw_command,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/antenna/reconnect-configured")
def reconnect_configured_antenna_devices(
    payload: AntennaReconnectPayload, request: Request
):
    require_admin(request)
    config = load_edge_config()
    try:
        return reconnect_configured_devices(
            config,
            antenna_command_runner,
            disconnect_first=payload.disconnect_first,
            timeout_sec=payload.timeout_sec,
            report_interval_ms=payload.report_interval_ms,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/pairing/start")
def start_pairing_session(payload: PairingStartPayload, request: Request):
    require_admin(request)
    try:
        return pairing_session.start(
            scan_duration_sec=payload.scan_duration_sec,
            temp_connect=payload.temp_connect,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/pairing/bind")
def bind_pairing_session(payload: PairingBindPayload, request: Request):
    require_admin(request)
    try:
        return pairing_session.bind(
            payload.mac,
            payload.equipment_type,
            payload.display_name,
            channel=payload.channel,
        )
    except PairingSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/pairing/connect")
def connect_pairing_session(payload: PairingConnectPayload, request: Request):
    require_admin(request)
    try:
        return pairing_session.connect(payload.mac)
    except PairingSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/pairing/finish")
def finish_pairing_session(payload: PairingFinishPayload, request: Request):
    require_admin(request)
    try:
        return pairing_session.finish(payload.restart)
    except PairingSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/pairing/status")
def get_pairing_status(request: Request):
    require_admin(request)
    return pairing_session.status()


@app.post("/api/pairing/confirm")
def confirm_pairing_session(payload: PairingConfirmPayload, request: Request):
    require_admin(request)
    try:
        return pairing_session.confirm(
            payload.mac, payload.equipment_type, payload.display_name
        )
    except PairingSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/api/pairing/cancel")
def cancel_pairing_session(request: Request):
    require_admin(request)
    try:
        return pairing_session.cancel()
    except PairingSessionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


EDGE_SETUP_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FitRace Edge Node Setup</title>
  <link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;700&family=Outfit:wght@400;600;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0a0a0a;
      --surface: #141414;
      --surface-2: #0d0e0f;
      --border: #262626;
      --border-strong: #3a3a3a;
      --text: #e3e2e2;
      --muted: #a3a3a3;
      --accent: #e2ff3b;
      --warn: #f59e0b;
      --danger: #ff4b4b;
      --ok: #34d399;
      --mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: "Outfit", "Noto Sans TC", sans-serif;
      line-height: 1.45;
    }

    .shell {
      width: min(1120px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }

    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
    }

    h1 {
      margin: 0;
      font-family: "Oswald", sans-serif;
      font-size: 28px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .sub {
      margin-top: 4px;
      color: var(--muted);
      font-size: 14px;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 0;
      background: var(--surface-2);
      color: var(--muted);
      font-family: var(--mono);
      font-size: 11px;
      text-transform: uppercase;
      white-space: nowrap;
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }

    .language-select {
      width: auto;
      min-width: 150px;
      height: 38px;
      border-radius: 0;
      border: 1px solid var(--border);
      background: var(--surface-2);
      color: var(--text);
    }

    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--ok);
      box-shadow: 0 0 10px rgba(52, 211, 153, 0.48);
    }

    .admin-auth-banner {
      margin-top: 16px;
      padding: 14px;
      border: 1px solid var(--danger);
      background: var(--surface-2);
    }

    .admin-auth-banner .field {
      margin: 10px 0 0;
      max-width: 320px;
    }

    main {
      display: grid;
      grid-template-columns: 360px 1fr;
      gap: 18px;
      margin-top: 20px;
    }

    .panel {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 0;
      padding: 18px;
    }

    .panel h2 {
      margin: 0 0 14px;
      font-family: "Oswald", sans-serif;
      font-size: 16px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .stack {
      display: grid;
      gap: 18px;
    }

    label {
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-family: var(--mono);
      font-size: 11px;
      font-weight: 500;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    input,
    select {
      width: 100%;
      height: 42px;
      padding: 0 12px;
      border: 1px solid var(--border);
      border-radius: 0;
      background: rgba(0, 0, 0, 0.32);
      color: var(--text);
      font: inherit;
    }

    .field { margin-bottom: 14px; }

    button {
      width: 100%;
      min-height: 44px;
      border: 1px solid var(--border-strong);
      border-radius: 0;
      background: var(--surface-2);
      color: var(--text);
      font-family: "Oswald", sans-serif;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      cursor: pointer;
    }

    button:hover:not(:disabled) {
      border-color: var(--accent);
      color: var(--accent);
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.45;
    }

    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #09090b;
    }

    button.primary:hover:not(:disabled) {
      color: #09090b;
      filter: brightness(1.08);
    }

    .status {
      display: grid;
      gap: 10px;
      margin-top: 16px;
    }

    .status-line {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-family: var(--mono);
      font-size: 13px;
    }

    .status-line strong {
      color: var(--text);
      font-weight: 700;
    }

    .uartlog {
      margin-top: 4px;
      /* tall enough to read a whole 30s poll cycle without scrolling, but capped
         by viewport height so the panel never pushes the page taller than the screen */
      max-height: min(70vh, 720px);
      min-height: 320px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 3px;
      font-family: var(--mono);
      font-size: 15px;
      line-height: 1.6;
    }
    .uartlog-row {
      display: flex;
      gap: 8px;
      align-items: baseline;
    }
    .uartlog-row .t { color: var(--muted); flex: none; }
    .uartlog-row .dir { flex: none; width: 1.2em; text-align: center; font-weight: 700; }
    .uartlog-row .ch { color: var(--muted); flex: none; }
    .uartlog-row .raw { color: var(--text); word-break: break-all; }
    .uartlog-row.tx .dir { color: #38bdf8; }
    .uartlog-row.rx .dir { color: #22c55e; }
    .uartlog-row.status .raw { color: var(--warn); font-weight: 700; }
    .uartlog-row.torn .raw { color: var(--danger); }
    .uartlog-empty { color: var(--muted); font-size: 14px; }

    .wifi-meter {
      display: grid;
      gap: 10px;
    }

    .wifi-score {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px;
      border: 1px solid var(--border);
      border-radius: 0;
      background: var(--surface-2);
    }

    .wifi-icon-wrap {
      display: flex;
      align-items: center;
      gap: 14px;
      min-width: 0;
    }

    .wifi-icon {
      width: 86px;
      height: 64px;
      flex: 0 0 auto;
    }

    .wifi-arc,
    .wifi-dot-mark {
      stroke: #5a6470;
      transition: stroke 0.25s ease, fill 0.25s ease;
    }

    .wifi-dot-mark {
      fill: #5a6470;
      stroke: none;
    }

    .wifi-icon.excellent .wifi-arc,
    .wifi-icon.excellent .wifi-arc.active,
    .wifi-icon.excellent .wifi-dot-mark { stroke: #22c55e; fill: #22c55e; }

    .wifi-icon.good .wifi-arc.active,
    .wifi-icon.good .wifi-dot-mark { stroke: #f97316; fill: #f97316; }

    .wifi-icon.fair .wifi-arc.active,
    .wifi-icon.fair .wifi-dot-mark { stroke: var(--warn); fill: var(--warn); }

    .wifi-icon.weak .wifi-arc.active,
    .wifi-icon.weak .wifi-dot-mark,
    .wifi-icon.poor .wifi-arc.active,
    .wifi-icon.poor .wifi-dot-mark { stroke: var(--danger); fill: var(--danger); }

    .wifi-status-text {
      min-width: 0;
    }

    .wifi-level {
      color: var(--text);
      font-family: "Oswald", sans-serif;
      font-size: 24px;
      font-weight: 700;
      line-height: 1.05;
      overflow-wrap: anywhere;
    }

    .wifi-sub {
      margin-top: 4px;
      color: var(--muted);
      font-size: 13px;
      overflow-wrap: anywhere;
    }

    .progress {
      height: 10px;
      overflow: hidden;
      border: 1px solid var(--border);
      border-radius: 0;
      background: rgba(0, 0, 0, 0.32);
    }

    .progress-fill {
      width: 0%;
      height: 100%;
      background: var(--accent);
      transition: width 0.2s ease;
    }

    .message {
      min-height: 22px;
      color: var(--muted);
      font-size: 13px;
    }

    .message.error { color: var(--danger); }
    .message.ok { color: var(--ok); }

    .device-list {
      display: grid;
      gap: 10px;
    }

    .device {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      padding: 14px;
      border: 1px solid var(--border);
      border-radius: 0;
      background: var(--surface-2);
    }

    .device-name {
      font-size: 15px;
      font-weight: 800;
    }

    .device-meta {
      margin-top: 4px;
      color: var(--muted);
      font-family: var(--mono);
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .rssi {
      align-self: start;
      min-width: 62px;
      padding: 6px 8px;
      border: 1px solid var(--border);
      border-radius: 0;
      color: var(--accent);
      text-align: center;
      font-weight: 800;
    }

    .empty {
      padding: 42px 16px;
      border: 1px dashed var(--border);
      border-radius: 0;
      color: var(--muted);
      text-align: center;
    }

    .button-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }

    .button-grid button {
      min-height: 40px;
      padding: 0 10px;
      font-size: 12px;
    }

    .button-secondary {
      border: 1px solid var(--border);
      background: var(--surface-2);
      color: var(--text);
    }

    .binding-list {
      display: grid;
      gap: 12px;
    }

    .binding-row {
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr) minmax(0, 1fr);
      gap: 10px;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 0;
      background: var(--surface-2);
    }

    .binding-row .field {
      margin-bottom: 0;
    }

    .binding-row.channel-active {
      border-color: var(--accent);
    }

    .binding-row.channel-dim {
      opacity: 0.35; /* dim rows outside the selected channel; keep them visible in place */
    }

    .binding-unbind {
      grid-column: 1 / -1;
      justify-self: end;
      width: auto;
      padding: 6px 14px;
      font-size: 12px;
    }

    .channel-slots {
      margin-top: 6px;
      font-weight: 600;
    }

    .channel-slots.full {
      color: #ff7b6b;
    }

    .channel-slots.free {
      color: var(--accent);
    }

    .modal-overlay {
      margin: 14px auto 0;
      max-width: 540px;
    }

    .modal-overlay[hidden] {
      display: none;
    }

    .modal-panel {
      background: var(--surface-2);
      border: 1px solid var(--accent);
      border-radius: 0;
      max-height: 70vh;
      overflow: auto;
      padding: 20px;
      position: relative;
    }

    .wifi-ip {
      font-family: var(--mono);
      font-size: 24px;
      font-weight: 700;
      letter-spacing: 0.5px;
      color: var(--accent);
    }

    .tab-bar {
      display: flex;
      gap: 8px;
    }

    .tab-bar .tab-button {
      width: auto;
      flex: 1;
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 0;
    }

    .tab-bar .tab-button.active {
      background: var(--accent);
      color: #09090b;
      border-color: var(--accent);
    }

    .antenna-advanced {
      margin-top: 16px;
      border-top: 1px solid var(--border);
      padding-top: 12px;
    }

    .antenna-advanced summary {
      cursor: pointer;
      font-weight: 600;
      margin-bottom: 12px;
    }

    .modal-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }

    .modal-head h2 {
      margin: 0;
      font-family: "Oswald", sans-serif;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .modal-close {
      flex-shrink: 0;
      width: auto;
      background: none;
      border: none;
      color: inherit;
      font-size: 20px;
      cursor: pointer;
      padding: 4px 8px;
    }

    .wizard-step-hint {
      margin: 6px 0 12px;
      font-size: 13px;
      opacity: 0.75;
    }

    .wizard-list {
      max-height: 560px; /* roughly 10 rows, rest scrolls */
      overflow-y: auto;
      margin-top: 4px;
    }

    .wizard-device {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 0;
      margin-top: 10px;
    }

    .wizard-device .meta {
      font-size: 12px;
      opacity: 0.7;
      overflow-wrap: anywhere;
    }

    .wizard-device button {
      flex-shrink: 0;
      width: auto;
      padding: 8px 14px;
    }

    .wizard-pick {
      justify-content: flex-start;
      cursor: pointer;
    }

    .wizard-pick input[type="checkbox"] {
      flex-shrink: 0;
      width: 20px;
      height: 20px;
      margin: 0;
      accent-color: var(--accent);
      cursor: pointer;
    }

    .wizard-pick.is-bound {
      opacity: 0.5;
      cursor: default;
    }

    .wizard-actions {
      display: flex;
      gap: 10px;
      margin-top: 16px;
    }

    .wizard-actions button {
      width: auto;
      flex: 1;
    }

    .binding-row .binding-target {
      grid-column: 1 / -1;
    }

    .readonly-input {
      color: var(--muted);
      background: rgba(0, 0, 0, 0.32);
      cursor: default;
    }

    .raw-output {
      max-height: 360px;
      overflow: auto;
      margin: 0;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 0;
      background: rgba(0, 0, 0, 0.32);
      color: var(--text);
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.5;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }

    .monitor-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 12px;
    }

    .monitor-toolbar .status-line {
      flex: 1;
    }

    .monitor-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .monitor-card {
      min-height: 184px;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 0;
      background: var(--surface-2);
    }

    .monitor-card-header {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }

    .monitor-equipment-name {
      min-width: 0;
      color: var(--text);
      font-size: 15px;
      font-weight: 800;
      overflow-wrap: anywhere;
    }

    .monitor-status-pill {
      flex: 0 0 auto;
      padding: 4px 8px;
      border: 1px solid var(--border);
      border-radius: 0;
      color: var(--muted);
      font-family: var(--mono);
      font-size: 11px;
      font-weight: 500;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    .monitor-status-pill.live {
      color: var(--ok);
      border-color: rgba(52, 211, 153, 0.4);
    }

    .monitor-status-pill.stale {
      color: var(--warn);
      border-color: rgba(245, 158, 11, 0.45);
    }

    .monitor-fields {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 7px 12px;
    }

    .monitor-field {
      min-width: 0;
      color: var(--muted);
      font-family: var(--mono);
      font-size: 11px;
    }

    .monitor-field strong {
      display: block;
      margin-top: 2px;
      color: var(--text);
      font-family: var(--mono);
      font-size: 13px;
      overflow-wrap: anywhere;
    }

    .monitor-field.wide {
      grid-column: 1 / -1;
    }

    @media (max-width: 820px) {
      main { grid-template-columns: 1fr; }
      header { align-items: flex-start; flex-direction: column; }
      .button-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .binding-row { grid-template-columns: 1fr; }
      .binding-row .binding-target { grid-column: auto; }
      .monitor-grid { grid-template-columns: 1fr; }
      /* ponytail: flatten the two .stack wrappers so `order` can move the UART log
         (left stack) below the antenna control panel (right stack) on one column;
         desktop's two-column layout above 820px is untouched. */
      .stack { display: contents; }
      section[aria-labelledby="uartlog-title"] { order: 1; }
      .tab-bar, #bindings-section, #monitor-section { order: 2; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>FitRace Edge Node</h1>
        <div class="sub" data-i18n="edge.subtitle">Local Edge Node setup</div>
      </div>
      <div class="header-actions">
        <select id="language-select" class="language-select" aria-label="System language">
          <option value="en-US">English</option>
          <option value="zh-TW">繁體中文</option>
        </select>
        <div class="badge"><span class="dot"></span><span>__EDGE_HOST_BADGE__</span></div>
      </div>
    </header>

    <div id="admin-auth-banner" class="admin-auth-banner" hidden>
      <div class="message error" data-i18n="admin.auth_required">Admin token required. Enter it below and save to continue.</div>
      <div class="field">
        <label for="admin-auth-input" data-i18n="admin.password_label">Admin token</label>
        <input id="admin-auth-input" type="password" autocomplete="off">
      </div>
      <button id="admin-auth-save" type="button" class="button-secondary" data-i18n="admin.save">Save token</button>
    </div>

    <main>
      <div class="stack">
        <section class="panel" aria-labelledby="wifi-title">
          <h2 id="wifi-title" data-i18n="wifi.title">Wi-Fi Signal</h2>
          <div class="wifi-meter" aria-live="polite">
            <div class="wifi-score">
              <div class="wifi-icon-wrap">
                <svg id="wifi-icon" class="wifi-icon" viewBox="0 0 96 72" role="img" aria-label="Wi-Fi signal level">
                  <path class="wifi-arc" data-arc="4" d="M10 24 C31 4 65 4 86 24" fill="none" stroke-width="7" stroke-linecap="round"/>
                  <path class="wifi-arc" data-arc="3" d="M23 37 C37 24 59 24 73 37" fill="none" stroke-width="7" stroke-linecap="round"/>
                  <path class="wifi-arc" data-arc="2" d="M35 50 C42 44 54 44 61 50" fill="none" stroke-width="7" stroke-linecap="round"/>
                  <circle class="wifi-dot-mark" cx="48" cy="62" r="5"/>
                </svg>
                <div class="wifi-status-text">
                  <div class="wifi-level" id="wifi-level">Checking</div>
                  <div class="wifi-sub" id="wifi-sub">Reading Wi-Fi status</div>
                </div>
              </div>
              <div class="badge"><span class="dot" id="wifi-dot"></span><span id="wifi-interface">wlan0</span></div>
            </div>
            <div class="status-line"><span>SSID</span><strong id="wifi-ssid">--</strong></div>
            <div class="status-line"><span>IP</span><strong id="wifi-ip" class="wifi-ip">__EDGE_HOST_IP__</strong></div>
            <div class="message" id="wifi-message">Reading current Wi-Fi status...</div>
            <button id="wifi-choose-btn" type="button" class="button-secondary" data-i18n="wifi.choose" style="margin-top:10px;">Choose Wi-Fi network</button>
          </div>
        </section>
        <section class="panel" aria-labelledby="uartlog-title">
          <h2 id="uartlog-title" data-i18n="uartlog.title">Antenna UART log</h2>
          <div class="uartlog" id="uartlog" aria-live="polite"></div>
        </section>
      </div>

      <div class="stack">
        <section class="panel" aria-labelledby="antenna-title">
          <h2 id="antenna-title" data-i18n="antenna.title">UART Antenna Control</h2>
          <div class="field">
            <label id="antenna-channel-label" data-i18n="antenna.channel">Active UART channel</label>
            <div class="tab-bar" id="antenna-channel" role="group" aria-labelledby="antenna-channel-label"></div>
            <div class="channel-slots" id="antenna-channel-slots" aria-live="polite"></div>
            <div class="sub" id="binding-filter-hint"></div>
          </div>
          <button class="antenna-command" type="button" data-command="scan">SCAN</button>
          <div class="message error" id="scan-live-warning" hidden data-i18n="antenna.scan_live_warning">Scanning interrupts live equipment data on connected devices.</div>
          <div class="status" aria-live="polite">
            <div class="status-line"><span data-i18n="antenna.command_status">Command status</span><strong id="antenna-state">Idle</strong></div>
            <div class="message" id="antenna-message">Ready to send UART commands.</div>
          </div>
          <details class="antenna-advanced">
            <summary data-i18n="antenna.advanced">Advanced maintenance</summary>
            <div class="field">
              <div class="sub" data-i18n="antenna.protocol_hint">Fixed by the antenna board protocol — shown for reference only.</div>
              <div class="status-line"><span data-i18n="antenna.baudrate">Baudrate</span><strong id="antenna-baudrate-value">--</strong></div>
              <div class="status-line"><span data-i18n="antenna.rtscts">RTS/CTS hardware flow control</span><strong id="antenna-rtscts-value">--</strong></div>
            </div>
            <div class="field">
              <label for="antenna-timeout" data-i18n="antenna.timeout">Read timeout seconds</label>
              <input id="antenna-timeout" type="number" min="1" max="30" value="5">
            </div>
            <div class="field">
              <label for="antenna-scan-duration" data-i18n="antenna.scan_duration">Scan duration seconds</label>
              <input id="antenna-scan-duration" type="number" min="1" max="30" value="5">
            </div>
            <div class="button-grid" aria-label="UART antenna commands">
              <button class="antenna-command" type="button" data-command="ping">PING</button>
              <button class="antenna-command" type="button" data-command="status">STATUS</button>
              <button class="antenna-command" type="button" data-command="version">VERSION</button>
              <button class="antenna-command" type="button" data-command="connect">CONNECT</button>
              <button class="antenna-command button-secondary" type="button" data-command="disconnect_all">DISCONNECT ALL</button>
              <button class="antenna-command button-secondary" type="button" data-command="reboot">REBOOT</button>
            </div>
            <div class="field" style="margin-top:14px;">
              <label for="antenna-macs" data-i18n="antenna.macs">CONNECT target (bound devices on this channel)</label>
              <select id="antenna-macs"></select>
            </div>
            <button id="antenna-connect-btn" type="button" class="button-secondary" data-i18n="antenna.connect">CONNECT selected devices</button>
            <button id="antenna-reconnect-configured-btn" type="button" class="button-secondary" data-i18n="antenna.reconnect_configured" style="margin-top:10px;">CONNECT configured devices</button>
            <div class="field" style="margin-top:14px;">
              <label for="antenna-report-interval" data-i18n="antenna.report_interval">Report interval ms</label>
              <input id="antenna-report-interval" type="number" min="100" max="10000" value="250">
            </div>
            <button id="antenna-report-btn" type="button" class="button-secondary" data-i18n="antenna.report">Set report interval</button>
            <div class="field" style="margin-top:14px;">
              <label for="antenna-raw" data-i18n="antenna.raw">Raw command</label>
              <input id="antenna-raw" type="text" placeholder="STATUS;" autocomplete="off">
            </div>
            <button id="antenna-raw-btn" type="button" class="button-secondary" data-i18n="antenna.send_raw">Send raw command</button>
            <div class="field" style="margin-top:14px;">
              <label data-i18n="antenna.output">UART Response</label>
              <pre id="antenna-output" class="raw-output">No UART command has been sent yet.</pre>
            </div>
          </details>
        </section>

        <div class="tab-bar" role="tablist">
          <button type="button" id="tab-bindings" class="tab-button active" role="tab" aria-selected="true" aria-controls="bindings-section" data-i18n="bindings.title">Equipment Bindings</button>
          <button type="button" id="tab-monitor" class="tab-button" role="tab" aria-selected="false" aria-controls="monitor-section" data-i18n="monitor.title">Runtime Monitor</button>
        </div>

        <section class="panel" aria-labelledby="bindings-title" id="bindings-section" role="tabpanel">
          <h2 id="bindings-title" data-i18n="bindings.title">Equipment Bindings</h2>
          <div class="status-line"><span data-i18n="bindings.node_id">Edge node</span><strong id="config-node-id">--</strong></div>
          <div class="field" style="margin-top:12px;">
            <label data-i18n="hub.address_label">Central Hub address</label>
            <input id="central-hub-input" type="text" autocomplete="off" placeholder="localhost">
            <div class="sub" data-i18n="hub.address_hint">Use "localhost" when the hub runs on this device, or a .local hostname for a separate hub. "auto" also works.</div>
          </div>
          <div class="binding-list" id="binding-list" style="margin-top:14px;"></div>
          <div class="message error" id="power-dry-run-badge" hidden data-i18n="power.dry_run_badge">Dry-run mode — power commands are not executed</div>
          <div class="button-grid" style="margin-top:14px;">
            <button id="config-save-btn" type="button" data-i18n="bindings.save">Save bindings</button>
            <button id="config-clear-btn" type="button" class="button-secondary" data-i18n="bindings.clear_all">Clear all bindings</button>
          </div>
          <div class="message" id="config-message" data-i18n="bindings.ready">Edit names here, then save and restart Edge runtime.</div>
        </section>

        <section class="panel" aria-labelledby="monitor-title" id="monitor-section" role="tabpanel" hidden>
          <div class="monitor-toolbar">
            <h2 id="monitor-title" data-i18n="monitor.title" style="margin:0;">Runtime Monitor</h2>
          </div>
          <div class="status-line">
            <span data-i18n="monitor.status">Fixed equipment telemetry slots</span>
            <strong id="monitor-count">0</strong>
          </div>
          <div id="monitor-grid" class="monitor-grid" aria-live="polite" style="margin-top:12px;">
            <div class="empty" data-i18n="monitor.empty">No equipment bindings configured.</div>
          </div>
        </section>
      </div>
    </main>
  </div>

  <div class="modal-overlay" id="scan-wizard" hidden>
    <div class="modal-panel">
      <div class="modal-head">
        <h2 id="wizard-title"></h2>
        <button type="button" class="modal-close" id="wizard-close" aria-label="Close">✕</button>
      </div>
      <div id="wizard-body"></div>
    </div>
  </div>

  <script>
    const languageSelect = document.getElementById("language-select");
    const wifiIcon = document.getElementById("wifi-icon");
    const wifiLevel = document.getElementById("wifi-level");
    const wifiSub = document.getElementById("wifi-sub");
    const wifiDot = document.getElementById("wifi-dot");
    const wifiInterface = document.getElementById("wifi-interface");
    const wifiSsid = document.getElementById("wifi-ssid");
    const wifiMessage = document.getElementById("wifi-message");
    const antennaChannelGroup = document.getElementById("antenna-channel");
    const antennaChannelSlots = document.getElementById("antenna-channel-slots");
    const antennaBaudrateValue = document.getElementById("antenna-baudrate-value");
    const antennaRtsctsValue = document.getElementById("antenna-rtscts-value");
    const antennaTimeoutInput = document.getElementById("antenna-timeout");
    const antennaScanDurationInput = document.getElementById("antenna-scan-duration");
    const antennaMacsInput = document.getElementById("antenna-macs");
    const antennaReportIntervalInput = document.getElementById("antenna-report-interval");
    const antennaRawInput = document.getElementById("antenna-raw");
    const antennaState = document.getElementById("antenna-state");
    const antennaMessage = document.getElementById("antenna-message");
    const antennaOutput = document.getElementById("antenna-output");
    const monitorGrid = document.getElementById("monitor-grid");
    const monitorCount = document.getElementById("monitor-count");
    const scanLiveWarning = document.getElementById("scan-live-warning");
    const antennaCommandButtons = Array.from(document.querySelectorAll(".antenna-command"));
    const antennaConnectBtn = document.getElementById("antenna-connect-btn");
    const antennaReconnectConfiguredBtn = document.getElementById("antenna-reconnect-configured-btn");
    const antennaReportBtn = document.getElementById("antenna-report-btn");
    const antennaRawBtn = document.getElementById("antenna-raw-btn");
    const bindingList = document.getElementById("binding-list");
    const configNodeId = document.getElementById("config-node-id");
    const configMessage = document.getElementById("config-message");
    const powerDryRunBadge = document.getElementById("power-dry-run-badge");
    const configSaveBtn = document.getElementById("config-save-btn");
    const configClearBtn = document.getElementById("config-clear-btn");
    const allAntennaButtons = [
      ...antennaCommandButtons,
      antennaConnectBtn,
      antennaReconnectConfiguredBtn,
      antennaReportBtn,
      antennaRawBtn,
    ];
    let edgeConfig = null;
    let antennaChannels = [];
    let selectedAntennaPort = ""; // port of the channel currently active for UART commands
    const ANTENNA_MAX_CONNECTIONS = 3; // nRF52832 board hard limit: 3 BLE links per board
    const EQUIPMENT_TYPES = [
      "treadmill", "curved_treadmill", "spin_bike", "fan_bike", "upright_bike",
      "recumbent_bike", "elliptical", "stair_climber", "rowing_machine", "ski_erg", "unknown",
    ];

    function typeLabel(value) {
      const key = `type.${value}`;
      const label = t(key);
      return label === key ? value : label;
    }

    function equipmentTypeOptions(selectedValue) {
      const values = EQUIPMENT_TYPES.includes(selectedValue) || !selectedValue
        ? EQUIPMENT_TYPES
        : [...EQUIPMENT_TYPES, selectedValue];
      return values.map((type) => (
        `<option value="${escapeHtml(type)}" ${type === selectedValue ? "selected" : ""}>${escapeHtml(typeLabel(type))}</option>`
      )).join("");
    }
    let monitorLatestByNode = new Map();
    let monitorLiveCount = 0; // read by the SCAN warning to know if a scan would interrupt live equipment
    let monitorDisplayedByNode = new Map();
    // normalised MAC -> the node_id its telemetry actually arrives under.
    let monitorNodeIdByMac = new Map();

    function normalizeMonitorMac(value) {
      return String(value || "").trim().toUpperCase();
    }

    function monitorBindingTargetCount(target) {
      const normalized = normalizeMonitorMac(target);
      if (!normalized) return 0;
      const bindings = Array.isArray(edgeConfig?.equipment_bindings) ? edgeConfig.equipment_bindings : [];
      return bindings.filter((binding) => normalizeMonitorMac(binding.ble_target) === normalized).length;
    }

    // node_id is only a label, and it can disagree with the runtime: a binding
    // edited while the runtime still holds an older config gets no match there,
    // so its telemetry is published under a MAC-derived node_id instead. The MAC
    // is the machine's real identity, so resolve by MAC first and keep node_id
    // as the fallback for payloads that carry no mac_address. A corrupt/legacy
    // config may contain the same MAC twice; in that case MAC is ambiguous, so
    // keep the two cards separate by their node_id instead of collapsing them.
    function monitorKeyForBinding(binding) {
      const byMac = monitorBindingTargetCount(binding.ble_target) === 1
        ? monitorNodeIdByMac.get(normalizeMonitorMac(binding.ble_target))
        : null;
      if (byMac) return byMac;
      return binding.node_id;
    }
    let monitorServerNowEpochMs = null;
    let monitorServerNowReceivedAtMs = null;
    const ANTENNA_DEFAULT_REPORT_INTERVAL_MS = 250;
    const MONITOR_REFRESH_MS = 250;
    const MONITOR_LIVE_WINDOW_MS = 3000;
    const MONITOR_SMOOTHING_MS = 180;
    const MONITOR_SMOOTH_FIELDS = [
      "instantaneous_speed_kph",
      "distance_m",
      "power_watts",
      "cadence_rpm",
      "rssi",
      "calories",
      "total_energy_kcal",
    ];
    function getBrowserLocale() {
      const saved = localStorage.getItem("fitrace.edge.locale");
      if (saved === "en-US" || saved === "zh-TW") return saved;
      const browserLang = navigator.language || navigator.userLanguage;
      if (browserLang && browserLang.toLowerCase().startsWith("zh")) {
        return "zh-TW";
      }
      return "en-US";
    }
    let currentLocale = getBrowserLocale();
    const dictionaries = __I18N_DICTIONARIES__;

    function t(key, params = {}) {
      let value = (dictionaries[currentLocale] || dictionaries["en-US"])[key] || dictionaries["en-US"][key] || key;
      Object.entries(params).forEach(([name, replacement]) => {
        value = value.replaceAll(`{${name}}`, String(replacement));
      });
      return value;
    }

    function applyTranslations() {
      document.documentElement.lang = currentLocale;
      languageSelect.value = currentLocale;
      document.querySelectorAll("[data-i18n]").forEach((element) => {
        element.innerText = t(element.dataset.i18n);
      });
      if (!antennaBusy) {
        antennaState.textContent = t("antenna.idle"); // don't clobber a status reflecting a command still in flight
      }
      if (!antennaMessage.classList.contains("error") && !antennaMessage.classList.contains("ok")) {
        antennaMessage.textContent = t("antenna.ready");
      }
      if (edgeConfig) {
        renderBindings(edgeConfig); // re-render dynamic rows in the new language
      }
      updateChannelOccupancy();
    }

    languageSelect.addEventListener("change", () => {
      currentLocale = languageSelect.value;
      localStorage.setItem("fitrace.edge.locale", currentLocale);
      applyTranslations();
      renderMonitorEquipment();
    });

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    function adminHeaders(extra = {}) {
      const headers = { ...extra };
      const token = localStorage.getItem("fitrace.adminPassword") || localStorage.getItem("fitrace.adminToken") || "";
      if (token) {
        headers["X-FitRace-Admin-Token"] = token;
      }
      return headers;
    }

    function showAdminAuthBanner() {
      const banner = document.getElementById("admin-auth-banner");
      if (banner) banner.hidden = false;
    }

    // Single choke point for the admin token header and the 401 banner — every
    // authenticated call goes through here so a copy-pasted fetch() can never
    // forget the 401 check again.
    async function adminFetch(url, options = {}) {
      const response = await fetch(url, { ...options, headers: adminHeaders(options.headers || {}) });
      if (response.status === 401) showAdminAuthBanner();
      return response;
    }

    document.getElementById("admin-auth-save")?.addEventListener("click", () => {
      const input = document.getElementById("admin-auth-input");
      const value = (input?.value || "").trim();
      if (!value) return;
      localStorage.setItem("fitrace.adminPassword", value);
      window.location.reload();
    });

    function setAntennaMessage(text, type = "") {
      antennaMessage.textContent = text;
      antennaMessage.className = `message ${type}`.trim();
    }

    function setConfigMessage(text, type = "") {
      configMessage.textContent = text;
      configMessage.className = `message ${type}`.trim();
    }

    function renderWifiStatus(status) {
      const connected = Boolean(status.connected);
      const level = status.quality_level || "unknown";
      const labels = {
        excellent: t("wifi.excellent"),
        good: t("wifi.good"),
        fair: t("wifi.fair"),
        weak: t("wifi.weak"),
        poor: t("wifi.poor"),
        unknown: t("wifi.disconnected"),
      };
      const arcCounts = {
        excellent: 4,
        good: 3,
        fair: 2,
        weak: 1,
        poor: 1,
        unknown: 0,
      };
      const activeCount = connected ? (arcCounts[level] ?? 0) : 0;

      wifiLevel.textContent = connected ? (labels[level] || t("wifi.checking")) : t("wifi.disconnected");
      wifiSub.textContent = connected ? t("wifi.position_hint") : t("wifi.connect_hint");
      wifiInterface.textContent = status.interface || "wlan0";
      wifiSsid.textContent = status.ssid || (connected ? "Unknown" : "Not connected");
      wifiMessage.textContent = status.recommendation || "No Wi-Fi status available.";
      wifiMessage.className = `message ${connected ? "" : "error"}`.trim();
      wifiDot.style.background = connected ? "var(--ok)" : "var(--danger)";
      wifiDot.style.boxShadow = connected
        ? "0 0 12px rgba(52, 211, 153, 0.45)"
        : "0 0 12px rgba(239, 71, 111, 0.45)";
      wifiIcon.setAttribute("class", `wifi-icon ${connected ? level : "unknown"}`.trim());
      wifiIcon.querySelectorAll(".wifi-arc").forEach((arc) => {
        const arcLevel = Number(arc.dataset.arc);
        arc.classList.toggle("active", arcLevel <= activeCount);
      });
    }

    function renderAntennaOutput(payload) {
      antennaOutput.textContent = JSON.stringify(payload, null, 2);
    }

    function formatEventTime(epochMs) {
      if (!epochMs) return "--";
      return new Date(epochMs).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    }

    function formatMetric(value, suffix = "", digits = 0) {
      if (value === null || value === undefined || value === "") return "--";
      const number = Number(value);
      if (!Number.isFinite(number)) return String(value);
      return `${number.toFixed(digits)}${suffix}`;
    }

    function renderMonitorField(labelKey, value, className = "", fieldName = "") {
      const fieldAttr = fieldName ? ` data-field="${fieldName}"` : "";
      return `
        <div class="monitor-field ${className}">
          <span>${escapeHtml(t(labelKey))}</span>
          <strong${fieldAttr}>${escapeHtml(value)}</strong>
        </div>
      `;
    }

    function monitorNowEpochMs() {
      if (monitorServerNowEpochMs && monitorServerNowReceivedAtMs !== null) {
        return monitorServerNowEpochMs + (performance.now() - monitorServerNowReceivedAtMs);
      }
      return Date.now();
    }

    function monitorTelemetryAgeMs(payload) {
      const timestamp = Number(payload?.timestamp_epoch_ms || 0);
      if (!timestamp) return Infinity;
      return monitorNowEpochMs() - timestamp;
    }

    function updateMonitorDisplayedPayloads(frameDeltaMs) {
      const alpha = 1 - Math.exp(-Math.max(0, frameDeltaMs) / MONITOR_SMOOTHING_MS);
      monitorLatestByNode.forEach((target, nodeId) => {
        const current = monitorDisplayedByNode.get(nodeId);
        if (!current) {
          monitorDisplayedByNode.set(nodeId, { ...target });
          return;
        }
        const next = { ...target };
        MONITOR_SMOOTH_FIELDS.forEach((field) => {
          const targetValue = Number(target?.[field]);
          const currentValue = Number(current?.[field]);
          if (!Number.isFinite(targetValue)) {
            return;
          }
          if (!Number.isFinite(currentValue)) {
            next[field] = targetValue;
            return;
          }
          const value = currentValue + ((targetValue - currentValue) * alpha);
          next[field] = Math.abs(value - targetValue) < 0.01 ? targetValue : value;
        });
        monitorDisplayedByNode.set(nodeId, next);
      });
    }

    function monitorStatusForPayload(payload) {
      if (!payload?.node_id) {
        return { label: t("monitor.waiting"), className: "" };
      }
      if (monitorTelemetryAgeMs(payload) <= MONITOR_LIVE_WINDOW_MS) {
        return { label: t("monitor.live"), className: "live" };
      }
      return { label: t("monitor.stale"), className: "stale" };
    }

    function monitorCardHtml(binding) {
      return `
          <div class="monitor-card" data-node-id="${escapeHtml(binding.node_id)}">
            <div class="monitor-card-header">
              <div class="monitor-equipment-name">${escapeHtml(binding.equipment_id || binding.node_id)}</div>
              <div class="monitor-status-pill"></div>
            </div>
            <div class="monitor-fields">
              ${renderMonitorField("monitor.name", binding.equipment_id || "--")}
              ${renderMonitorField("monitor.type", binding.equipment_type ? typeLabel(binding.equipment_type) : "--")}
              ${renderMonitorField("monitor.mac", "--", "wide", "mac")}
              ${renderMonitorField("monitor.channel", binding.antenna_channel || "--")}
              ${renderMonitorField("monitor.updated", "--", "", "updated")}
              ${renderMonitorField("monitor.speed", "--", "", "speed")}
              ${renderMonitorField("monitor.distance", "--", "", "distance")}
              ${renderMonitorField("monitor.power", "--", "", "power")}
              ${renderMonitorField("monitor.cadence", "--", "", "cadence")}
              ${renderMonitorField("monitor.rssi", "--", "", "rssi")}
              ${renderMonitorField("monitor.calories", "--", "", "calories")}
            </div>
          </div>
        `;
    }

    // Writing textContent unconditionally replaces the text node even when the
    // string is unchanged, which kills any DOM Selection anchored inside it (e.g. an
    // operator selecting a MAC address) on the very next frame. Only write on change.
    function setMonitorFieldText(el, value) {
      if (el && el.textContent !== value) el.textContent = value;
    }

    // Writes the fields that change without touching card structure, so the DOM nodes
    // (and any text selection / CSS transition on them) survive between rebuilds.
    // Reads from monitorCardRefs (built once per card-DOM rebuild, see renderMonitorEquipment)
    // instead of re-querying the DOM on every call — this runs every animation frame.
    function updateMonitorCardLeaves(bindings) {
      bindings.forEach((binding) => {
        const refs = monitorCardRefs.get(binding.node_id);
        if (!refs) return;
        // refs are keyed by the binding's own node_id (both sides come from the
        // binding list), but telemetry is keyed by whatever the runtime published.
        const key = monitorKeyForBinding(binding);
        const payload = monitorLatestByNode.get(key) || {};
        const displayPayload = monitorDisplayedByNode.get(key) || payload;
        const status = monitorStatusForPayload(payload);
        const pill = refs.pill;
        if (pill) {
          const pillClass = `monitor-status-pill ${status.className}`;
          if (pill.className !== pillClass) pill.className = pillClass;
          if (pill.textContent !== status.label) pill.textContent = status.label;
        }
        const fields = refs.fields;
        setMonitorFieldText(fields.get("mac"), payload.mac_address || binding.ble_target || "--");
        setMonitorFieldText(fields.get("updated"), formatEventTime(payload.timestamp_epoch_ms));
        setMonitorFieldText(fields.get("speed"), formatMetric(displayPayload.instantaneous_speed_kph, " kph", 2));
        setMonitorFieldText(fields.get("distance"), formatMetric(displayPayload.distance_m, " m", 0));
        setMonitorFieldText(fields.get("power"), formatMetric(displayPayload.power_watts, " W", 0));
        setMonitorFieldText(fields.get("cadence"), formatMetric(displayPayload.cadence_rpm, " rpm", 0));
        setMonitorFieldText(fields.get("rssi"), formatMetric(displayPayload.rssi, " dBm", 0));
        setMonitorFieldText(fields.get("calories"), formatMetric(displayPayload.calories ?? displayPayload.total_energy_kcal, " kcal", 0));
      });
    }

    let monitorGridSignature = null;
    // node_id -> { pill, fields: Map(fieldName -> element) }; rebuilt only when the
    // card DOM itself is rebuilt (see the signature branch below), never per-frame.
    let monitorCardRefs = new Map();

    // Keeps monitorLiveCount (and its two DOM reflections) fresh without touching
    // card DOM — cheap enough to call on every 250ms poll even while the Monitor
    // tab is hidden, since the SCAN confirm guard reads monitorLiveCount regardless.
    function recomputeMonitorLiveCount() {
      const bindings = Array.isArray(edgeConfig?.equipment_bindings) ? edgeConfig.equipment_bindings : [];
      monitorLiveCount = bindings.filter((binding) => {
        const payload = monitorLatestByNode.get(monitorKeyForBinding(binding));
        return payload?.node_id && monitorTelemetryAgeMs(payload) <= MONITOR_LIVE_WINDOW_MS;
      }).length;
      monitorCount.textContent = `${monitorLiveCount}/${bindings.length}`;
      if (scanLiveWarning) scanLiveWarning.hidden = monitorLiveCount === 0;
    }

    function renderMonitorEquipment() {
      recomputeMonitorLiveCount();
      const bindings = Array.isArray(edgeConfig?.equipment_bindings) ? edgeConfig.equipment_bindings : [];

      // Rebuild the card DOM only when the set of cards actually changes (added,
      // removed, renamed, retyped, rechanneled, or the locale switched) — every other
      // call just updates leaf text below via updateMonitorCardLeaves.
      const signature = `${currentLocale}|${bindings.map((b) => `${b.node_id}:${b.equipment_id}:${b.equipment_type}:${b.antenna_channel}`).join(",")}`;
      if (signature !== monitorGridSignature) {
        monitorGridSignature = signature;
        monitorGrid.innerHTML = bindings.length
          ? bindings.map(monitorCardHtml).join("")
          : `<div class="empty">${escapeHtml(t("monitor.empty"))}</div>`;
        monitorCardRefs = new Map();
        if (bindings.length) {
          monitorGrid.querySelectorAll(".monitor-card").forEach((card) => {
            const fields = new Map();
            card.querySelectorAll("[data-field]").forEach((el) => fields.set(el.dataset.field, el));
            monitorCardRefs.set(card.dataset.nodeId, { pill: card.querySelector(".monitor-status-pill"), fields });
          });
        }
      }
      if (bindings.length) updateMonitorCardLeaves(bindings);
    }

    let monitorLastFrameMs = performance.now();
    function animateMonitorEquipment(frameMs) {
      const deltaMs = frameMs - monitorLastFrameMs;
      monitorLastFrameMs = frameMs;
      if (!monitorSection.hidden) {
        updateMonitorDisplayedPayloads(deltaMs);
        renderMonitorEquipment();
      }
      requestAnimationFrame(animateMonitorEquipment);
    }

    function updateMonitorFromEvents(events) {
      events.forEach((event) => {
        const payload = event.payload || {};
        const topic = event.topic || "";
        if ((event.source !== "mqtt" && event.source !== "local") || event.direction !== "publish" || !topic.startsWith("gym/telemetry/")) {
          return;
        }
        if (!payload.node_id) {
          return;
        }
        const previous = monitorLatestByNode.get(payload.node_id);
        if (!previous || Number(payload.timestamp_epoch_ms || 0) >= Number(previous.timestamp_epoch_ms || 0)) {
          monitorLatestByNode.set(payload.node_id, payload);
          if (payload.mac_address) {
            monitorNodeIdByMac.set(normalizeMonitorMac(payload.mac_address), payload.node_id);
          }
        }
      });
      if (monitorSection.hidden) {
        recomputeMonitorLiveCount();
      } else {
        renderMonitorEquipment();
      }
      renderUartLog(events);
    }

    const UARTLOG_MAX = 80;
    let uartLogSignature = null;
    function renderUartLog(events) {
      const uartLog = document.getElementById("uartlog");
      if (!uartLog) return;
      const rows = events.filter(
        (e) => e.source === "uart" && !(e.parsed && e.parsed.type === "telemetry")
      ).slice(0, UARTLOG_MAX);
      // Skip the rebuild when nothing changed so an operator scrolled up to read a
      // torn frame doesn't get snapped back to the top on the next 250ms poll.
      const signature = `${currentLocale}|${rows.map((e) => `${e.timestamp_epoch_ms}|${e.direction}|${e.channel}|${(e.parsed && e.parsed.raw) ?? e.message}`).join(",")}`;
      if (signature === uartLogSignature) return;
      uartLogSignature = signature;
      const scrollTop = uartLog.scrollTop;
      if (!rows.length) {
        uartLog.innerHTML = `<div class="uartlog-empty">${escapeHtml(t("uartlog.empty"))}</div>`;
        uartLog.scrollTop = scrollTop;
        return;
      }
      uartLog.innerHTML = rows.map((e) => {
        const dir = e.direction === "tx" ? "tx" : "rx";
        const arrow = dir === "tx" ? "▸" : "◂";
        const raw = String((e.parsed && e.parsed.raw != null ? e.parsed.raw : e.message) || "");
        const isStatus = e.parsed && e.parsed.type === "status";
        // ponytail: a board line that isn't ';'-terminated is a torn/dropped UART frame
        const torn = dir === "rx" && raw !== "" && !raw.trimEnd().endsWith(";");
        const cls = torn ? "torn" : (isStatus ? "status" : "");
        return `<div class="uartlog-row ${dir} ${cls}">`
          + `<span class="t">${escapeHtml(formatEventTime(e.timestamp_epoch_ms))}</span>`
          + `<span class="dir">${arrow}</span>`
          + `<span class="ch">${escapeHtml(e.channel || "")}</span>`
          + `<span class="raw">${escapeHtml(raw)}</span>`
          + `</div>`;
      }).join("");
      uartLog.scrollTop = scrollTop;
    }

    async function refreshMonitorEvents() {
      try {
        const response = await adminFetch("/api/monitor/events?kind=telemetry&limit=200");
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || t("monitor.failed"));
        }
        if (Number(payload.server_now_epoch_ms || 0)) {
          monitorServerNowEpochMs = Number(payload.server_now_epoch_ms);
          monitorServerNowReceivedAtMs = performance.now();
        }
        updateMonitorFromEvents(Array.isArray(payload.events) ? payload.events : []);
      } catch (error) {
        monitorCount.textContent = "!";
        monitorGrid.innerHTML = `<div class="empty">${escapeHtml(error.message || t("monitor.failed"))}</div>`;
        monitorGridSignature = null; // force a rebuild on the next successful poll
      }
    }

    function channelBindingCounts() {
      const counts = new Map();
      const rows = Array.from(bindingList?.querySelectorAll(".binding-row") || []);
      if (rows.length) {
        rows.forEach((row) => {
          const channel = row.dataset.channel;
          const target = row.querySelector(".binding-target-input")?.value;
          if (!channel || !target) return;
          counts.set(channel, (counts.get(channel) || 0) + 1);
        });
        return counts;
      }
      const bindings = Array.isArray(edgeConfig?.equipment_bindings) ? edgeConfig.equipment_bindings : [];
      bindings.forEach((binding) => {
        if (!binding.ble_target || !binding.antenna_channel) return;
        counts.set(binding.antenna_channel, (counts.get(binding.antenna_channel) || 0) + 1);
      });
      return counts;
    }

    function selectedAntennaChannel() {
      return antennaChannels.find((channel) => channel.port === selectedAntennaPort) || null;
    }

    function updateAntennaProtocolInfo() {
      const channel = selectedAntennaChannel();
      antennaBaudrateValue.textContent = channel ? String(channel.baudrate) : "--";
      antennaRtsctsValue.textContent = channel ? t(channel.rtscts ? "antenna.rtscts_on" : "antenna.rtscts_off") : "--";
    }

    // One button per configured channel, occupancy baked into the label — this is the
    // page's active-channel selector, not a settings dropdown, so every channel's load
    // is visible without interacting (see antenna-channel field in the HTML above).
    function renderAntennaChannelButtons(counts) {
      antennaChannelGroup.innerHTML = antennaChannels.map((channel) => {
        const used = counts.get(channel.id) || 0;
        const active = channel.port === selectedAntennaPort;
        return `<button type="button" class="tab-button${active ? " active" : ""}" data-port="${escapeHtml(channel.port)}" aria-pressed="${active}">${escapeHtml(channel.id)} · ${used}/${ANTENNA_MAX_CONNECTIONS}</button>`;
      }).join("");
    }

    function updateChannelOccupancy() {
      const counts = channelBindingCounts();
      // With no channels configured, a UART command can still target the server's
      // fallback default_port (selectedAntennaPort gets set to it in renderAntennaConfig)
      // — only a total absence of any known port leaves nothing to send a command to.
      const channelsAvailable = antennaChannels.length > 0;
      const hasFallbackPort = !channelsAvailable && Boolean(selectedAntennaPort);
      if (!antennaBusy) {
        allAntennaButtons.forEach((button) => { button.disabled = !channelsAvailable && !hasFallbackPort; });
      }
      if (!channelsAvailable) {
        antennaChannelGroup.innerHTML = "";
        if (hasFallbackPort) {
          antennaChannelSlots.textContent = t("antenna.no_channels");
          antennaChannelSlots.className = "channel-slots";
        } else {
          antennaChannelSlots.textContent = t("antenna.channel_load_failed");
          antennaChannelSlots.className = "channel-slots full";
        }
        populateConnectTargets();
        updateAntennaProtocolInfo();
        return;
      }
      renderAntennaChannelButtons(counts);
      renderSelectedChannel(currentChannelId(), counts);
      populateConnectTargets();
      updateAntennaProtocolInfo();
    }

    function currentChannelId() {
      const channel = selectedAntennaChannel();
      return channel ? channel.id : "";
    }

    function knownChannelIds() {
      return new Set(antennaChannels.map((channel) => channel.id));
    }

    function boundTargetsForCurrentChannel() {
      const channelId = currentChannelId();
      const channelIds = knownChannelIds();
      const bindings = Array.isArray(edgeConfig?.equipment_bindings) ? edgeConfig.equipment_bindings : [];
      // CONNECT is per-board, so normally only offer devices bound to the selected
      // channel — but a binding whose channel is blank or no longer configured has
      // nowhere else to go, so treat it as unassigned and offer it on every channel.
      return bindings.filter((binding) => {
        if (!binding.ble_target) return false;
        const assigned = binding.antenna_channel && channelIds.has(binding.antenna_channel);
        return assigned ? binding.antenna_channel === channelId : true;
      });
    }

    function populateConnectTargets() {
      if (!antennaMacsInput || antennaMacsInput.tagName !== "SELECT") return;
      const previous = antennaMacsInput.value;
      const targets = boundTargetsForCurrentChannel();
      if (!targets.length) {
        antennaMacsInput.innerHTML = `<option value="" disabled selected>${escapeHtml(t("antenna.connect_none"))}</option>`;
        return;
      }
      const options = [`<option value="__all__">${escapeHtml(t("antenna.connect_all", { count: targets.length }))}</option>`];
      targets.forEach((binding) => {
        const label = `${binding.equipment_id || binding.ble_target} · ${binding.ble_target}`;
        options.push(`<option value="${escapeHtml(binding.ble_target)}">${escapeHtml(label)}</option>`);
      });
      antennaMacsInput.innerHTML = options.join("");
      if (previous && Array.from(antennaMacsInput.options).some((option) => option.value === previous)) {
        antennaMacsInput.value = previous;
      }
    }

    function renderSelectedChannel(channelId, counts) {
      const channelIds = knownChannelIds();
      let visible = 0;
      document.querySelectorAll(".binding-row").forEach((row) => {
        row.classList.remove("channel-active", "channel-dim");
        const rowChannel = row.dataset.channel;
        const assigned = rowChannel && channelIds.has(rowChannel);
        if (!assigned) return; // unassigned/unknown channel — reachable everywhere, never dimmed
        const match = rowChannel === channelId;
        row.classList.add(match ? "channel-active" : "channel-dim");
        if (match) visible += 1;
      });
      const hint = document.getElementById("binding-filter-hint");
      if (hint) {
        hint.textContent = t("bindings.filtered", { channel: channelId, count: visible });
      }
      const used = counts.get(channelId) || 0;
      const free = Math.max(0, ANTENNA_MAX_CONNECTIONS - used);
      if (free > 0) {
        antennaChannelSlots.textContent = t("antenna.slots_free", { channel: channelId, free });
        antennaChannelSlots.className = "channel-slots free";
      } else {
        antennaChannelSlots.textContent = t("antenna.slots_full", { channel: channelId });
        antennaChannelSlots.className = "channel-slots full";
      }
    }

    function renderAntennaConfig(config) {
      const channels = Array.isArray(config.channels) ? config.channels : [];
      antennaChannels = channels;
      // A fresh/uncommissioned node has no channels yet — fall back to the server's
      // default_port so UART controls (including REBOOT) stay reachable.
      selectedAntennaPort = channels.length ? (channels[0]?.port || "") : (config.default_port || "");
      if (edgeConfig) {
        renderBindings(edgeConfig);
      }
      updateChannelOccupancy();
    }

    function channelOptions(selectedValue) {
      return antennaChannels.map((channel) => (
        `<option value="${escapeHtml(channel.id)}" ${channel.id === selectedValue ? "selected" : ""}>${escapeHtml(channel.id)} (${escapeHtml(channel.port)})</option>`
      )).join("");
    }

    // ponytail: keyed by node_id (not index) so a re-render can restore in-progress
    // edits even if the underlying binding list order changes.
    //
    // "Dirty" is defined against what a render actually put in the DOM (row.dataset.rendered),
    // not against the raw config value — the two differ whenever the render can't faithfully
    // reproduce the config (empty antenna-channel list, blank/unknown equipment type both fall
    // back to whatever <option> the browser auto-selects). Comparing to config directly treated
    // those render quirks as user edits and made them sticky across re-renders.
    function bindingRowValues(row) {
      return {
        equipment_id: row.querySelector(".binding-equipment-id").value,
        equipment_type: row.querySelector(".binding-equipment-type").value,
        antenna_channel: row.querySelector(".binding-channel").value,
      };
    }

    function markBindingRowRendered(row) {
      row.dataset.rendered = JSON.stringify(bindingRowValues(row));
    }

    function bindingRowDirty(row) {
      return JSON.stringify(bindingRowValues(row)) !== row.dataset.rendered;
    }

    function captureBindingEdits() {
      const edits = new Map();
      bindingList.querySelectorAll(".binding-row").forEach((row) => {
        const nodeId = row.dataset.nodeId;
        if (!nodeId || !bindingRowDirty(row)) return;
        edits.set(nodeId, bindingRowValues(row));
      });
      return edits;
    }

    function renderBindings(config) {
      const priorEdits = captureBindingEdits(); // must run before edgeConfig is reassigned below
      edgeConfig = config;
      configNodeId.textContent = config.node_id || "--";
      const hubInput = document.getElementById("central-hub-input");
      if (hubInput && document.activeElement !== hubInput) {
        hubInput.value = config.mqtt_host || "";
      }
      const bindings = Array.isArray(config.equipment_bindings) ? config.equipment_bindings : [];
      if (!bindings.length) {
        bindingList.innerHTML = `<div class="empty">${escapeHtml(t("monitor.empty"))}</div>`;
        renderMonitorEquipment();
        updateChannelOccupancy();
        return;
      }
      bindingList.innerHTML = bindings.map((binding, index) => `
        <div class="binding-row" data-index="${index}" data-channel="${escapeHtml(binding.antenna_channel || "")}" data-node-id="${escapeHtml(binding.node_id || "")}">
          <div class="field">
            <label>${escapeHtml(t("bindings.name"))}</label>
            <input class="binding-equipment-id" type="text" value="${escapeHtml(binding.equipment_id || "")}" autocomplete="off">
          </div>
          <div class="field">
            <label>${escapeHtml(t("bindings.type"))}</label>
            <select class="binding-equipment-type">
              ${equipmentTypeOptions(binding.equipment_type)}
            </select>
          </div>
          <div class="field">
            <label>${escapeHtml(t("bindings.channel"))}</label>
            <select class="binding-channel">
              ${channelOptions(binding.antenna_channel || "")}
            </select>
          </div>
          <div class="field binding-target">
            <label>${escapeHtml(t("bindings.target"))} · ${escapeHtml(binding.node_id || "")}</label>
            <input class="binding-target-input readonly-input" type="text" value="${escapeHtml(binding.ble_target || "")}" readonly tabindex="-1">
          </div>
          <button type="button" class="binding-unbind button-secondary" data-index="${index}">${escapeHtml(t("bindings.unbind"))}</button>
        </div>
      `).join("");
      // Baseline every row against what this fresh render actually produced BEFORE restoring
      // any prior edit onto it — otherwise a restored (still-unsaved) value would get recorded
      // as its own baseline and immediately read back as clean.
      bindingList.querySelectorAll(".binding-row").forEach(markBindingRowRendered);
      priorEdits.forEach((values, nodeId) => {
        const row = bindingList.querySelector(`.binding-row[data-node-id="${CSS.escape(nodeId)}"]`);
        if (!row) return;
        row.querySelector(".binding-equipment-id").value = values.equipment_id;
        row.querySelector(".binding-equipment-type").value = values.equipment_type;
        row.querySelector(".binding-channel").value = values.antenna_channel;
        row.dataset.channel = values.antenna_channel;
      });
      renderMonitorEquipment();
      updateChannelOccupancy();
    }

    window.addEventListener("beforeunload", (event) => {
      const dirty = Array.from(bindingList.querySelectorAll(".binding-row")).some(bindingRowDirty);
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    });

    async function loadEdgeConfig() {
      try {
        const response = await adminFetch("/api/config");
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "Failed to load config");
        }
        renderBindings(payload);
      } catch (error) {
        setConfigMessage(error.message, "error");
      }
    }

    function collectEdgeConfig() {
      if (!edgeConfig) {
        throw new Error("Config is not loaded");
      }
      const bindings = Array.from(bindingList.querySelectorAll(".binding-row")).map((row, index) => {
        const original = edgeConfig.equipment_bindings[index] || {};
        return {
          ...original,
          equipment_id: row.querySelector(".binding-equipment-id").value.trim(),
          equipment_type: row.querySelector(".binding-equipment-type").value,
          antenna_channel: row.querySelector(".binding-channel").value,
          ble_target: original.ble_target,
        };
      });
      const hubInput = document.getElementById("central-hub-input");
      const mqttHost = (hubInput && hubInput.value.trim()) || "localhost";
      return {
        ...edgeConfig,
        mqtt_host: mqttHost,
        max_ftms_connections: Math.max(1, bindings.length),
        equipment_bindings: bindings,
      };
    }

    async function saveEdgeConfig() {
      let payload;
      try {
        payload = collectEdgeConfig();
      } catch (error) {
        setConfigMessage(error.message, "error");
        return false;
      }
      configSaveBtn.disabled = true;
      try {
        const response = await adminFetch("/api/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.detail || t("bindings.failed"));
        }
        edgeConfig = result.config;
        renderBindings(edgeConfig);
        setConfigMessage(t("bindings.saved"), "ok");
        return true;
      } catch (error) {
        setConfigMessage(error.message, "error");
        return false;
      } finally {
        configSaveBtn.disabled = false;
      }
    }

    // Shows a persistent notice up front so an operator never has to press
    // Restart to discover power commands are dry-run-only on this device.
    async function checkPowerDryRunMode() {
      try {
        const response = await adminFetch("/api/system/power/status");
        if (!response.ok) return;
        const status = await response.json();
        if (status.restart_service_dry_run ?? status.dry_run) {
          powerDryRunBadge.textContent = t("power.dry_run_badge");
          powerDryRunBadge.hidden = false;
        }
      } catch (_error) {
        // best-effort; badge just stays hidden if status can't be fetched
      }
    }

    // Returns true when the runtime actually restarted, false otherwise (dry-run
    // or failure). Callers that show their own follow-up success message must
    // check this before overwriting the dry-run warning below.
    async function restartEdgeRuntime() {
      configSaveBtn.disabled = true;
      try {
        const response = await adminFetch("/api/system/power/restart-service", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.detail || "Restart failed");
        }
        // PowerManager still returns HTTP 200 in dry-run mode (accepted but not
        // executed) -- surface that loudly instead of reporting false success.
        if (result.dry_run === true || result.executed === false) {
          setConfigMessage(t("power.dry_run_warning"), "error");
          return false;
        }
        setConfigMessage(t("bindings.restarted"), "ok");
        return true;
      } catch (error) {
        setConfigMessage(error.message, "error");
        return false;
      } finally {
        window.setTimeout(() => {
          configSaveBtn.disabled = false;
        }, 2000);
      }
    }

    async function saveAndApplyBindings() {
      if (await saveEdgeConfig()) {
        await restartEdgeRuntime();
      }
    }

    async function clearAllBindings() {
      const count = (edgeConfig?.equipment_bindings || []).length;
      if (!count) return;
      if (!window.confirm(t("bindings.clear_all_confirm", { count }))) return;
      edgeConfig = { ...edgeConfig, equipment_bindings: [], max_ftms_connections: 1 };
      renderBindings(edgeConfig);
      const saved = await saveEdgeConfig();
      if (!saved) {
        await loadEdgeConfig();
        return;
      }
      setConfigMessage(t("bindings.unbinding"), "ok");
      try {
        const response = await adminFetch("/api/antenna/reconnect-configured", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            disconnect_first: true,
            timeout_sec: Math.max(1, Math.min(30, Number(antennaTimeoutInput.value) || 5)),
            report_interval_ms: Math.max(100, Math.min(10000, Number(antennaReportIntervalInput.value) || ANTENNA_DEFAULT_REPORT_INTERVAL_MS)),
          }),
        });
        if (!response.ok) {
          let detail = "";
          try {
            const result = await response.json();
            detail = result?.detail || "";
          } catch (_parseError) {
            // response body wasn't JSON — fall back to the generic message below
          }
          setConfigMessage(detail ? `${t("bindings.failed")}: ${detail}` : t("bindings.failed"), "error");
          return;
        }
      } catch (error) {
        setConfigMessage(error.message || t("bindings.failed"), "error");
        return;
      }
      const restarted = await restartEdgeRuntime();
      if (restarted) {
        setConfigMessage(t("bindings.cleared"), "ok");
      }
    }

    async function loadAntennaConfig() {
      try {
        const response = await adminFetch("/api/antenna/config");
        if (!response.ok) {
          // Keep whatever selectedAntennaPort/antennaChannels are already known —
          // updateChannelOccupancy alone decides fallback vs. fully-disabled.
          updateChannelOccupancy();
          return;
        }
        renderAntennaConfig(await response.json());
      } catch (_error) {
        updateChannelOccupancy();
      }
    }

    function buildAntennaPayload(command) {
      const port = selectedAntennaPort;
      if (!port) {
        throw new Error("Serial port is required");
      }
      const channel = selectedAntennaChannel();

      const payload = {
        port,
        command,
        baudrate: channel ? channel.baudrate : 115200,
        rtscts: channel ? !!channel.rtscts : false,
        timeout_sec: Math.max(1, Math.min(30, Number(antennaTimeoutInput.value) || 5)),
        scan_duration_sec: Math.max(1, Math.min(30, Number(antennaScanDurationInput.value) || 5)),
      };

      if (command === "connect") {
        const target = antennaMacsInput.value;
        if (target === "__all__") {
          payload.macs = boundTargetsForCurrentChannel().map((binding) => binding.ble_target);
        } else if (target) {
          payload.macs = [target];
        } else {
          payload.macs = [];
        }
        if (!payload.macs.length) {
          throw new Error("Select a device to CONNECT");
        }
      }

      if (command === "report") {
        payload.report_interval_ms = Math.max(100, Math.min(10000, Number(antennaReportIntervalInput.value) || ANTENNA_DEFAULT_REPORT_INTERVAL_MS));
      }

      if (command === "raw") {
        payload.raw_command = antennaRawInput.value.trim();
        if (!payload.raw_command) {
          throw new Error("Raw command is required");
        }
      }

      return payload;
    }

    let antennaBusy = false;
    function setAntennaButtonsDisabled(disabled) {
      antennaBusy = disabled;
      allAntennaButtons.forEach((button) => {
        button.disabled = disabled;
      });
      // the Wi-Fi picker reuses #scan-wizard with the scan wizard, so it must stay
      // closed to the operator while a UART command (e.g. SCAN) owns that dialog
      wifiChooseBtn.disabled = disabled;
    }

    const scanWizard = document.getElementById("scan-wizard");
    const wizardTitle = document.getElementById("wizard-title");
    const wizardBody = document.getElementById("wizard-body");
    const wizardCloseBtn = document.getElementById("wizard-close");
    let wizardDevices = [];
    let wizardScanChannelId = "";
    const wizardSelected = new Set();
    const WIZARD_TYPE_MAP = {
      TREADMILL: "treadmill", TREAD: "treadmill",
      ROWER: "rowing_machine", ROWING: "rowing_machine",
      BIKE: "fan_bike", FAN_BIKE: "fan_bike",
      ELLIPTICAL: "elliptical",
      SKI: "ski_erg", SKI_ERG: "ski_erg",
    };

    function closeScanWizard() {
      scanWizard.hidden = true;
    }
    wizardCloseBtn.addEventListener("click", closeScanWizard);

    function showWizardAt(anchor) {
      // dialog sits in normal flow right below its trigger button, so it is
      // always visible where the user clicked — no fixed-position quirks
      if (anchor && anchor.nextElementSibling !== scanWizard) {
        anchor.insertAdjacentElement("afterend", scanWizard);
      }
      scanWizard.hidden = false;
      scanWizard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    function boundTargetSet() {
      return new Set((edgeConfig?.equipment_bindings || [])
        .filter((binding) => binding.ble_target)
        .map((binding) => binding.ble_target.toUpperCase()));
    }

    function usefulName(value) {
      const trimmed = (value || "").trim();
      return trimmed && trimmed !== "UNKNOWN" ? trimmed : "";
    }

    function openScanWizard(result) {
      const byAddress = new Map();
      (result.parsed || []).forEach((entry) => {
        if (entry.type !== "device" || !entry.address) return;
        const key = entry.address.toUpperCase();
        const previous = byAddress.get(key);
        // A single scan reports the same MAC many times; merge sightings so a
        // nameless-but-stronger ping never discards a name seen earlier.
        const merged = previous ? { ...previous } : { ...entry, name: usefulName(entry.name), device_type: usefulName(entry.device_type) };
        if (previous) {
          if ((entry.rssi ?? -999) > (previous.rssi ?? -999)) merged.rssi = entry.rssi;
          if (!merged.name && usefulName(entry.name)) merged.name = usefulName(entry.name);
          if (!merged.device_type && usefulName(entry.device_type)) merged.device_type = usefulName(entry.device_type);
        }
        byAddress.set(key, merged);
      });
      wizardDevices = Array.from(byAddress.values());
      if (!wizardDevices.length) return;
      wizardSelected.clear();
      const channelByPort = new Map(antennaChannels.map((channel) => [channel.port, channel.id]));
      wizardScanChannelId = channelByPort.get(result.port) || "";
      renderWizardStep1();
      showWizardAt(document.querySelector('button[data-command="scan"]'));
    }

    function renderWizardStep1() {
      wizardTitle.textContent = t("wizard.title1");
      const bound = boundTargetSet();
      const counts = channelBindingCounts();
      const freeSlots = antennaChannels.reduce(
        (sum, channel) => sum + Math.max(0, ANTENNA_MAX_CONNECTIONS - (counts.get(channel.id) || 0)),
        0,
      );
      // Drop selections for anything now bound or no longer in the scan list.
      const addresses = new Set(wizardDevices.map((device) => device.address.toUpperCase()));
      wizardSelected.forEach((addr) => {
        if (!addresses.has(addr) || bound.has(addr)) wizardSelected.delete(addr);
      });

      wizardBody.innerHTML = `
        <div class="wizard-step-hint">${escapeHtml(t("wizard.hint1"))}</div>
        <div class="wizard-list">
          ${wizardDevices.map((device) => {
            const addr = device.address.toUpperCase();
            const isBound = bound.has(addr);
            const checked = wizardSelected.has(addr) ? "checked" : "";
            return `
              <label class="wizard-device wizard-pick ${isBound ? "is-bound" : ""}">
                <input type="checkbox" data-pick="${escapeHtml(addr)}" ${checked} ${isBound ? "disabled" : ""}>
                <div>
                  <div>${escapeHtml(device.name || device.address)}${isBound ? ` · ${escapeHtml(t("wizard.bound"))}` : ""}</div>
                  <div class="meta">${escapeHtml(device.address)} · RSSI ${escapeHtml(String(device.rssi ?? "--"))} dBm · ${escapeHtml(device.device_type || "UNKNOWN")}</div>
                </div>
              </label>
            `;
          }).join("")}
        </div>
        <div class="wizard-step-hint" id="wizard-capacity"></div>
        <div class="wizard-actions">
          <button type="button" id="wizard-bind">${escapeHtml(t("wizard.bind_selected"))}</button>
        </div>
      `;

      const bindBtn = document.getElementById("wizard-bind");
      const capacityHint = document.getElementById("wizard-capacity");
      const refresh = () => {
        const count = wizardSelected.size;
        bindBtn.textContent = count
          ? t("wizard.bind_selected_count", { count })
          : t("wizard.bind_selected");
        bindBtn.disabled = count === 0 || freeSlots === 0;
        capacityHint.textContent = freeSlots === 0
          ? t("wizard.capacity_full")
          : t("wizard.capacity_free", { free: freeSlots });
      };
      wizardBody.querySelectorAll("[data-pick]").forEach((box) => {
        box.addEventListener("change", () => {
          if (box.checked) wizardSelected.add(box.dataset.pick);
          else wizardSelected.delete(box.dataset.pick);
          refresh();
        });
      });
      bindBtn.addEventListener("click", () => applyMultiBind(freeSlots));
      refresh();
    }

    function nextNodeId(bindings) {
      const matches = bindings
        .map((binding) => String(binding.node_id || "").match(/^(.*?)([0-9]+)$/))
        .filter(Boolean);
      if (matches.length) {
        const last = matches[matches.length - 1];
        const prefix = last[1];
        const samePrefix = matches.filter((match) => match[1] === prefix);
        const next = Math.max(...samePrefix.map((match) => Number(match[2]))) + 1;
        return prefix + String(next).padStart(last[2].length, "0");
      }
      return `${edgeConfig?.node_id || "edge"}-${String(bindings.length + 1).padStart(2, "0")}`;
    }

    async function applyMultiBind(freeSlots) {
      // Fill the scanned channel first, then the rest, respecting the
      // per-board 3-connection limit. Selected devices beyond capacity are
      // skipped and reported.
      const counts = channelBindingCounts();
      const orderedChannels = [
        ...antennaChannels.filter((channel) => channel.id === wizardScanChannelId),
        ...antennaChannels.filter((channel) => channel.id !== wizardScanChannelId),
      ];
      const remaining = new Map(
        orderedChannels.map((channel) => [channel.id, ANTENNA_MAX_CONNECTIONS - (counts.get(channel.id) || 0)]),
      );
      const picks = wizardDevices.filter((device) => wizardSelected.has(device.address.toUpperCase()));
      const bindings = [...(edgeConfig?.equipment_bindings || [])];
      let added = 0;
      for (const device of picks) {
        const channel = orderedChannels.find((ch) => (remaining.get(ch.id) || 0) > 0);
        if (!channel) break; // out of slots
        remaining.set(channel.id, remaining.get(channel.id) - 1);
        bindings.push({
          node_id: nextNodeId(bindings),
          equipment_id: (device.name && device.name !== "UNKNOWN") ? device.name : device.address,
          equipment_type: WIZARD_TYPE_MAP[(device.device_type || "").toUpperCase()] || "treadmill",
          ble_target: device.address,
          antenna_channel: channel.id,
        });
        added += 1;
      }
      if (!added) return;
      const skipped = picks.length - added;
      wizardSelected.clear();
      edgeConfig = { ...edgeConfig, equipment_bindings: bindings, max_ftms_connections: bindings.length };
      renderBindings(edgeConfig);
      const saved = await saveEdgeConfig();
      if (!saved) {
        closeScanWizard();
        return;
      }
      // Push the new targets to the antenna board right away so they connect
      // in seconds, not after the runtime's slow reconcile loop. disconnect_first
      // is false: adding devices must not drop the ones already streaming.
      try {
        await adminFetch("/api/antenna/reconnect-configured", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            disconnect_first: false,
            timeout_sec: Math.max(1, Math.min(30, Number(antennaTimeoutInput.value) || 5)),
            report_interval_ms: Math.max(100, Math.min(10000, Number(antennaReportIntervalInput.value) || ANTENNA_DEFAULT_REPORT_INTERVAL_MS)),
          }),
        });
      } catch (_error) {
        // best-effort; the runtime restart below reloads config and reconnects
      }
      await restartEdgeRuntime();
      renderWizardStep3(added, skipped);
    }

    function renderWizardStep3(added, skipped) {
      wizardTitle.textContent = t("wizard.title3");
      const hint = skipped
        ? t("wizard.saved_multi", { count: added }) + t("wizard.some_skipped", { skipped })
        : t("wizard.saved_multi", { count: added });
      wizardBody.innerHTML = `
        <div class="wizard-step-hint">${escapeHtml(hint)}</div>
        <div class="wizard-actions">
          <button type="button" id="wizard-done">${escapeHtml(t("wizard.close"))}</button>
        </div>
      `;
      document.getElementById("wizard-done").addEventListener("click", closeScanWizard);
    }

    const wifiChooseBtn = document.getElementById("wifi-choose-btn");

    async function openWifiPicker() {
      wizardTitle.textContent = t("wifi.picker_title");
      wizardBody.innerHTML = `<div class="wizard-step-hint">${escapeHtml(t("wifi.scanning"))}</div>`;
      showWizardAt(wifiChooseBtn);
      let payload;
      try {
        const response = await adminFetch("/api/wifi/networks");
        payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.detail || "Wi-Fi scan failed");
        }
      } catch (error) {
        wizardBody.innerHTML = `<div class="wizard-step-hint">${escapeHtml(error.message)}</div>`;
        return;
      }
      const networks = payload.networks || [];
      if (!networks.length) {
        wizardBody.innerHTML = `<div class="wizard-step-hint">${escapeHtml(t("wifi.none_found"))}</div>`;
        return;
      }
      wizardBody.innerHTML = `
        <div class="wizard-step-hint">${escapeHtml(t("wifi.picker_hint"))}</div>
        <div class="wizard-list">
          ${networks.map((net, index) => `
            <div class="wizard-device">
              <div>
                <div>${escapeHtml(net.ssid)}${net.active ? " ✓" : ""}</div>
                <div class="meta">${net.signal}%${net.secured ? " · 🔒" : ""}${net.saved ? ` · ${escapeHtml(t("wifi.saved"))}` : ""}</div>
              </div>
              <button type="button" data-net-index="${index}" ${net.active ? "disabled" : ""}>
                ${escapeHtml(net.active ? t("wifi.connected") : t("wifi.connect"))}
              </button>
            </div>
          `).join("")}
        </div>
        <div class="wizard-actions">
          <button type="button" class="button-secondary" id="wifi-rescan">${escapeHtml(t("wifi.rescan"))}</button>
        </div>
      `;
      wizardBody.querySelectorAll("[data-net-index]").forEach((button) => {
        button.addEventListener("click", () => renderWifiConnect(networks[Number(button.dataset.netIndex)]));
      });
      document.getElementById("wifi-rescan").addEventListener("click", openWifiPicker);
    }

    function renderWifiConnect(net) {
      wizardTitle.textContent = net.ssid;
      const needsPassword = net.secured && !net.saved;
      wizardBody.innerHTML = `
        <div class="wizard-step-hint">${escapeHtml(t("wifi.switch_warning"))}</div>
        ${needsPassword ? `
          <div class="field">
            <label>${escapeHtml(t("wifi.password"))}</label>
            <input id="wifi-password" type="password" autocomplete="off">
          </div>
        ` : ""}
        <div class="wizard-actions">
          <button type="button" class="button-secondary" id="wifi-back">${escapeHtml(t("wizard.back"))}</button>
          <button type="button" id="wifi-connect-confirm">${escapeHtml(t("wifi.connect"))}</button>
        </div>
        <div class="wizard-step-hint" id="wifi-connect-result"></div>
      `;
      document.getElementById("wifi-back").addEventListener("click", openWifiPicker);
      document.getElementById("wifi-connect-confirm").addEventListener("click", async () => {
        const resultBox = document.getElementById("wifi-connect-result");
        const confirmBtn = document.getElementById("wifi-connect-confirm");
        const password = document.getElementById("wifi-password")?.value || null;
        if (needsPassword && !password) {
          resultBox.textContent = t("wifi.password_required");
          return;
        }
        confirmBtn.disabled = true;
        resultBox.textContent = t("wifi.connecting");
        try {
          const response = await adminFetch("/api/wifi/connect", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ssid: net.ssid, password }),
          });
          const result = await response.json();
          if (!response.ok) {
            throw new Error(result.detail || "Connect failed");
          }
          resultBox.textContent = t("wifi.connect_ok", { ssid: net.ssid, ip: result.ip || "?" });
          const ipLabel = document.getElementById("wifi-ip");
          if (ipLabel && result.ip) ipLabel.textContent = result.ip;
          refreshWifiStatus();
        } catch (error) {
          resultBox.textContent = error.message;
          confirmBtn.disabled = false;
        }
      });
    }

    wifiChooseBtn.addEventListener("click", openWifiPicker);

    const tabBindingsBtn = document.getElementById("tab-bindings");
    const tabMonitorBtn = document.getElementById("tab-monitor");
    const bindingsSection = document.getElementById("bindings-section");
    const monitorSection = document.getElementById("monitor-section");

    function selectTab(showMonitor) {
      bindingsSection.hidden = showMonitor;
      monitorSection.hidden = !showMonitor;
      tabBindingsBtn.classList.toggle("active", !showMonitor);
      tabMonitorBtn.classList.toggle("active", showMonitor);
      tabBindingsBtn.setAttribute("aria-selected", String(!showMonitor));
      tabMonitorBtn.setAttribute("aria-selected", String(showMonitor));
    }
    tabBindingsBtn.addEventListener("click", () => selectTab(false));
    tabMonitorBtn.addEventListener("click", () => selectTab(true));

    async function runAntennaCommand(command) {
      let payload;
      try {
        payload = buildAntennaPayload(command);
      } catch (error) {
        antennaState.textContent = t("antenna.failed");
        setAntennaMessage(error.message, "error");
        return;
      }

      setAntennaButtonsDisabled(true);
      antennaState.textContent = command.toUpperCase();
      setAntennaMessage(t("antenna.running", { command: command.toUpperCase(), port: payload.port }));
      antennaOutput.textContent = t("antenna.waiting");

      try {
        const response = await adminFetch("/api/antenna/command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.detail || "UART command failed");
        }
        antennaState.textContent = t("antenna.complete_state");
        setAntennaMessage(t("antenna.complete", { command: command.toUpperCase(), count: result.rx.length }), "ok");
        renderAntennaOutput(result);
        if (command === "scan") {
          openScanWizard(result);
        }
      } catch (error) {
        antennaState.textContent = t("antenna.failed");
        setAntennaMessage(error.message, "error");
        renderAntennaOutput({ error: error.message });
      } finally {
        setAntennaButtonsDisabled(false);
      }
    }

    async function reconnectConfiguredDevices() {
      const reportIntervalMs = Math.max(100, Math.min(10000, Number(antennaReportIntervalInput.value) || ANTENNA_DEFAULT_REPORT_INTERVAL_MS));
      const timeoutSec = Math.max(1, Math.min(30, Number(antennaTimeoutInput.value) || 5));
      setAntennaButtonsDisabled(true);
      antennaState.textContent = "CONNECT";
      setAntennaMessage(t("antenna.running", { command: "CONNECT", port: "configured channels" }));
      antennaOutput.textContent = t("antenna.waiting");

      try {
        const response = await adminFetch("/api/antenna/reconnect-configured", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            timeout_sec: timeoutSec,
            report_interval_ms: reportIntervalMs,
          }),
        });
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.detail || "Configured reconnect failed");
        }
        const lineCount = result.channels.reduce((total, channel) => (
          total
          + (channel.connect?.rx?.length || 0)
          + (channel.report?.rx?.length || 0)
        ), 0);
        antennaState.textContent = t("antenna.complete_state");
        setAntennaMessage(t("antenna.complete", { command: "CONNECT", count: lineCount }), "ok");
        renderAntennaOutput(result);
      } catch (error) {
        antennaState.textContent = t("antenna.failed");
        setAntennaMessage(error.message, "error");
        renderAntennaOutput({ error: error.message });
      } finally {
        setAntennaButtonsDisabled(false);
      }
    }

    async function refreshWifiStatus() {
      try {
        const response = await adminFetch("/api/wifi/status?interface=wlan0");
        const status = await response.json();
        if (!response.ok) {
          throw new Error(status.detail || "Failed to read Wi-Fi status");
        }
        renderWifiStatus(status);
      } catch (error) {
        renderWifiStatus({
          interface: "wlan0",
          connected: false,
          recommendation: error.message,
        });
      }
    }

    const ANTENNA_CONFIRM_KEYS = {
      disconnect_all: "antenna.disconnect_all_confirm",
      reboot: "antenna.reboot_confirm",
    };
    antennaCommandButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const command = button.dataset.command;
        const confirmKey = ANTENNA_CONFIRM_KEYS[command];
        if (confirmKey && !window.confirm(t(confirmKey))) return;
        if (command === "scan" && monitorLiveCount > 0 && !window.confirm(t("antenna.scan_live_confirm"))) return;
        runAntennaCommand(command);
      });
    });
    antennaChannelGroup.addEventListener("click", (event) => {
      const button = event.target.closest(".tab-button");
      if (!button) return;
      selectedAntennaPort = button.dataset.port;
      updateChannelOccupancy();
    });
    bindingList.addEventListener("change", (event) => {
      const select = event.target.closest(".binding-channel");
      if (!select) return;
      select.closest(".binding-row").dataset.channel = select.value;
      updateChannelOccupancy();
    });
    bindingList.addEventListener("click", async (event) => {
      const button = event.target.closest(".binding-unbind");
      if (!button) return;
      const index = Number(button.dataset.index);
      const binding = edgeConfig?.equipment_bindings?.[index];
      if (!binding) return;
      if (!window.confirm(t("bindings.unbind_confirm", { name: binding.equipment_id }))) return;
      const bindings = edgeConfig.equipment_bindings.filter((_, i) => i !== index);
      edgeConfig = { ...edgeConfig, equipment_bindings: bindings, max_ftms_connections: Math.max(1, bindings.length) };
      renderBindings(edgeConfig);
      const saved = await saveEdgeConfig();
      if (!saved) {
        await loadEdgeConfig();
        return;
      }
      setConfigMessage(t("bindings.unbinding"), "ok");
      try {
        await adminFetch("/api/antenna/reconnect-configured", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            disconnect_first: true,
            timeout_sec: Math.max(1, Math.min(30, Number(antennaTimeoutInput.value) || 5)),
            report_interval_ms: Math.max(100, Math.min(10000, Number(antennaReportIntervalInput.value) || ANTENNA_DEFAULT_REPORT_INTERVAL_MS)),
          }),
        });
      } catch (_error) {
        // board list refresh is best-effort; runtime restart below reloads config
      }
      const restarted = await restartEdgeRuntime();
      if (restarted) {
        setConfigMessage(t("bindings.unbound", { name: binding.equipment_id }), "ok");
      }
    });
    antennaConnectBtn.addEventListener("click", () => runAntennaCommand("connect"));
    antennaReconnectConfiguredBtn.addEventListener("click", reconnectConfiguredDevices);
    antennaReportBtn.addEventListener("click", () => runAntennaCommand("report"));
    antennaRawBtn.addEventListener("click", () => runAntennaCommand("raw"));
    configSaveBtn.addEventListener("click", saveAndApplyBindings);
    configClearBtn.addEventListener("click", clearAllBindings);

    applyTranslations();
    loadAntennaConfig();
    loadEdgeConfig();
    checkPowerDryRunMode();
    refreshWifiStatus();
    refreshMonitorEvents();
    setInterval(refreshWifiStatus, 5000);
    setInterval(refreshMonitorEvents, MONITOR_REFRESH_MS);
    requestAnimationFrame(animateMonitorEquipment);
  </script>
</body>
</html>
"""

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"
_I18N = {
    "en-US": json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8")),
    "zh-TW": json.loads((LOCALES_DIR / "zh_tw.json").read_text(encoding="utf-8")),
}
_I18N_JSON = json.dumps(_I18N, ensure_ascii=False).replace("</", "<\\/")
EDGE_SETUP_HTML_RENDERED = EDGE_SETUP_HTML.replace("__I18N_DICTIONARIES__", _I18N_JSON)

# The new operator page is a real static file (not a Python string), which
# permanently escapes the backslash-unescaping trap documented in CLAUDE.md
# for EDGE_SETUP_HTML above. Same __I18N_DICTIONARIES__ placeholder + `</`
# escaping convention, read and rendered once at import time.
OPERATOR_HTML_PATH = Path(__file__).resolve().parent / "operator.html"
OPERATOR_HTML = OPERATOR_HTML_PATH.read_text(encoding="utf-8")
OPERATOR_HTML_RENDERED = OPERATOR_HTML.replace("__I18N_DICTIONARIES__", _I18N_JSON)
