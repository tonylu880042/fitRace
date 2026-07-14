import json
import os
import socket
import time
from dataclasses import asdict
from pathlib import Path

from pydantic import BaseModel, Field
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from edge_node.domain.models import EdgeNodeConfig
from edge_node.infrastructure.antenna.command_runner import (
    AntennaCommandRequest,
    AntennaCommandRunner,
)
from edge_node.infrastructure.ble.ftms_scanner import BleakFtmsScanner
from edge_node.infrastructure.network.wifi_status import LinuxWifiStatusReader, WifiStatus
from edge_node.usecases.event_log import EdgeEventLog
from edge_node.usecases.ftms_scanner import scan_ftms_devices
from fitrace_common import wifi_manager
from fitrace_common.power_manager import PowerActionError, PowerManager


app = FastAPI(title="FitRaceStudio Edge Node")
ftms_scanner = BleakFtmsScanner()
wifi_status_reader = LinuxWifiStatusReader()
edge_event_log = EdgeEventLog.from_env()
antenna_command_runner = AntennaCommandRunner(event_log=edge_event_log)
power_manager = PowerManager(
    target="edge",
    service_name="fitracestudio-edge.service",
)
CONFIG_PATH = Path(__file__).resolve().parents[2] / "config.json"
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


class EdgeConfigPayload(EdgeNodeConfig):
    pass


def require_admin(request: Request):
    expected_token = os.getenv("FITRACE_ADMIN_TOKEN")
    if not expected_token:
        return
    provided_token = request.headers.get("X-FitRace-Admin-Token")
    if provided_token != expected_token:
        raise HTTPException(status_code=401, detail="Admin token required")


def load_edge_config() -> EdgeNodeConfig:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return EdgeNodeConfig.model_validate(json.load(f))
    except FileNotFoundError:
        return EdgeNodeConfig(node_id="fitrace-edge", antenna_protocol_version="unknown")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"Invalid Edge Node config: {e}")


def save_edge_config(config: EdgeNodeConfig):
    CONFIG_PATH.write_text(
        json.dumps(config.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def default_antenna_port() -> str:
    config = load_edge_config()
    if config.antenna_channels:
        return config.antenna_channels[0].port
    return FALLBACK_ANTENNA_PORT


@app.get("/health")
def health_check():
    return {"status": "ok", "role": "edge"}


@app.get("/api/system/power/status")
def get_power_status(request: Request):
    require_admin(request)
    return power_manager.status()


def run_power_action(action):
    try:
        return asdict(action())
    except PowerActionError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/system/power/restart-service")
def restart_edge_service(request: Request):
    require_admin(request)
    return run_power_action(power_manager.restart_service)


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
            s.connect(("8.8.8.8", 80))  # no packet sent; just selects the outbound interface
            return s.getsockname()[0]
    except OSError:
        return ""


@app.get("/", response_class=HTMLResponse)
def edge_setup_page():
    ip = local_ip_address()
    badge = f"{ip}:8001" if ip else "Local web setup :8001"
    return HTMLResponse(EDGE_SETUP_HTML.replace("__EDGE_HOST_BADGE__", badge))


@app.get("/api/config")
def get_edge_config(request: Request):
    require_admin(request)
    return load_edge_config().model_dump()


@app.post("/api/config")
def update_edge_config(payload: EdgeConfigPayload, request: Request):
    require_admin(request)
    save_edge_config(payload)
    return {"status": "saved", "config": payload.model_dump()}


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
    )
):
    require_admin(request)
    return wifi_status_reader.read(interface=interface).model_dump()


@app.get("/api/wifi/networks")
def list_wifi_networks(request: Request, interface: str = Query("wlan0")):
    require_admin(request)
    try:
        return {"interface": interface, "networks": wifi_manager.list_networks(interface)}
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


