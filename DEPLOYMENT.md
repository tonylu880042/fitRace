# FitRaceStudio Deployment Configuration

This document defines the deployment network, device naming, discovery, and setup rules for FitRaceStudio hardware shipped with the dedicated AP.

## 1. Deployment Decision

FitRaceStudio ships with a professional AP. Network provisioning is completed before delivery, and Edge Nodes are configured through their local web services on the shipped LAN.

All deployed devices join the same local LAN:

```text
SSID: fitRace26
Central Hub: joins fitRace26
Edge Nodes: join fitRace26
Setup phone/tablet: joins fitRace26
Dashboard screen: joins fitRace26
```

BLE is used only for FTMS equipment telemetry capture through the antenna board. The RPi product path controls the antenna board over UART instead of connecting through a USB Bluetooth dongle directly:

```text
Up to 5 Fitness Equipment -> BLE / FTMS -> Antenna Board -> UART -> Edge Node -> Wi-Fi / MQTT -> Central Hub
```

## 2. Recommended Network Plan

Use a predictable AP subnet for shipped systems.

```text
SSID: fitRace26
Subnet: 192.168.26.0/24
Gateway/AP: 192.168.26.1
Central Hub: 192.168.26.10
Edge Node web setup port: 8001
Hub API/dashboard port: 8000
MQTT port: 1883
```

Recommended DHCP reservations:

| Device | Hostname | Suggested IP |
| --- | --- | --- |
| Central Hub | fitrace-hub | 192.168.26.10 |
| Edge Node 01 | fitrace-edge-01 | 192.168.26.101 |
| Edge Node 02 | fitrace-edge-02 | 192.168.26.102 |
| Edge Node 03 | fitrace-edge-03 | 192.168.26.103 |

The exact IP range can change per product batch, but each shipped system should have one documented subnet and reservation table.

## 3. Device Naming

IPv4 does not name devices. Device names are provided by hostname, mDNS, DHCP reservation labels, DNS records, and application-level registration.

Every Edge Node must have one stable identity shared across all layers:

```text
node_id: fitrace-edge-01
hostname: fitrace-edge-01
mDNS name: fitrace-edge-01.local
MQTT client id: fitrace-edge-01
MQTT status topic: fitrace/nodes/fitrace-edge-01/status
Web setup URL: http://fitrace-edge-01.local:8001/
```

Recommended naming pattern:

```text
fitrace-edge-{station_number}
```

Examples:

```text
fitrace-edge-01
fitrace-edge-02
fitrace-edge-03
```

Use lowercase hostnames and hyphens. Avoid underscores in hostnames.

## 4. Raspberry Pi Provisioning

Each RPi Edge Node should be provisioned before shipment.

Install Python dependencies from the project metadata instead of copying a local
`.venv` between devices:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
```

For production images that do not need test tooling, use:

```bash
.venv/bin/python -m pip install -e .
```

Set hostname:

```bash
sudo hostnamectl set-hostname fitrace-edge-01
```

Install and enable mDNS:

```bash
sudo apt install avahi-daemon
sudo systemctl enable --now avahi-daemon
```

Provision Wi-Fi for the shipped AP:

```text
SSID: fitRace26
Password: configured per shipped system
```

The AP credential should be provisioned during manufacturing or pre-delivery setup. Normal field setup should not require entering AP credentials.

## 5. mDNS Discovery

Each Edge Node should be reachable by hostname on the local LAN:

```text
http://fitrace-edge-01.local:8001/
```

Each Edge Node should also advertise a service for discovery:

```text
Service type: _fitrace-edge._tcp.local
Port: 8001
TXT records:
  node_id=fitrace-edge-01
  role=edge
  api_version=1
