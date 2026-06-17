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
GET  /api/ble/scan
POST /api/restart
```

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
  "antenna_protocol_version": "pending-hardware",
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

### FTMS Scan Command (Paused USB Dongle Prototype)

This section records the paused USB Bluetooth dongle prototype. It is not the current product deployment path while the two-channel UART antenna board is pending.

The product setup UI should ultimately command the antenna board to scan through UART. Until the hardware protocol is available, the UI may keep mock/configured states and should not require this USB dongle scan path for deployment.

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
```

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
5. Power on Edge Nodes.
6. Confirm each Edge Node joins `fitRace26`.
7. Open each Edge Node web setup page.
8. Configure equipment identity and FTMS target.
9. Confirm each configured FTMS device status is connected.
10. Confirm MQTT status is connected.
11. Confirm Hub dashboard shows each node online.
12. Run a short telemetry test before starting an event.

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