@app.get("/api/monitor/events")
def get_monitor_events(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
):
    require_admin(request)
    return {
        "path": str(edge_event_log.path),
        "server_now_epoch_ms": int(time.time() * 1000),
        "events": edge_event_log.list_events(limit=limit),
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
def reconnect_configured_antenna_devices(payload: AntennaReconnectPayload, request: Request):
    require_admin(request)
    config = load_edge_config()
    channels_by_id = {channel.id: channel for channel in config.antenna_channels}
    targets_by_channel: dict[str, list[str]] = {}
    for binding in config.equipment_bindings:
        if not binding.antenna_channel:
            continue
        if binding.antenna_channel not in channels_by_id:
            continue
        targets_by_channel.setdefault(binding.antenna_channel, []).append(binding.ble_target)

    if not targets_by_channel and not payload.disconnect_first:
        raise HTTPException(status_code=400, detail="No configured antenna targets found")

    results = []
    try:
        if payload.disconnect_first:
            # clear every board's link/target list so removed bindings
            # actually free their connection slot (firmware only has ALL)
            for channel in config.antenna_channels:
                antenna_command_runner.run(
                    AntennaCommandRequest(
                        port=channel.port,
                        baudrate=channel.baudrate,
                        rtscts=channel.rtscts,
                        command="disconnect_all",
                        timeout_sec=payload.timeout_sec,
                    )
                )
        for channel_id, macs in targets_by_channel.items():
            channel = channels_by_id[channel_id]
            connect_result = antenna_command_runner.run(
                AntennaCommandRequest(
                    port=channel.port,
                    baudrate=channel.baudrate,
                    rtscts=channel.rtscts,
                    command="connect",
                    timeout_sec=payload.timeout_sec,
                    macs=macs,
                )
            )
            report_result = antenna_command_runner.run(
                AntennaCommandRequest(
                    port=channel.port,
                    baudrate=channel.baudrate,
                    rtscts=channel.rtscts,
                    command="report",
                    timeout_sec=payload.timeout_sec,
                    report_interval_ms=payload.report_interval_ms,
                )
            )
            results.append(
                {
                    "channel_id": channel_id,
                    "port": channel.port,
                    "macs": macs,
                    "connect": connect_result,
                    "report": report_result,
                }
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {"status": "reconnected", "channels": results}


EDGE_SETUP_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FitRace Edge Node Setup</title>
  <style>
    :root {
      --bg: #0b0d10;
      --panel: #15191f;
      --panel-2: #101419;
      --border: #2a313b;
      --text: #f4f7fb;
      --muted: #9aa6b2;
      --accent: #d7ff3f;
      --warning: #f6a524;
      --danger: #ef476f;
      --ok: #34d399;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
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
      font-size: 28px;
      letter-spacing: 0;
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
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--muted);
      font-size: 13px;
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
      border-radius: 6px;
    }

    .dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--ok);
      box-shadow: 0 0 12px rgba(52, 211, 153, 0.45);
    }

    main {
      display: grid;
      grid-template-columns: 360px 1fr;
      gap: 18px;
      margin-top: 20px;
    }

    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 18px;
    }

    .panel h2 {
      margin: 0 0 14px;
      font-size: 16px;
      letter-spacing: 0;
    }

    .stack {
      display: grid;
      gap: 18px;
    }

    label {
      display: block;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }

    input,
    select {
      width: 100%;
      height: 42px;
      padding: 0 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #090b0e;
      color: var(--text);
      font: inherit;
    }

    .field { margin-bottom: 14px; }

    .toggle {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--panel-2);
      color: var(--muted);
      font-size: 14px;
    }

    .toggle input {
      width: 18px;
      height: 18px;
      accent-color: var(--accent);
    }

    button {
      width: 100%;
      min-height: 44px;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: #070807;
      cursor: pointer;
      font-weight: 800;
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.45;
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
      font-size: 13px;
    }

    .status-line strong {
      color: var(--text);
      font-weight: 700;
    }

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
      border-radius: 8px;
      background: var(--panel-2);
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
    .wifi-icon.fair .wifi-dot-mark { stroke: var(--warning); fill: var(--warning); }

    .wifi-icon.weak .wifi-arc.active,
    .wifi-icon.weak .wifi-dot-mark,
    .wifi-icon.poor .wifi-arc.active,
    .wifi-icon.poor .wifi-dot-mark { stroke: var(--danger); fill: var(--danger); }

    .wifi-status-text {
      min-width: 0;
    }

    .wifi-level {
      color: var(--text);
      font-size: 24px;
      font-weight: 800;
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
      border-radius: 999px;
      background: #090b0e;
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
      border-radius: 8px;
      background: var(--panel-2);
    }

    .device-name {
      font-size: 15px;
      font-weight: 800;
    }

    .device-meta {
      margin-top: 4px;
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .rssi {
      align-self: start;
      min-width: 62px;
      padding: 6px 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--accent);
      text-align: center;
      font-weight: 800;
    }

    .empty {
      padding: 42px 16px;
      border: 1px dashed var(--border);
      border-radius: 8px;
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
      background: var(--panel-2);
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
      border-radius: 8px;
      background: var(--panel-2);
    }

    .binding-row .field {
      margin-bottom: 0;
    }

    .binding-row.channel-active {
      border-color: var(--accent);
    }

    .binding-row.channel-dim {
      display: none; /* only the selected channel's devices stay visible */
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
      background: var(--panel-2, #111);
      border: 1px solid var(--accent);
      border-radius: 12px;
      max-height: 70vh;
      overflow: auto;
      padding: 20px;
      position: relative;
    }

    .tab-bar {
      display: flex;
      gap: 8px;
    }

    .tab-bar .tab-button {
      width: auto;
      flex: 1;
      background: var(--panel);
      color: var(--text);
      border: 1px solid var(--border);
    }

    .tab-bar .tab-button.active {
      background: var(--accent);
      color: #111;
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
      border-radius: 8px;
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
      background: #0d1116;
      cursor: default;
    }

    .raw-output {
      max-height: 360px;
      overflow: auto;
      margin: 0;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #090b0e;
      color: var(--text);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
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

    .monitor-refresh {
      width: auto;
      min-width: 96px;
      min-height: 34px;
      padding: 0 12px;
      font-size: 12px;
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
      border-radius: 8px;
      background: var(--panel-2);
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
      border-radius: 999px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }

    .monitor-status-pill.live {
      color: var(--ok);
      border-color: rgba(52, 211, 153, 0.4);
    }

    .monitor-status-pill.stale {
      color: var(--warning);
      border-color: rgba(246, 165, 36, 0.45);
    }

    .monitor-fields {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 7px 12px;
    }

    .monitor-field {
      min-width: 0;
      color: var(--muted);
      font-size: 11px;
    }

    .monitor-field strong {
      display: block;
      margin-top: 2px;
      color: var(--text);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
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
          <option value="it">Italiano</option>
          <option value="fr">Français</option>
          <option value="de-CH">Deutsch (Schweiz)</option>
          <option value="sv">Svenska</option>
        </select>
        <div class="badge"><span class="dot"></span><span>__EDGE_HOST_BADGE__</span></div>
      </div>
    </header>

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
            <div class="message" id="wifi-message">Reading current Wi-Fi status...</div>
            <button id="wifi-choose-btn" type="button" class="button-secondary" data-i18n="wifi.choose" style="margin-top:10px;">Choose Wi-Fi network</button>
          </div>
        </section>
      </div>

      <div class="stack">
        <section class="panel" aria-labelledby="antenna-title">
          <h2 id="antenna-title" data-i18n="antenna.title">UART Antenna Control</h2>
          <div class="field">
            <label for="antenna-channel" data-i18n="antenna.channel">UART channel</label>
            <select id="antenna-channel">
              <option value="">Manual serial port</option>
            </select>
            <div class="sub" id="antenna-channel-load"></div>
            <div class="channel-slots" id="antenna-channel-slots" aria-live="polite"></div>
          </div>
          <button class="antenna-command" type="button" data-command="scan">SCAN</button>
          <div class="status" aria-live="polite">
            <div class="status-line"><span data-i18n="antenna.command_status">Command status</span><strong id="antenna-state">Idle</strong></div>
            <div class="message" id="antenna-message">Ready to send UART commands.</div>
          </div>
          <details class="antenna-advanced">
            <summary data-i18n="antenna.advanced">Advanced maintenance</summary>
            <div class="field">
              <label for="antenna-port" data-i18n="antenna.port">Serial port</label>
              <input id="antenna-port" type="text" value="/dev/serial0" autocomplete="off">
            </div>
            <div class="field">
              <label for="antenna-baudrate" data-i18n="antenna.baudrate">Baudrate</label>
              <input id="antenna-baudrate" type="number" min="9600" max="1000000" value="115200">
            </div>
            <div class="field">
              <label class="toggle" for="antenna-rtscts">
                <span data-i18n="antenna.rtscts">RTS/CTS hardware flow control</span>
                <input id="antenna-rtscts" type="checkbox">
              </label>
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
              <button class="antenna-command button-secondary" type="button" data-command="disconnect_all">DISCONNECT</button>
              <button class="antenna-command button-secondary" type="button" data-command="reboot">REBOOT</button>
            </div>
            <div class="field" style="margin-top:14px;">
              <label for="antenna-macs" data-i18n="antenna.macs">Device MACs / IDs for CONNECT</label>
              <input id="antenna-macs" type="text" placeholder="AA:BB:CC:DD:EE:01,AA:BB:CC:DD:EE:02" autocomplete="off">
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
          <button type="button" id="tab-bindings" class="tab-button active" data-i18n="bindings.title">Equipment Bindings</button>
          <button type="button" id="tab-monitor" class="tab-button" data-i18n="monitor.title">Runtime Monitor</button>
        </div>

        <section class="panel" aria-labelledby="bindings-title" id="bindings-section">
          <h2 id="bindings-title" data-i18n="bindings.title">Equipment Bindings</h2>
          <div class="status-line"><span data-i18n="bindings.node_id">Edge node</span><strong id="config-node-id">--</strong></div>
          <div class="sub" id="binding-filter-hint"></div>
          <div class="binding-list" id="binding-list" style="margin-top:14px;"></div>
          <div class="button-grid" style="margin-top:14px;">
            <button id="config-save-btn" type="button" data-i18n="bindings.save">Save bindings</button>
          </div>
          <div class="message" id="config-message" data-i18n="bindings.ready">Edit names here, then save and restart Edge runtime.</div>
        </section>

        <section class="panel" aria-labelledby="monitor-title" id="monitor-section" hidden>
          <div class="monitor-toolbar">
            <h2 id="monitor-title" data-i18n="monitor.title" style="margin:0;">Runtime Monitor</h2>
            <button id="monitor-refresh-btn" type="button" class="button-secondary monitor-refresh" data-i18n="monitor.refresh">Refresh</button>
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
    const antennaPortInput = document.getElementById("antenna-port");
    const antennaChannelSelect = document.getElementById("antenna-channel");
    const antennaChannelLoad = document.getElementById("antenna-channel-load");
    const antennaChannelSlots = document.getElementById("antenna-channel-slots");
    const antennaBaudrateInput = document.getElementById("antenna-baudrate");
    const antennaRtsctsInput = document.getElementById("antenna-rtscts");
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
    const monitorRefreshBtn = document.getElementById("monitor-refresh-btn");
    const antennaCommandButtons = Array.from(document.querySelectorAll(".antenna-command"));
    const antennaConnectBtn = document.getElementById("antenna-connect-btn");
    const antennaReconnectConfiguredBtn = document.getElementById("antenna-reconnect-configured-btn");
    const antennaReportBtn = document.getElementById("antenna-report-btn");
    const antennaRawBtn = document.getElementById("antenna-raw-btn");
    const bindingList = document.getElementById("binding-list");
    const configNodeId = document.getElementById("config-node-id");
    const configMessage = document.getElementById("config-message");
    const configSaveBtn = document.getElementById("config-save-btn");
    const allAntennaButtons = [
      ...antennaCommandButtons,
      antennaConnectBtn,
      antennaReconnectConfiguredBtn,
      antennaReportBtn,
      antennaRawBtn,
    ];
    let edgeConfig = null;
    let antennaChannels = [];
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
    let monitorDisplayedByNode = new Map();
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
      if (saved) return saved;
      const browserLang = navigator.language || navigator.userLanguage;
      if (browserLang && browserLang.toLowerCase().startsWith("zh")) {
        return "zh-TW";
      }
      return "en-US";
    }
    let currentLocale = getBrowserLocale();
    const dictionaries = {
      "en-US": {
        "edge.subtitle": "Local Edge Node setup",
        "wifi.title": "Wi-Fi Signal",
        "wifi.checking": "Checking",
        "wifi.reading": "Reading Wi-Fi status",
        "wifi.excellent": "Excellent Wi-Fi",
        "wifi.good": "Good Wi-Fi",
        "wifi.fair": "Usable Wi-Fi",
        "wifi.weak": "Weak Wi-Fi",
        "wifi.poor": "Poor Wi-Fi",
        "wifi.disconnected": "Disconnected",
        "wifi.position_hint": "Signal state helps adjust on-site placement",
        "wifi.connect_hint": "Confirm the Edge Node is connected to the AP",
        "wifi.choose": "Choose Wi-Fi network",
        "wifi.picker_title": "Wi-Fi networks",
        "wifi.picker_hint": "Select the AP this Edge Node should connect to.",
        "wifi.scanning": "Scanning for networks (about 10 seconds)...",
        "wifi.none_found": "No networks found. Move closer to the AP and retry.",
        "wifi.saved": "saved",
        "wifi.connected": "Connected",
        "wifi.connect": "Connect",
        "wifi.switch_warning": "Switching networks changes this device's IP. A remote browser will lose this page — reconnect via the new IP or fitrace-pi.local. The on-device screen is unaffected.",
        "wifi.password": "Wi-Fi password",
        "wifi.password_required": "Password is required for this network.",
        "wifi.connecting": "Connecting...",
        "wifi.connect_ok": "Connected to {ssid}. IP: {ip}",
        "wifi.rescan": "Rescan",
        "antenna.title": "UART Antenna Control",
        "antenna.port": "Serial port",
        "antenna.channel": "UART channel",
        "antenna.advanced": "Advanced maintenance",
        "antenna.channel_load": "Bound devices per channel (max {max}/board)",
        "antenna.slots_free": "{channel}: {free} slot(s) free for a new device",
        "antenna.slots_full": "{channel} is full — pick another channel for new devices",
        "wizard.title1": "Add scanned devices",
        "wizard.hint1": "Select a device found by the scan to add it to the system.",
        "wizard.bound": "Already bound",
        "wizard.add": "Add",
        "wizard.title2": "Device settings",
        "wizard.replace": "Replace existing device (all channels full)",
        "wizard.back": "Back",
        "wizard.confirm": "Save device",
        "wizard.title3": "Done",
        "wizard.saved_hint": "{name} saved. Edge runtime restarted — the device will connect automatically.",
        "wizard.close": "Close",
        "type.treadmill": "Treadmill",
        "type.curved_treadmill": "Curved treadmill (non-motorized)",
        "type.spin_bike": "Spin bike",
        "type.fan_bike": "Fan bike (air bike)",
        "type.upright_bike": "Upright bike",
        "type.recumbent_bike": "Recumbent bike",
        "type.elliptical": "Elliptical",
        "type.stair_climber": "Stair climber",
        "type.rowing_machine": "Rowing machine",
        "type.ski_erg": "Ski erg",
        "type.unknown": "Unknown",
        "antenna.baudrate": "Baudrate",
        "antenna.rtscts": "RTS/CTS hardware flow control",
        "antenna.timeout": "Read timeout seconds",
        "antenna.scan_duration": "Scan duration seconds",
        "antenna.macs": "Device MACs / IDs for CONNECT",
        "antenna.connect": "CONNECT selected devices",
        "antenna.reconnect_configured": "CONNECT configured devices",
        "antenna.report_interval": "Report interval ms",
        "antenna.report": "Set report interval",
        "antenna.raw": "Raw command",
        "antenna.send_raw": "Send raw command",
        "antenna.command_status": "Command status",
        "antenna.output": "UART Response",
        "antenna.ready": "Ready to send UART commands.",
        "antenna.running": "Sending {command} to {port}.",
        "antenna.complete": "{command} complete. Received {count} line(s).",
        "antenna.complete_state": "Complete",
        "antenna.failed": "Command failed",
        "antenna.idle": "Idle",
        "monitor.title": "Runtime Monitor",
        "monitor.refresh": "Refresh",
        "monitor.status": "Fixed equipment telemetry slots",
        "monitor.empty": "No equipment bindings configured.",
        "monitor.failed": "Monitor read failed",
        "monitor.waiting": "Waiting",
        "monitor.live": "Live",
        "monitor.stale": "Idle (no data)",
        "monitor.name": "Name",
        "monitor.type": "Type",
        "monitor.mac": "MAC",
        "monitor.channel": "UART",
        "monitor.speed": "Speed",
        "monitor.distance": "Distance",
        "monitor.power": "Power",
        "monitor.cadence": "Cadence",
        "monitor.rssi": "RSSI",
        "monitor.calories": "Calories",
        "monitor.updated": "Updated",
        "bindings.title": "Equipment Bindings",
        "bindings.node_id": "Edge node",
        "bindings.name": "Display name",
        "bindings.type": "Equipment type",
        "bindings.channel": "UART channel",
        "bindings.target": "BLE target / MAC",
        "bindings.save": "Save and apply",
        "bindings.restart": "Restart Edge runtime",
        "bindings.ready": "Edit here; saving applies changes and restarts the Edge runtime automatically.",
        "bindings.saved": "Bindings saved. Restarting Edge runtime...",
        "bindings.restarted": "Edge runtime restarted. Changes applied.",
        "bindings.failed": "Config update failed",
        "bindings.unbind": "Unbind",
        "bindings.unbind_confirm": "Unbind {name}? The device will be disconnected from the antenna board.",
        "bindings.unbinding": "Unbinding — refreshing antenna target lists...",
        "bindings.unbound": "{name} unbound. Edge runtime restarted.",
        "bindings.filtered": "Showing {channel} devices ({count}). Pick another channel or Manual serial port to see the rest."
      }
    };
    dictionaries["zh-TW"] = {
      ...dictionaries["en-US"],
      "edge.subtitle": "Edge Node 本機設定",
      "wifi.title": "Wi-Fi 訊號",
      "wifi.checking": "檢查中",
      "wifi.reading": "讀取 Wi-Fi 狀態中",
      "wifi.excellent": "極佳 Wi-Fi",
      "wifi.good": "良好 Wi-Fi",
      "wifi.fair": "可用 Wi-Fi",
      "wifi.weak": "弱 Wi-Fi",
      "wifi.poor": "不良 Wi-Fi",
      "wifi.disconnected": "未連線",
      "wifi.position_hint": "訊號狀態可用於現場位置調整",
      "wifi.connect_hint": "請確認 Edge Node 已連上 AP",
      "wifi.choose": "選擇 Wi-Fi 網路",
      "wifi.picker_title": "Wi-Fi 網路清單",
      "wifi.picker_hint": "選擇這台 Edge Node 要連線的 AP。",
      "wifi.scanning": "掃描網路中（約 10 秒）...",
      "wifi.none_found": "找不到網路，請靠近 AP 後重試。",
      "wifi.saved": "已儲存",
      "wifi.connected": "使用中",
      "wifi.connect": "連線",
      "wifi.switch_warning": "切換網路後裝置 IP 會改變，遠端瀏覽器會斷開此頁面——請改用新 IP 或 fitrace-pi.local 重連。機上螢幕不受影響。",
      "wifi.password": "Wi-Fi 密碼",
      "wifi.password_required": "此網路需要密碼。",
      "wifi.connecting": "連線中...",
      "wifi.connect_ok": "已連上 {ssid}，IP：{ip}",
      "wifi.rescan": "重新掃描",
      "antenna.title": "UART 天線板控制",
      "antenna.port": "Serial port",
      "antenna.channel": "UART 通道",
      "antenna.advanced": "進階維護",
      "antenna.channel_load": "各通道已綁定設備（每板上限 {max}）",
      "antenna.slots_free": "{channel} 還有 {free} 個空位可綁定新設備",
      "antenna.slots_full": "{channel} 已滿，新設備請改用其他通道",
      "wizard.title1": "加入掃描到的設備",
      "wizard.hint1": "選擇掃描找到的裝置，將它加入系統。",
      "wizard.bound": "已綁定",
      "wizard.add": "加入",
      "wizard.title2": "設備設定",
      "wizard.replace": "取代現有設備（所有通道已滿）",
      "wizard.back": "上一步",
      "wizard.confirm": "儲存設備",
      "wizard.title3": "完成",
      "wizard.saved_hint": "{name} 已儲存，Edge runtime 已重啟，設備將自動連線。",
      "wizard.close": "關閉",
      "type.treadmill": "跑步機",
      "type.curved_treadmill": "無動力跑步機",
      "type.spin_bike": "飛輪車",
      "type.fan_bike": "風扇車",
      "type.upright_bike": "立式健身車",
      "type.recumbent_bike": "臥式健身車",
      "type.elliptical": "橢圓機",
      "type.stair_climber": "樓梯機",
      "type.rowing_machine": "划船機",
      "type.ski_erg": "滑雪機",
      "type.unknown": "未知",
      "antenna.baudrate": "Baudrate",
      "antenna.rtscts": "RTS/CTS 硬體流控",
      "antenna.timeout": "讀取逾時秒數",
      "antenna.scan_duration": "掃描秒數",
      "antenna.macs": "CONNECT 用設備 MAC / ID",
      "antenna.connect": "CONNECT 選定設備",
      "antenna.reconnect_configured": "CONNECT 已設定設備",
      "antenna.report_interval": "回報週期 ms",
      "antenna.report": "設定回報週期",
      "antenna.raw": "原始命令",
      "antenna.send_raw": "送出原始命令",
      "antenna.command_status": "命令狀態",
      "antenna.output": "UART 回應",
      "antenna.ready": "準備送出 UART 命令。",
      "antenna.running": "正在送出 {command} 到 {port}。",
      "antenna.complete": "{command} 完成，收到 {count} 行。",
      "antenna.complete_state": "完成",
      "antenna.failed": "命令失敗",
      "antenna.idle": "閒置",
      "monitor.title": "運行監測",
      "monitor.refresh": "重新整理",
      "monitor.status": "固定設備即時欄位",
      "monitor.empty": "尚未設定設備綁定。",
      "monitor.failed": "監測資料讀取失敗",
      "monitor.waiting": "等待中",
      "monitor.live": "即時",
      "monitor.stale": "閒置（無數據）",
      "monitor.name": "名稱",
      "monitor.type": "類型",
      "monitor.mac": "MAC",
      "monitor.channel": "UART",
      "monitor.speed": "速度",
      "monitor.distance": "距離",
      "monitor.power": "功率",
      "monitor.cadence": "步頻",
      "monitor.rssi": "RSSI",
      "monitor.calories": "熱量",
      "monitor.updated": "更新時間",
      "bindings.title": "設備綁定",
      "bindings.node_id": "Edge Node",
      "bindings.name": "顯示名稱",
      "bindings.type": "設備類型",
      "bindings.channel": "UART 通道",
      "bindings.target": "BLE 目標 / MAC",
      "bindings.save": "儲存並套用",
      "bindings.restart": "重啟 Edge runtime",
      "bindings.ready": "在這裡修改設定，儲存後會自動重啟 Edge runtime 套用。",
      "bindings.saved": "設備綁定已儲存，正在重啟 Edge runtime...",
      "bindings.restarted": "Edge runtime 已重啟，設定已套用。",
      "bindings.failed": "設定更新失敗",
      "bindings.unbind": "解綁",
      "bindings.unbind_confirm": "確定解綁 {name}？將從天線板斷開此設備連線。",
      "bindings.unbinding": "解綁中——更新天線板目標清單...",
      "bindings.unbound": "{name} 已解綁，Edge runtime 已重啟。",
      "bindings.filtered": "目前只顯示 {channel} 的設備（{count} 台），切換通道或選 Manual serial port 可檢視全部。"
    };
    ["it", "fr", "de-CH", "sv"].forEach((locale) => {
      dictionaries[locale] = { ...dictionaries["en-US"] };
    });

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
      antennaState.textContent = t("antenna.idle");
      if (!antennaMessage.classList.contains("error") && !antennaMessage.classList.contains("ok")) {
        antennaMessage.textContent = t("antenna.ready");
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

    function renderMonitorField(labelKey, value, className = "") {
      return `
        <div class="monitor-field ${className}">
          <span>${escapeHtml(t(labelKey))}</span>
          <strong>${escapeHtml(value)}</strong>
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

    function renderMonitorEquipment() {
      const bindings = Array.isArray(edgeConfig?.equipment_bindings) ? edgeConfig.equipment_bindings : [];
      const liveCount = bindings.filter((binding) => {
        const payload = monitorLatestByNode.get(binding.node_id);
        return payload?.node_id && monitorTelemetryAgeMs(payload) <= MONITOR_LIVE_WINDOW_MS;
      }).length;
      monitorCount.textContent = `${liveCount}/${bindings.length}`;
      if (!bindings.length) {
        monitorGrid.innerHTML = `<div class="empty">${escapeHtml(t("monitor.empty"))}</div>`;
        return;
      }
      monitorGrid.innerHTML = bindings.map((binding) => {
        const payload = monitorLatestByNode.get(binding.node_id) || {};
        const displayPayload = monitorDisplayedByNode.get(binding.node_id) || payload;
        const status = monitorStatusForPayload(payload);
        const updated = formatEventTime(payload.timestamp_epoch_ms);
        return `
          <div class="monitor-card" data-node-id="${escapeHtml(binding.node_id)}">
            <div class="monitor-card-header">
              <div class="monitor-equipment-name">${escapeHtml(binding.equipment_id || binding.node_id)}</div>
              <div class="monitor-status-pill ${status.className}">${escapeHtml(status.label)}</div>
            </div>
            <div class="monitor-fields">
              ${renderMonitorField("monitor.name", binding.equipment_id || "--")}
              ${renderMonitorField("monitor.type", binding.equipment_type ? typeLabel(binding.equipment_type) : "--")}
              ${renderMonitorField("monitor.mac", payload.mac_address || binding.ble_target || "--", "wide")}
              ${renderMonitorField("monitor.channel", binding.antenna_channel || "--")}
              ${renderMonitorField("monitor.updated", updated)}
              ${renderMonitorField("monitor.speed", formatMetric(displayPayload.instantaneous_speed_kph, " kph", 2))}
              ${renderMonitorField("monitor.distance", formatMetric(displayPayload.distance_m, " m", 0))}
              ${renderMonitorField("monitor.power", formatMetric(displayPayload.power_watts, " W", 0))}
              ${renderMonitorField("monitor.cadence", formatMetric(displayPayload.cadence_rpm, " rpm", 0))}
              ${renderMonitorField("monitor.rssi", formatMetric(displayPayload.rssi, " dBm", 0))}
              ${renderMonitorField("monitor.calories", formatMetric(displayPayload.calories ?? displayPayload.total_energy_kcal, " kcal", 0))}
            </div>
          </div>
        `;
      }).join("");
    }

    let monitorLastFrameMs = performance.now();
    function animateMonitorEquipment(frameMs) {
      const deltaMs = frameMs - monitorLastFrameMs;
      monitorLastFrameMs = frameMs;
      updateMonitorDisplayedPayloads(deltaMs);
      renderMonitorEquipment();
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
        }
      });
      renderMonitorEquipment();
    }

    async function refreshMonitorEvents() {
      try {
        const response = await fetch("/api/monitor/events?limit=200", {
          headers: adminHeaders(),
        });
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

    function updateChannelOccupancy() {
      const counts = channelBindingCounts();
      const channelById = new Map(antennaChannels.map((channel) => [channel.port, channel.id]));
      Array.from(antennaChannelSelect.options).forEach((option) => {
        const id = channelById.get(option.value);
        if (!id) return;
        const used = counts.get(id) || 0;
        option.textContent = `${id} (${option.value}) · ${used}/${ANTENNA_MAX_CONNECTIONS}`;
      });
      if (!antennaChannels.length) {
        antennaChannelLoad.textContent = "";
        return;
      }
      const summary = antennaChannels.map((channel) => {
        const used = counts.get(channel.id) || 0;
        const mark = used >= ANTENNA_MAX_CONNECTIONS ? " ⚠" : "";
        return `${channel.id}: ${used}/${ANTENNA_MAX_CONNECTIONS}${mark}`;
      }).join(" · ");
      antennaChannelLoad.textContent = `${t("antenna.channel_load", { max: ANTENNA_MAX_CONNECTIONS })} — ${summary}`;
      const selectedId = channelById.get(antennaChannelSelect.value) || "";
      renderSelectedChannel(selectedId, counts);
    }

    function renderSelectedChannel(channelId, counts) {
      let visible = 0;
      document.querySelectorAll(".binding-row").forEach((row) => {
        row.classList.remove("channel-active", "channel-dim");
        if (!channelId) return;
        const match = row.dataset.channel === channelId;
        row.classList.add(match ? "channel-active" : "channel-dim");
        if (match) visible += 1;
      });
      const hint = document.getElementById("binding-filter-hint");
      if (hint) {
        hint.textContent = channelId ? t("bindings.filtered", { channel: channelId, count: visible }) : "";
      }
      if (!channelId) {
        antennaChannelSlots.textContent = "";
        antennaChannelSlots.className = "channel-slots";
        return;
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
      antennaChannelSelect.innerHTML = `<option value="">Manual serial port</option>`;
      channels.forEach((channel) => {
        const option = document.createElement("option");
        option.value = channel.port;
        option.textContent = `${channel.id} (${channel.port})`;
        option.dataset.baudrate = channel.baudrate || "";
        option.dataset.rtscts = channel.rtscts ? "1" : "0";
        antennaChannelSelect.appendChild(option);
      });
      if (config.default_port) {
        antennaPortInput.value = config.default_port;
        antennaChannelSelect.value = config.default_port;
      }
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

    function renderBindings(config) {
      edgeConfig = config;
      configNodeId.textContent = config.node_id || "--";
      const bindings = Array.isArray(config.equipment_bindings) ? config.equipment_bindings : [];
      if (!bindings.length) {
        bindingList.innerHTML = `<div class="empty">No equipment bindings configured.</div>`;
        renderMonitorEquipment();
        updateChannelOccupancy();
        return;
      }
      bindingList.innerHTML = bindings.map((binding, index) => `
        <div class="binding-row" data-index="${index}" data-channel="${escapeHtml(binding.antenna_channel || "")}">
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
      renderMonitorEquipment();
      updateChannelOccupancy();
    }

    async function loadEdgeConfig() {
      try {
        const response = await fetch("/api/config", {
          headers: adminHeaders(),
        });
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
      return {
        ...edgeConfig,
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
        const response = await fetch("/api/config", {
          method: "POST",
          headers: adminHeaders({ "Content-Type": "application/json" }),
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

    async function restartEdgeRuntime() {
      configSaveBtn.disabled = true;
      try {
        const response = await fetch("/api/system/power/restart-service", {
          method: "POST",
          headers: adminHeaders({ "Content-Type": "application/json" }),
          body: "{}",
        });
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.detail || "Restart failed");
        }
        setConfigMessage(t("bindings.restarted"), "ok");
      } catch (error) {
        setConfigMessage(error.message, "error");
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

    async function loadAntennaConfig() {
      try {
        const response = await fetch("/api/antenna/config", {
          headers: adminHeaders(),
        });
        if (!response.ok) return;
        renderAntennaConfig(await response.json());
      } catch (_error) {
        // Keep the fallback value already rendered in the input.
      }
    }

    function buildAntennaPayload(command) {
      const port = antennaPortInput.value.trim();
      if (!port) {
        throw new Error("Serial port is required");
      }

      const payload = {
        port,
        command,
        baudrate: Math.max(9600, Number(antennaBaudrateInput.value) || 115200),
        rtscts: antennaRtsctsInput.checked,
        timeout_sec: Math.max(1, Math.min(30, Number(antennaTimeoutInput.value) || 5)),
        scan_duration_sec: Math.max(1, Math.min(30, Number(antennaScanDurationInput.value) || 5)),
      };

      if (command === "connect") {
        payload.macs = antennaMacsInput.value
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean);
        if (!payload.macs.length) {
          throw new Error("Enter at least one device MAC or ID for CONNECT");
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

    function setAntennaButtonsDisabled(disabled) {
      allAntennaButtons.forEach((button) => {
        button.disabled = disabled;
      });
    }

    const scanWizard = document.getElementById("scan-wizard");
    const wizardTitle = document.getElementById("wizard-title");
    const wizardBody = document.getElementById("wizard-body");
    const wizardCloseBtn = document.getElementById("wizard-close");
    let wizardDevices = [];
    let wizardScanChannelId = "";
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

    function openScanWizard(result) {
      const byAddress = new Map();
      (result.parsed || []).forEach((entry) => {
        if (entry.type !== "device" || !entry.address) return;
        const key = entry.address.toUpperCase();
        const previous = byAddress.get(key);
        if (!previous || (entry.rssi ?? -999) > (previous.rssi ?? -999)) {
          byAddress.set(key, entry);
        }
      });
      wizardDevices = Array.from(byAddress.values());
      if (!wizardDevices.length) return;
      const channelByPort = new Map(antennaChannels.map((channel) => [channel.port, channel.id]));
      wizardScanChannelId = channelByPort.get(result.port) || "";
      renderWizardStep1();
      showWizardAt(document.querySelector('button[data-command="scan"]'));
    }

    function renderWizardStep1() {
      wizardTitle.textContent = t("wizard.title1");
      const bound = boundTargetSet();
      wizardBody.innerHTML = `
        <div class="wizard-step-hint">${escapeHtml(t("wizard.hint1"))}</div>
        <div class="wizard-list">
          ${wizardDevices.map((device, index) => {
            const isBound = bound.has(device.address.toUpperCase());
            return `
              <div class="wizard-device">
                <div>
                  <div>${escapeHtml(device.name || device.address)}</div>
                  <div class="meta">${escapeHtml(device.address)} · RSSI ${escapeHtml(String(device.rssi ?? "--"))} dBm · ${escapeHtml(device.device_type || "UNKNOWN")}</div>
                </div>
                <button type="button" data-device-index="${index}" ${isBound ? "disabled" : ""}>
                  ${escapeHtml(isBound ? t("wizard.bound") : t("wizard.add"))}
                </button>
              </div>
            `;
          }).join("")}
        </div>
      `;
      wizardBody.querySelectorAll("[data-device-index]").forEach((button) => {
        button.addEventListener("click", () => renderWizardStep2(wizardDevices[Number(button.dataset.deviceIndex)]));
      });
    }

    function renderWizardStep2(device) {
      wizardTitle.textContent = t("wizard.title2");
      const counts = channelBindingCounts();
      const freeChannels = antennaChannels.filter((channel) => (counts.get(channel.id) || 0) < ANTENNA_MAX_CONNECTIONS);
      const allFull = antennaChannels.length > 0 && !freeChannels.length;
      const preferredChannel = freeChannels.some((channel) => channel.id === wizardScanChannelId)
        ? wizardScanChannelId
        : (freeChannels[0]?.id || "");
      const defaultName = (device.name && device.name !== "UNKNOWN") ? device.name : device.address;
      const defaultType = WIZARD_TYPE_MAP[(device.device_type || "").toUpperCase()] || "treadmill";
      wizardBody.innerHTML = `
        <div class="wizard-step-hint">${escapeHtml(device.address)} · RSSI ${escapeHtml(String(device.rssi ?? "--"))} dBm</div>
        <div class="field">
          <label>${escapeHtml(t("bindings.name"))}</label>
          <input id="wizard-name" type="text" value="${escapeHtml(defaultName)}" autocomplete="off">
        </div>
        <div class="field">
          <label>${escapeHtml(t("bindings.type"))}</label>
          <select id="wizard-type">
            ${equipmentTypeOptions(defaultType)}
          </select>
        </div>
        <div class="field">
          <label>${escapeHtml(t("bindings.channel"))}</label>
          <select id="wizard-channel">
            ${antennaChannels.map((channel) => {
              const used = counts.get(channel.id) || 0;
              const full = used >= ANTENNA_MAX_CONNECTIONS;
              return `<option value="${escapeHtml(channel.id)}" ${channel.id === preferredChannel ? "selected" : ""} ${full && !allFull ? "disabled" : ""}>${escapeHtml(channel.id)} (${escapeHtml(channel.port)}) · ${used}/${ANTENNA_MAX_CONNECTIONS}${full ? " ⚠" : ""}</option>`;
            }).join("")}
          </select>
        </div>
        ${allFull ? `
          <div class="field">
            <label>${escapeHtml(t("wizard.replace"))}</label>
            <select id="wizard-replace">
              ${(edgeConfig?.equipment_bindings || []).map((binding, index) => (
                `<option value="${index}">${escapeHtml(binding.equipment_id)} (${escapeHtml(binding.antenna_channel)} · ${escapeHtml(binding.ble_target)})</option>`
              )).join("")}
            </select>
          </div>
        ` : ""}
        <div class="wizard-actions">
          <button type="button" class="button-secondary" id="wizard-back">${escapeHtml(t("wizard.back"))}</button>
          <button type="button" id="wizard-confirm">${escapeHtml(t("wizard.confirm"))}</button>
        </div>
      `;
      document.getElementById("wizard-back").addEventListener("click", renderWizardStep1);
      document.getElementById("wizard-confirm").addEventListener("click", () => applyWizardBinding(device, allFull));
    }

    function nextWizardNodeId() {
      const matches = (edgeConfig?.equipment_bindings || [])
        .map((binding) => String(binding.node_id || "").match(/^(.*?)([0-9]+)$/))
        .filter(Boolean);
      if (matches.length) {
        const prefix = matches[matches.length - 1][1];
        const samePrefix = matches.filter((match) => match[1] === prefix);
        const next = Math.max(...samePrefix.map((match) => Number(match[2]))) + 1;
        return prefix + String(next).padStart(matches[matches.length - 1][2].length, "0");
      }
      return `${edgeConfig?.node_id || "edge"}-${String((edgeConfig?.equipment_bindings || []).length + 1).padStart(2, "0")}`;
    }

    async function applyWizardBinding(device, allFull) {
      const name = document.getElementById("wizard-name").value.trim();
      const type = document.getElementById("wizard-type").value;
      const channelId = document.getElementById("wizard-channel").value;
      if (!name || !channelId) return;
      const binding = {
        node_id: nextWizardNodeId(),
        equipment_id: name,
        equipment_type: type,
        ble_target: device.address,
        antenna_channel: channelId,
      };
      const bindings = [...(edgeConfig?.equipment_bindings || [])];
      if (allFull) {
        const replaceIndex = Number(document.getElementById("wizard-replace").value);
        binding.node_id = bindings[replaceIndex].node_id;
        bindings[replaceIndex] = binding;
      } else {
        bindings.push(binding);
      }
      edgeConfig = { ...edgeConfig, equipment_bindings: bindings, max_ftms_connections: bindings.length };
      renderBindings(edgeConfig);
      const saved = await saveEdgeConfig();
      if (saved) {
        await restartEdgeRuntime();
        renderWizardStep3(name);
      } else {
        closeScanWizard();
      }
    }

    function renderWizardStep3(name) {
      wizardTitle.textContent = t("wizard.title3");
      wizardBody.innerHTML = `
        <div class="wizard-step-hint">${escapeHtml(t("wizard.saved_hint", { name }))}</div>
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
        const response = await fetch("/api/wifi/networks", { headers: adminHeaders() });
        payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Wi-Fi scan failed");
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
          const response = await fetch("/api/wifi/connect", {
            method: "POST",
            headers: adminHeaders({ "Content-Type": "application/json" }),
            body: JSON.stringify({ ssid: net.ssid, password }),
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Connect failed");
          resultBox.textContent = t("wifi.connect_ok", { ssid: net.ssid, ip: result.ip || "?" });
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
      antennaOutput.textContent = "Waiting for UART response...";

      try {
        const response = await fetch("/api/antenna/command", {
          method: "POST",
          headers: adminHeaders({ "Content-Type": "application/json" }),
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
      antennaOutput.textContent = "Waiting for UART response...";

      try {
        const response = await fetch("/api/antenna/reconnect-configured", {
          method: "POST",
          headers: adminHeaders({ "Content-Type": "application/json" }),
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
        const response = await fetch("/api/wifi/status?interface=wlan0", {
          headers: adminHeaders(),
        });
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

    antennaCommandButtons.forEach((button) => {
      button.addEventListener("click", () => runAntennaCommand(button.dataset.command));
    });
    antennaChannelSelect.addEventListener("change", () => {
      const selected = antennaChannelSelect.selectedOptions[0];
      if (selected && selected.value) {
        antennaPortInput.value = selected.value;
        if (selected.dataset.baudrate) {
          antennaBaudrateInput.value = selected.dataset.baudrate;
        }
        antennaRtsctsInput.checked = selected.dataset.rtscts === "1";
      }
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
        await fetch("/api/antenna/reconnect-configured", {
          method: "POST",
          headers: adminHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            disconnect_first: true,
            timeout_sec: Math.max(1, Math.min(30, Number(antennaTimeoutInput.value) || 5)),
            report_interval_ms: Math.max(100, Math.min(10000, Number(antennaReportIntervalInput.value) || ANTENNA_DEFAULT_REPORT_INTERVAL_MS)),
          }),
        });
      } catch (_error) {
        // board list refresh is best-effort; runtime restart below reloads config
      }
      await restartEdgeRuntime();
      setConfigMessage(t("bindings.unbound", { name: binding.equipment_id }), "ok");
    });
    antennaConnectBtn.addEventListener("click", () => runAntennaCommand("connect"));
    antennaReconnectConfiguredBtn.addEventListener("click", reconnectConfiguredDevices);
    antennaReportBtn.addEventListener("click", () => runAntennaCommand("report"));
    antennaRawBtn.addEventListener("click", () => runAntennaCommand("raw"));
    configSaveBtn.addEventListener("click", saveAndApplyBindings);
    monitorRefreshBtn.addEventListener("click", refreshMonitorEvents);

    applyTranslations();
    loadAntennaConfig();
    loadEdgeConfig();
    refreshWifiStatus();
    refreshMonitorEvents();
    setInterval(refreshWifiStatus, 5000);
    setInterval(refreshMonitorEvents, MONITOR_REFRESH_MS);
    requestAnimationFrame(animateMonitorEquipment);
  </script>
</body>
</html>
"""