```

Central Hub can use mDNS as a discovery path, but it should not rely on mDNS alone.

## 6. Edge Node Web Setup

Each Edge Node hosts a local web setup service on the `fitRace26` LAN.

Recommended endpoints:

```text
GET  /api/config
POST /api/config
GET  /api/status
POST /api/antenna/command
GET  /api/ble/scan        # troubleshooting only
POST /api/restart
```

Production setup and maintenance endpoints that can expose local device inventory or change device state must require the local admin token. The current Edge setup API expects the same header used by the Hub admin pages:

```text
X-FitRace-Admin-Token: <local-admin-token>
```

Set this token through `FITRACE_ADMIN_TOKEN` on both Hub and Edge services.

Recommended config fields:

```json
{
  "node_id": "fitrace-edge-01",
  "hub_host": "192.168.26.10",
  "mqtt_host": "192.168.26.10",
  "mqtt_port": 1883,
  "web_config_port": 8001,
  "max_ftms_connections": 5,
  "available_channels": 2,
  "antenna_protocol_version": "uart-ftms-json-v1.1",
  "antenna_channels": [
    {
      "id": "uart-1",
      "port": "/dev/ttyAMA0",
      "uart": "UART0",
      "tx_gpio": "GPIO14",
      "rx_gpio": "GPIO15",
      "baudrate": 115200,
      "rtscts": false
    },
    {
      "id": "uart-2",
      "port": "/dev/ttyAMA4",
      "uart": "UART4",
      "tx_gpio": "GPIO12",
      "rx_gpio": "GPIO13",
      "baudrate": 115200,
      "rtscts": false,
      "dtoverlay": "uart4-pi5"
    }
  ],
  "equipment_bindings": [
    {
      "node_id": "fitrace-edge-01-01",
      "equipment_id": "BIKE_01",
      "equipment_type": "fan_bike",
      "ble_target": "BIKE_01_4A21",
      "antenna_channel": "uart-1"
    },
    {
      "node_id": "fitrace-edge-01-02",
      "equipment_id": "TREAD_01",
      "equipment_type": "treadmill",
      "ble_target": "TREAD_01_8F3A",
      "antenna_channel": "uart-2"
    }
  ]
}
```

`node_id` at the top level identifies the physical Edge Node. `equipment_bindings[*].node_id` identifies each independent telemetry stream sent to Central Hub. A single Edge Node may bind at most 5 FTMS devices.

The setup page should show:

```text
Node ID
Hostname
Current IP
Hub reachability
MQTT connection status
FTMS / antenna board connection status
Last telemetry timestamp
Firmware/software version
```

### UART Antenna Board Control

The product Edge Node path controls the antenna board over UART. The local setup page exposes these commands through:

```text
POST /api/antenna/command
X-FitRace-Admin-Token: <local-admin-token>
```

Request body:

```json
{
  "port": "/dev/ttyAMA0",
  "command": "scan",
  "baudrate": 115200,
  "rtscts": false,
  "timeout_sec": 5,
  "scan_duration_sec": 5,
  "macs": ["AA:BB:CC:DD:EE:01"],
  "report_interval_ms": 1000,
  "raw_command": "STATUS;"
}
```

The configured production UART channels are:

| Channel | UART | Port | TX GPIO | RX GPIO | Raspberry Pi overlay |
|---|---|---|---|---|---|
| Channel | Pi model | UART | Port | TX GPIO | RX GPIO | Raspberry Pi overlay |
|---|---|---|---|---|---|---|
| `uart-1` | Pi 4 | UART0 | `/dev/ttyAMA0` | GPIO14 | GPIO15 | built in / primary UART |
| `uart-1` | Pi 5 | UART0 | `/dev/ttyAMA0` | GPIO14 | GPIO15 | `dtoverlay=uart0-pi5` after disabling serial console |
| `uart-2` | Pi 4 | UART5 | `/dev/ttyAMA4` | GPIO12 | GPIO13 | `dtoverlay=uart5` |
| `uart-2` | Pi 5 | UART4 | `/dev/ttyAMA4` | GPIO12 | GPIO13 | `dtoverlay=uart4-pi5` |

Pi 5 UART0 deployment gotcha: on a fresh Raspberry Pi OS image, GPIO14/GPIO15 may be reserved for the Linux serial console. In that state `/dev/serial0` can open, but `PING;` will not reach the antenna board because `console=serial0,115200` and `serial-getty@ttyAMA10.service` own the port, and pin mux may show GPIO14/GPIO15 as `none`. After enabling `dtoverlay=uart0-pi5`, use `/dev/ttyAMA0` for GPIO14/GPIO15; `/dev/serial0` may still point to another internal UART such as `/dev/ttyAMA10`.

Before using `uart-1` on Pi 5:

```bash
sudo cp /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.fitrace-bak
sudo sed -i 's/ console=serial0,115200//g; s/console=serial0,115200 //g' /boot/firmware/cmdline.txt
sudo systemctl disable --now serial-getty@ttyAMA10.service || true
sudo systemctl disable --now serial-getty@serial0.service || true

if ! grep -qxF 'dtoverlay=uart0-pi5' /boot/firmware/config.txt; then
  echo 'dtoverlay=uart0-pi5' | sudo tee -a /boot/firmware/config.txt
fi

sudo reboot
```

After reboot, verify:

```bash
pinctrl get 14
pinctrl get 15
ls -l /dev/serial0 /dev/ttyAMA*
```

Expected: GPIO14/GPIO15 are assigned to `TXD0/RXD0`, `serial-getty` is inactive, `/dev/ttyAMA0` exists, and `POST /api/antenna/command` with `{"port":"/dev/ttyAMA0","command":"ping"}` can receive `BOOT:*` from the antenna board.

Only fields required by the selected command need to be present. The Edge Node opens the serial port, sends the command sequence, reads line-based UART responses, parses each response, and closes the port.

Supported commands:

| API `command` | UART command sent | Required fields | Notes |
|---|---|---|---|
| `ping` | `PING;` | none | Board returns `BOOT:HAS_LIST,count=<N>;` or `BOOT:NO_LIST;`. |
| `scan` | `SCAN:START;`, then `SCAN:STOP;` | `scan_duration_sec` optional | Edge Node reads `DEVICE:` lines during the scan window. |
| `connect` | `CONNECT:<MAC...>;` | `macs` non-empty | Edge Node does not cap the MAC count; antenna board firmware may still ignore devices beyond its own capacity. |
| `disconnect_all` | `DISCONNECT:ALL;` | none | Clears board-side connections/list. |
| `report` | `REPORT:<ms>;` | `report_interval_ms` | Allowed range is 100-10000 ms. |
| `status` | `STATUS;` | none | Used for diagnostics and future health checks. |
| `version` | `VERSION;` | none | Returns firmware version. |
| `reboot` | `REBOOT;` | none | Board may briefly disconnect after `REBOOT:OK;`. |
| `raw` | caller-provided command | `raw_command` | Edge Node appends `;` and `\r\n` if needed. |

Successful response:

```json
{
  "port": "/dev/ttyAMA0",
  "baudrate": 115200,
  "rtscts": false,
  "command": "scan",
  "elapsed_sec": 5.214,
  "tx": ["SCAN:START;", "SCAN:STOP;"],
  "rx": [
    "SCAN:OK;",
    "DEVICE:AA:BB:CC:DD:EE:01,-55,Treadmill-01,TMILL;",
    "SCAN:OK;"
  ],
  "parsed": [
    {"type": "ok", "command": "SCAN", "raw": "SCAN:OK;"},
    {
      "type": "device",
      "address": "AA:BB:CC:DD:EE:01",
      "rssi": -55,
      "name": "Treadmill-01",
      "device_type": "TMILL",
      "raw": "DEVICE:AA:BB:CC:DD:EE:01,-55,Treadmill-01,TMILL;"
    },
    {"type": "ok", "command": "SCAN", "raw": "SCAN:OK;"}
  ]
}
```

UART line format is ASCII, semicolon-terminated, and newline-delimited when sent over serial.

Control response schemas:

| UART response | Parsed `type` | Parsed fields |
|---|---|---|
| `BOOT:HAS_LIST,count=2;` | `boot` | `has_list=true`, `count=2` |
| `BOOT:NO_LIST;` | `boot` | `has_list=false`, `count=0` |
| `DEVICE:<MAC>,<RSSI>,<NAME>,<TYPE>;` | `device` | `address`, `rssi`, `name`, `device_type` |
| `BLE_DEVICE:<MAC>,<RSSI>,<NAME>[,<TYPE>];` | `device` | Same as `DEVICE`; missing type becomes `UNKNOWN`. |
| `STATUS:REPORT,2/3;` | `status` | `state=REPORT`, `connected=2`, `target=3` |
| `STATUS:REPORT,conn=2,target=3;` | `status` | Legacy-compatible form; normalized to `connected` and `target`. |
| `VERSION:1.1.0;` | `version` | `version=1.1.0` |
| `<COMMAND>:OK;` | `ok` | `command=<COMMAND>` |
| `ERROR:...;` or `<COMMAND>:ERROR:...;` | `error` | `message` contains the raw error line. |

FTMS telemetry response:

```text
FTMS:<MAC>,<TYPE>,{json};
```

Supported `TYPE` values are `TMILL`, `BIKE`, `ROWER`, `ELLIP`, and `UNKNOWN`.

Speed-based equipment (`TMILL`, `BIKE`, `ELLIP`) uses:

```json
{
  "rssi": -55,
  "instantaneous_speed": 8.32,
  "total_distance": 1204,
  "instantaneous_power": 142,
  "total_energy": 37
}
```

Rower equipment uses `stroke_rate` and `instantaneous_pace` instead of `instantaneous_speed`:

```json
{
  "rssi": -60,
  "stroke_rate": 24.5,
  "total_distance": 850,
  "instantaneous_pace": 125,
  "instantaneous_power": 98,
  "total_energy": 22
}
```

Edge Node keeps the original firmware JSON in `payload` and also normalizes common fields for UI/API consumers:

| Parsed field | Source |
|---|---|
| `equipment_type` | `TMILL=treadmill`, `BIKE=fan_bike`, `ROWER=rowing_machine`, `ELLIP=elliptical`, `UNKNOWN=unknown` |
| `rssi` | `payload.rssi` |
| `instantaneous_speed_kph` | `payload.instantaneous_speed`, default `0.0` |
| `cadence_rpm` | `payload.stroke_rate`, default `0.0` |
| `pace_sec_per_500m` | `payload.instantaneous_pace` |
| `distance_m` | `payload.total_distance`, default `0.0` |
| `power_watts` | `payload.instantaneous_power`, default `0` |
| `total_energy_kcal` | `payload.total_energy`, default `0` |

Malformed FTMS JSON is returned as:

```json
{
  "type": "invalid",
  "message_type": "telemetry",
  "reason": "invalid_json",
  "raw": "FTMS:AA:BB:CC:DD:EE:01,BIKE,{\"rssi\":-62"
}
```

### FTMS Scan Command (USB Dongle Troubleshooting)

The current `/api/ble/scan` endpoint is a protected troubleshooting tool. It requires `FITRACE_ADMIN_TOKEN` when the token is configured.

Recommended request:

```text
GET /api/ble/scan?adapter=hci1&timeout_sec=5
```

Default behavior:

```text
adapter: hci1
timeout_sec: 5
include_all: false
```

`hci1` is the recommended USB Bluetooth dongle adapter. The onboard adapter, if present, may appear as `hci0`.

Recommended response:

```json
{
  "adapter": "hci1",
  "timeout_sec": 5.0,
  "include_all": false,
  "devices": [
    {
      "address": "AA:BB:CC:DD:EE:01",
      "name": "FTMS Bike",
      "rssi": -45,
      "service_uuids": ["00001826-0000-1000-8000-00805f9b34fb"],
      "matched_services": ["00001826-0000-1000-8000-00805f9b34fb"]
    }
  ]
}
```

If a machine does not advertise the FTMS service UUID, technicians can use:

```text
GET /api/ble/scan?adapter=hci1&timeout_sec=5&include_all=true
```

This returns all visible BLE devices and should be treated as a troubleshooting mode, not the default equipment binding flow.

## 7. Central Hub Node Discovery

Central Hub should find Edge Nodes using layered discovery.

Recommended priority:

```text
1. MQTT heartbeat / node registry
2. mDNS service discovery: _fitrace-edge._tcp.local
3. DHCP reservation / AP client list
4. Subnet scan fallback
```

The primary source of truth should be active Edge Node registration or heartbeat. mDNS and AP records are discovery aids, not race-state truth.

## 8. MQTT Registration and Heartbeat

Each Edge Node should publish an online status message after boot and continue publishing heartbeat messages.

Recommended topic:

```text
fitrace/nodes/{node_id}/status
```

Recommended payload:

```json
{
  "edge_node_id": "fitrace-edge-01",
  "hostname": "fitrace-edge-01",
  "ip": "192.168.26.101",
  "status": "online",
  "software_version": "0.1.0",
  "antenna_protocol_version": "pending-hardware",
  "max_ftms_connections": 5,
  "available_channels": 2,
  "last_seen_epoch_ms": 1760000000000,
  "equipment_streams": [
    {
      "node_id": "fitrace-edge-01-01",
      "equipment_id": "BIKE_01",
      "equipment_type": "fan_bike",
      "status": "configured",
      "antenna_channel": "uart-1",
      "rssi": null,
      "last_telemetry_epoch_ms": null,
      "error_code": null
    }
  ]
}
```

Recommended heartbeat interval:

```text
1 - 5 seconds during active race operation
5 - 15 seconds during idle operation
```

Central Hub should mark a node offline if no heartbeat is received after the configured timeout, for example 10 seconds.

Central Hub exposes the current registry through:

```text
GET /api/nodes
```

## 9. Hub-Led OTA Updates

When Central Hub has public internet access, it should be responsible for checking the public update service, downloading signed release artifacts, and coordinating Edge Node updates over the local `fitRace26` LAN.

The recommended update path is:

```text
Public update server -> Central Hub -> Edge Nodes
```

Central Hub should:

```text
Check signed update manifest over HTTPS
Download Hub and Edge artifacts
Verify manifest signature and artifact checksums
Install its own update only while the system is idle
Restart application services for normal Python app updates
Require full OS reboot only for OS, driver, firmware, or kernel-level updates
Serve verified Edge artifacts to Edge Nodes over the LAN
Update Edge Nodes sequentially or in small batches
Track update status through Edge heartbeat
Keep the previous release for rollback
```

Edge Nodes should not need direct public internet access for ordinary updates. Each Edge Node should run a small local update agent that accepts authenticated update commands from the Hub, stages the artifact, restarts local services, and reports progress through status endpoints or MQTT heartbeat.

Updates must be blocked while a race is running or while telemetry is actively needed for a race. Full update design, manifest schema, rollout rules, API recommendations, and rollback requirements are documented in [OTA_UPDATE.md](OTA_UPDATE.md). AWS/S3 cloud requirements are documented in [AWS_CLOUD_REQUIREMENTS.md](AWS_CLOUD_REQUIREMENTS.md).

## 10. Management Power Controls

Central Hub management UI should provide controlled power actions for maintenance:

```text
Restart Hub service
Reboot Central Hub
Shutdown Central Hub
Restart selected Edge Node services
Reboot selected Edge Node
Shutdown selected Edge Node
```

These actions must require local admin authentication and must be blocked while a race or update is active. The web application should not execute arbitrary shell commands directly. Use a restricted privileged helper or systemd-controlled command service that only allows known actions.

Development builds default to dry-run power actions. Real system commands must be explicitly enabled on deployed hardware:

```text
FITRACE_POWER_COMMANDS_ENABLED=1
FITRACE_ADMIN_TOKEN=<local-admin-token>
FITRACE_NODE_COMMAND_TOKEN=<hub-to-edge-command-token>
FITRACE_RACE_RESULTS_PATH=/var/lib/fitracestudio/race_results.jsonl
FITRACE_EDGE_MONITOR_PATH=/var/lib/fitracestudio/edge_monitor.jsonl
```

`FITRACE_ADMIN_TOKEN` protects Hub and Edge HTTP management APIs. `FITRACE_NODE_COMMAND_TOKEN` protects MQTT commands sent from the Hub to Edge Nodes, including system shutdown. Hub and Edge services in the same shipped system must share the same `FITRACE_NODE_COMMAND_TOKEN`. If `FITRACE_NODE_COMMAND_TOKEN` is not set, the current implementation falls back to `FITRACE_ADMIN_TOKEN`; production deployments should set both explicitly so browser-facing admin access and machine-to-machine command authorization can rotate independently.

`FITRACE_RACE_RESULTS_PATH` controls where completed race snapshots are stored.
If it is not set, the Hub writes `data/race_results.jsonl` relative to its
working directory.

`FITRACE_EDGE_MONITOR_PATH` controls where Edge Nodes write recent UART RX/TX
and MQTT publish monitor events for the local setup page. If it is not set, the
Edge service writes `data/edge_monitor.jsonl` relative to its working directory.

Recommended local API shape:

```text
GET  /api/system/power/status
POST /api/system/power/restart-service
POST /api/system/power/reboot
POST /api/system/power/shutdown
POST /api/nodes/{edge_node_id}/power/restart-service
POST /api/nodes/{edge_node_id}/power/reboot
POST /api/nodes/{edge_node_id}/power/shutdown
```

Every power action should write an audit log with timestamp, user, target, action, and result.

## 11. Fallback Subnet Scan

Subnet scanning can be used only as a fallback.

Example target:

```text
192.168.26.0/24
port 8001
GET /api/status
```

Do not use subnet scan as the primary discovery path because it can be slower, noisier, and affected by firewall or AP client isolation settings.

## 12. AP Requirements

The shipped AP must support:

```text
WPA2/WPA3 security
2.4 GHz support for RPi compatibility
Optional 5 GHz support
DHCP reservations or static leases
Client-to-client LAN communication
No client isolation for FitRaceStudio devices
Stable operation with all Edge Nodes connected
```

If AP client isolation is enabled, Hub-to-Edge web setup and discovery will fail. It must be disabled for the FitRaceStudio LAN.

## 13. Security Baseline

Recommended baseline:

```text
Strong AP password per shipment or customer
Technician PIN or local admin auth for Edge Web Setup writes
Read-only status endpoints may be visible on LAN
Write endpoints require authentication
Hub and Edge HTTP management APIs require FITRACE_ADMIN_TOKEN
Hub-to-Edge MQTT power commands require FITRACE_NODE_COMMAND_TOKEN
MQTT username/password
Optional per-node MQTT credentials
Topic ACLs when supported
No AP credentials shown in ordinary setup screens
Signed OTA update manifests and checksum verification
Local admin authentication before manual update installation
Local admin authentication before reboot or shutdown
```

## 14. Field Installation Checklist

1. Power on the shipped AP.
2. Confirm SSID `fitRace26` is broadcasting.
3. Power on Central Hub.
4. Confirm Hub is reachable at its reserved IP or hostname.
5. Confirm `FITRACE_ADMIN_TOKEN` and `FITRACE_NODE_COMMAND_TOKEN` are configured on Hub and Edge services.
6. Power on Edge Nodes.
7. Confirm each Edge Node joins `fitRace26`.
8. Open each Edge Node web setup page.
9. Configure equipment identity and FTMS target.
10. Confirm each configured FTMS device status is connected.
11. Confirm MQTT status is connected.
12. Confirm Hub dashboard shows each node online.
13. Run a short telemetry test before starting an event.

## 15. Open Implementation Questions

These items should be finalized before production implementation:

```text
Will the AP provide DHCP reservations, or will devices use static IPs?
Will node discovery use mDNS only as fallback, or also populate a Hub node list?
Will Edge Web Setup use PIN, password, or signed provisioning token?
Will MQTT credentials be shared per system or unique per node?
Will AP credentials be identical across shipments or unique per customer?
Will OTA release channels be stable-only, or stable plus beta for pilot customers?
Will reboot/shutdown controls target Hub only at first, or Hub and Edge Nodes together?
```
