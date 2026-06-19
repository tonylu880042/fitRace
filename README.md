# FitRaceStudio

**FitRaceStudio** is a local IoT aerobic race system designed for connected fitness studios.
It captures real-time telemetry from aerobic fitness equipment, aggregates the data locally, and drives live race visualization on an in-studio dashboard screen.

The system is designed for indoor fitness competitions, aerobic group challenges, studio events, and digital fitness experiences where robustness, low latency, local networking, and clear communication-channel separation are required.

---

## 1. Project Overview

FitRaceStudio connects multiple aerobic fitness machines through local Edge Nodes. Each Edge Node controls an antenna board over **UART**; the antenna board handles **BLE / FTMS** equipment links, while the Edge Node publishes normalized telemetry through **Wi-Fi / MQTT** to a **Central Hub** for real-time aggregation, race state management, and dashboard visualization.

Typical supported equipment includes:

* Treadmill
* Fan Bike
* Rowing Machine
* Ski Erg
* Other FTMS-compatible aerobic equipment

The system is designed to operate inside a local studio network without depending on external cloud services during live race operation.

Deployment network, hostname, mDNS, and Edge Node discovery rules are documented in [DEPLOYMENT.md](DEPLOYMENT.md). Normalized Edge-to-Hub telemetry is documented in [TELEMETRY_SPEC.md](TELEMETRY_SPEC.md). Hub-led OTA update design is documented in [OTA_UPDATE.md](OTA_UPDATE.md). AWS cloud requirements for update distribution are documented in [AWS_CLOUD_REQUIREMENTS.md](AWS_CLOUD_REQUIREMENTS.md). Cloud update validation is documented in [CLOUD_UPDATE_TEST_PLAN.md](CLOUD_UPDATE_TEST_PLAN.md).

---

## Development Verification

```text
.venv/bin/python -m pytest -q
node scripts/verify_dashboard_ux.mjs
```

`scripts/verify_dashboard_ux.mjs` starts a local test Hub, seeds a team race,
drives Dashboard state through READY, COUNTDOWN, RUNNING, Team Battle completion,
and RESULT, then writes screenshots under `output/screenshots/dashboard-ux/`.

---

## 2. Core Design Philosophy

FitRaceStudio follows these architectural principles:

1. **Local-first operation**
   Race telemetry, dashboard display, and device communication run inside the local studio network.

2. **Async-first software architecture**
   Python services use asynchronous event-driven design to support real-time data streaming and reconnection logic.

3. **Clear communication separation**

   * UART antenna board + BLE / FTMS: equipment-to-edge telemetry capture
   * Wi-Fi / MQTT: edge-to-hub data transport
   * WebSocket: hub-to-dashboard real-time visualization
   * Shipped AP `fitRace26`: preconfigured local network for Hub, Edge Nodes, setup phones, and dashboard devices
   * Edge Node Web Setup: per-node configuration through each Edge Node's local web service

4. **Edge data capture, hub data orchestration**
   Edge Nodes focus on equipment telemetry collection.
   The Central Hub focuses on aggregation, race state, visualization routing, and system coordination.

5. **Studio-friendly hardware expectation**
   Hardware should look like a compact proprietary fitness IoT gateway, not a hobbyist development board.

---

## 3. System Architecture

```text
Aerobic Equipment
    |
    | BLE / FTMS
    v
Antenna Board
    |
    | UART
    v
Equipment Data Capture Node
    |
    | Wi-Fi / MQTT
    v
Central Hub
    |
    | WebSocket / Local Dashboard API
    v
Race Dashboard Screen
```

### Main Components

```text
FitRaceStudio/
├── edge_node/
│   ├── UART antenna board orchestration
│   ├── local configuration receiver
│   ├── MQTT telemetry publisher
│   └── mock telemetry generator
│
├── hub_server/
│   ├── MQTT subscriber
│   ├── race state machine
│   ├── WebSocket dashboard stream
│   ├── local API endpoints
│   └── dashboard frontend
│
└── setup_app/
    └── mobile or web-based configuration interface for Edge Node local web setup
```

---

## 4. Communication Channels

FitRaceStudio uses different communication channels for different system responsibilities.

### 4.1 UART Antenna Board + BLE / FTMS

Used between aerobic equipment, the antenna board, and the Edge Node.

```text
Fitness Equipment → BLE / FTMS → Antenna Board → UART → Edge Node
```

Purpose:

* Capture speed
* Capture cadence
* Capture power
* Capture distance
* Capture elapsed time
* Capture heart rate if available
* Receive FTMS notifications from compatible equipment through the antenna board

BLE / FTMS should only be used for equipment-side telemetry capture. The Edge Node product path controls this through the antenna board UART protocol, not direct RPi USB Bluetooth dongles.
It should not be used for race dashboard streaming or multi-node coordination.

---

### 4.2 Wi-Fi / MQTT

Used between Edge Nodes and the Central Hub.

```text
Edge Node → Central Hub
```

Purpose:

* Publish real-time equipment telemetry
* Support multiple equipment nodes
* Provide lightweight local message routing
* Decouple equipment capture from dashboard logic

Recommended MQTT topic structure:

```text
gym/telemetry/{node_id}
fitrace/nodes/{edge_node_id}/status
gym/config/{node_id}
gym/race/state
```

Example telemetry topic:

```text
gym/telemetry/treadmill-01
gym/telemetry/fanbike-01
gym/telemetry/rower-01
gym/telemetry/skierg-01
```

Example telemetry payload:

```json
{
  "node_id": "treadmill-01",
  "equipment_id": "TREAD_01",
  "equipment_type": "treadmill",
  "instantaneous_speed_kph": 12.4,
  "cadence_rpm": 82,
  "power_watts": 245,
  "heart_rate_bpm": 156,
  "distance_m": 1240.5,
  "elapsed_time_ms": 360000,
  "timestamp_epoch_ms": 1760000000000
}
```

---

### 4.3 WebSocket

Used between the Central Hub backend and the dashboard screen.

```text
Central Hub → Race Dashboard Screen
```

Purpose:

* Push real-time telemetry
* Push race state updates
* Push leaderboard changes
* Push timing and ranking updates

Dashboard endpoint:

```text
/ws/dashboard
```

---

### 4.4 Shipped AP Network

FitRaceStudio is shipped with a professional access point that is configured before delivery.

All system devices join the same dedicated LAN:

```text
SSID: fitRace26
Central Hub → fitRace26 AP
Edge Nodes → fitRace26 AP
Setup phone/tablet → fitRace26 AP
Dashboard screen → fitRace26 AP
```

Recommended shipped AP behavior:

* The AP is part of the delivered hardware package.
* SSID `fitRace26` and credentials are configured before shipment.
* Central Hub and all Edge Nodes are provisioned to join `fitRace26` automatically.
* Edge Node Wi-Fi credentials are not configured in the field during normal installation.
* The technician connects a phone or tablet to `fitRace26` and opens each Edge Node's local web setup service.

Field setup focuses on:

* Node identity
* Equipment type
* FTMS target device binding
* Hub or MQTT address
* Station mapping and telemetry test

### 4.5 Edge Node Web Setup

Edge Node configuration is performed through a local web service hosted by each Edge Node.

The technician workflow is:

```text
1. Connect phone/tablet to AP SSID: fitRace26
2. Open the target Edge Node setup URL
3. Configure node_id, equipment_id, equipment_type, FTMS target, and Hub/MQTT address
4. Save configuration to the Edge Node local config.json
5. Restart or reload the Edge Node service
6. Verify FTMS and MQTT status from the setup page
```

Possible Edge Node setup URL patterns:

```text
http://fitrace-edge-01.local/
http://<edge_node_ip>/
```

The exact discovery mechanism can be fixed IP reservation, QR code labels, mDNS, or a Hub-side node list.

Recommended Edge Node setup API:

```text
GET  /api/config
POST /api/config
GET  /api/status
POST /api/restart
```

---

## 5. Hardware Architecture

### 5.1 Central Hub

The Central Hub is the main local orchestration node.

Recommended hardware type:

* Compact mini PC
* Industrial IoT gateway
* Raspberry Pi Compute Module-based product enclosure
* Fanless x86 gateway
* Small Linux appliance

The Central Hub should not be presented as a large server rack.
For customer expectation management, it should appear as a compact local control box suitable for a fitness studio.

Recommended responsibilities:

* Run MQTT broker or connect to local MQTT broker
* Run FastAPI backend
* Run WebSocket server
* Run race state machine
* Host dashboard frontend
* Connect to the shipped `fitRace26` AP or wired LAN
* Monitor Edge Node status on the local LAN
* Store race configuration
* Optionally cache local race results

Suggested hardware specifications:

| Item      | Recommendation                  |
| --------- | ------------------------------- |
| CPU       | ARM64 quad-core or x86 mini PC  |
| RAM       | 4 GB minimum, 8 GB preferred    |
| Storage   | 32 GB minimum, 128 GB preferred |
| Network   | Wi-Fi + Ethernet                |
| OS        | Linux                           |
| Enclosure | Compact proprietary enclosure   |
| Power     | Stable DC power adapter         |
| Optional  | UPS or power-loss protection    |

Recommended Central Hub software:

* Linux
* Python 3.11+
* FastAPI
* MQTT broker, such as Mosquitto
* MQTT client bridge
* WebSocket dashboard service
* Local configuration service
* Local update manager for Hub and Edge Node software updates
* Local admin power controls for service restart, reboot, and shutdown

---

### 5.2 Edge Node

Each Edge Node controls a two-channel antenna board over UART. The antenna board owns the BLE radio modules used to connect to nearby FTMS aerobic machines. The supported product limit is 5 FTMS devices per Edge Node.

Recommended hardware type:

* Compact equipment telemetry gateway
* Custom enclosure with antenna board, Wi-Fi, and UART wiring
* Linux-based edge device
* Industrial embedded controller

Each Edge Node should appear as a proprietary equipment data capture module rather than a development board.

Recommended responsibilities:

* Connect to up to 5 fitness machines through the UART-controlled antenna board
* Capture real-time equipment telemetry
* Convert FTMS packets into normalized telemetry JSON
* Publish telemetry to Central Hub through Wi-Fi / MQTT
* Maintain local configuration
* Reconnect each FTMS equipment independently after antenna board, BLE, or Wi-Fi interruption
* Optionally buffer short telemetry gaps

Suggested Edge Node hardware specifications:

| Item      | Recommendation                         |
| --------- | -------------------------------------- |
| CPU       | ARM Linux device                       |
| RAM       | 1 GB minimum, 2 GB preferred           |
| Antenna   | 2 UART channels, BLE modules on board  |
| Wi-Fi     | 2.4 GHz / 5 GHz supported              |
| OS        | Linux                                  |
| Power     | USB-C or DC input                      |
| Enclosure | Compact black or gray box              |
| Mounting  | Velcro, bracket, or machine-side mount |

---

### 5.3 Antenna Board Strategy

BLE is reserved for equipment telemetry capture through FTMS. Edge Node setup must not depend on BLE Peripheral mode or USB Bluetooth dongle control in the product path.

```text
UART channel 1: antenna board BLE module 1
UART channel 2: antenna board BLE module 2
edge web service: HTTP setup/configuration over fitRace26 LAN
```

However, in the customer-facing system concept, this should be abstracted as:

```text
Equipment Data Capture Node
```

Do not expose internal BLE adapter details in sales or customer-facing materials.

Engineering role separation:

| Interface        | Role    | Purpose                         |
| ---------------- | ------- | ------------------------------- |
| BLE adapter      | Central | FTMS equipment connection       |
| Wi-Fi / Ethernet | LAN     | MQTT telemetry and web setup    |

Production simplification:

* BLE setup is removed from the architecture.
* Edge setup is done through each node's local web service on `fitRace26`.
* FTMS capture should remain BLE central mode.
* A second BLE adapter is only needed if field testing proves FTMS capture stability requires it, not for setup.

---

## 6. Software Modules

## 6.1 `hub_server/`

The Central Hub backend service.

### Responsibilities

* Subscribe to MQTT telemetry topics
* Receive real-time equipment data
* Broadcast telemetry to dashboard clients through WebSocket
* Manage race lifecycle
* Provide local API endpoints
* Serve basic dashboard frontend
* Maintain race state

### Main modules

```text
hub_server/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── mqtt_client.py
│   ├── race_state.py
│   ├── websocket_manager.py
│   └── schemas.py
└── static/
    └── index.html
```

### Module Definitions

#### `main.py`

FastAPI application entry point.

Responsibilities:

* Initialize application lifecycle
* Start MQTT bridge
* Start WebSocket manager
* Expose REST API endpoints
* Expose dashboard WebSocket endpoint
* Serve frontend test page

Main endpoints:

```text
GET  /health
GET  /api/race/state
POST /api/race/configure
POST /api/race/countdown-start
POST /api/race/start
POST /api/race/stop
POST /api/race/reset
POST /api/race/start-sound
POST /api/leaderboard/display
WS   /ws/dashboard
```

---

#### `config.py`

Central Hub configuration.

Responsibilities:

* Load hub runtime settings
* Define MQTT broker connection
* Define WebSocket queue settings
* Define app host and port

Example settings:

```text
HUB_MQTT_HOST
HUB_MQTT_PORT
HUB_MQTT_CLIENT_ID
HUB_MQTT_TOPIC
HUB_APP_HOST
HUB_APP_PORT
```

---

#### `mqtt_client.py`

MQTT subscription bridge.

Responsibilities:

* Connect to MQTT broker
* Subscribe to `gym/telemetry/#`
* Parse incoming telemetry messages
* Dispatch telemetry to WebSocket manager
* Handle auto-reconnect

---

#### `race_state.py`

Race state machine.

Responsibilities:

* Configure race
* Start race
* Stop race
* Reset race
* Track race timing
* Provide race state snapshot

Suggested race states:

```text
IDLE
READY
RUNNING
STOPPED
```

Future extension:

```text
PAUSED
FINISHED
ERROR
RECOVERY
```

---

#### `websocket_manager.py`

Real-time WebSocket broadcast manager.

Responsibilities:

* Manage connected dashboard clients
* Queue outgoing telemetry messages
* Broadcast telemetry to all connected screens
* Remove disconnected clients
* Avoid blocking MQTT ingestion

---

#### `schemas.py`

Shared data schemas.

Responsibilities:

* Define race command schemas
* Define race state schemas
* Define telemetry envelope schemas
* Ensure structured typed payloads

---

#### `static/index.html`

Minimal dashboard test page.

Responsibilities:

* Connect to `/ws/dashboard`
* Print incoming JSON messages to browser console
* Verify WebSocket telemetry stream during development

---

## 6.2 `edge_node/`

The Edge Node service.

### Responsibilities

* Load local configuration
* Start local web setup service
* Start BLE central FTMS capture task
* Generate mock telemetry during development
* Publish telemetry to MQTT
* Maintain reconnect loops
* Keep BLE and MQTT logic separated
* Keep setup web service, BLE capture, and MQTT publishing separated

### Main modules

```text
edge_node/
├── app/
│   ├── main.py
│   ├── runner.py
│   ├── config.py
│   ├── mqtt_client.py
│   ├── ble_central.py
│   ├── web_config.py
│   ├── mock_ftms.py
│   └── types.py
└── config.json
```

---

### Module Definitions

#### `main.py`

Edge Node process entry point.

Responsibilities:

* Parse config path
* Load configuration
* Start Edge Node service
* Handle graceful shutdown
* Capture SIGINT and SIGTERM

---

#### `runner.py`

Edge Node orchestration module.

Responsibilities:

* Initialize MQTT publisher
* Initialize local web configuration server
* Initialize FTMS source
* Choose mock mode or real BLE mode
* Publish normalized telemetry
* Stop all background tasks gracefully

---

#### `config.py`

Local Edge Node configuration.

Responsibilities:

* Read `config.json`
* Validate node settings
* Define MQTT parameters
* Define BLE adapter settings
* Define local web setup parameters
* Define equipment identity
* Define mock mode

Example config fields:

```json
{
  "node_id": "fitrace-edge-01",
  "mqtt_host": "192.168.26.10",
  "mqtt_port": 1883,
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
    }
  ]
}
```

The top-level `node_id` is the physical Edge Node identity. Each `equipment_bindings[*].node_id` is a telemetry stream identity that Central Hub can rank and bind to a station independently.

---

#### `mqtt_client.py`

Async MQTT publisher.

Responsibilities:

* Connect to Central Hub MQTT broker
* Publish telemetry messages
* Reconnect automatically after network failure
* Wait for MQTT readiness before publishing

---

#### `ble_central.py`

FTMS BLE central client.

Responsibilities:

* Connect to FTMS-compatible equipment
* Subscribe to FTMS characteristics
* Parse FTMS telemetry packets
* Convert raw BLE data to normalized telemetry
* Reconnect after BLE disconnection

Current implementation:

* Placeholder for real BLE FTMS integration

Future implementation:

* Use BLE library such as `bleak`
* Support FTMS Indoor Bike Data
* Support FTMS Treadmill Data
* Support FTMS Rower Data
* Support FTMS Cross Trainer / SkiErg-like data where available

---

#### `web_config.py`

Edge Node local web setup service.

Responsibilities:

* Expose HTTP configuration UI and API on the `fitRace26` LAN
* Accept setup configuration from technician phone/tablet
* Support node identity, equipment binding, FTMS target, and Hub/MQTT address
* Validate and write local `config.json`
* Expose status for FTMS connection, MQTT connection, and last telemetry timestamp

Current implementation:

* Placeholder local setup service

Future direction:

* Support QR-code or mDNS based node discovery.
* Support authenticated technician access before writing configuration.

---

#### `mock_ftms.py`

Mock telemetry generator.

Responsibilities:

* Generate fake FTMS-like data every 500 ms
* Simulate speed, cadence, power, heart rate, distance, and elapsed time
* Allow full system testing without physical equipment

Useful for:

* Dashboard testing
* MQTT testing
* Race state testing
* Multi-node simulation

---

#### `types.py`

Typed telemetry models.

Responsibilities:

* Define normalized telemetry schema
* Ensure payload consistency between Edge Node and Central Hub

Example model:

```json
{
  "node_id": "rower-01",
  "equipment_id": "ROW_01",
  "equipment_type": "rowing_machine",
  "instantaneous_speed_kph": 8.6,
  "cadence_rpm": 28,
  "power_watts": 210,
  "heart_rate_bpm": 148,
  "distance_m": 950.2,
  "elapsed_time_ms": 240000,
  "timestamp_epoch_ms": 1760000000000
}
```

---

## 7. Setup App Definition

The Setup App is used during system installation and maintenance.

It may be implemented as:

* Mobile app
* Local web app
* PWA
* Technician tablet interface

### Setup App Responsibilities

* Connect phone/tablet to the shipped `fitRace26` AP
* Open each Edge Node's local web setup service
* Configure Edge Node identity
* Bind equipment to nodes
* Assign station names
* Test telemetry signal
* Confirm MQTT connectivity
* Save local configuration
* Trigger device restart or rejoin

### Suggested Setup App Screens

#### 1. Wi-Fi Settings

Fields:

```text
AP SSID: fitRace26
Connection Status
Signal Strength
Hub Reachability
```

#### 2. Node Discovery

Fields:

```text
Node ID
MAC Address
IP Address
Firmware Version
Connection Status
Last Seen
```

#### 3. Equipment Binding

Fields:

```text
Station Name
Equipment Type
FTMS Device Name
FTMS Device Address
Binding Status
```

#### 4. System Test

Fields:

```text
BLE Connected
MQTT Connected
Telemetry Received
Dashboard Online
Race Control Online
```

---

## 8. Shipped AP and Edge Web Setup Design

This section documents the shipped AP architecture used for field installation and maintenance.

### Shipped Professional AP

The system ships with a professional AP configured before delivery.

```text
SSID: fitRace26
Purpose: Dedicated local LAN for FitRaceStudio Hub, Edge Nodes, setup phones/tablets, and dashboard devices
Provisioning: Configured before shipment
Field Wi-Fi setup: Not part of normal installation
```

---

### Network Responsibilities

1. **Professional AP**
   Provides the dedicated `fitRace26` LAN used by the Central Hub, Edge Nodes, setup devices, and dashboard screens.

2. **Central Hub**
   Runs MQTT integration, race state, dashboard, admin APIs, and node monitoring.

3. **Edge Nodes**
   Publish telemetry and expose local web setup services on the shipped LAN.

4. **Field support**
   Troubleshoots AP availability, DHCP reservations, device provisioning, and service health on the LAN.

### Setup Flow

```text
1. Professional AP boots with SSID fitRace26
2. Central Hub joins fitRace26
3. Edge Nodes join fitRace26
4. Technician connects phone/tablet to fitRace26
5. Technician opens the target Edge Node local web setup page
6. Technician configures node identity, equipment type, antenna channel / FTMS target, and Hub/MQTT address
7. Edge Node saves config.json and restarts or reloads services
8. Edge Node commands the antenna board over UART to connect to FTMS equipment
9. Edge Node publishes telemetry to Central Hub through Wi-Fi / MQTT
10. Central Hub shows node status in the dashboard and race control desk
```

---

### Normal Operation Mode

The system operates on the shipped AP LAN.

```text
Edge Nodes → fitRace26 AP → Central Hub → Dashboard Screen / Race Control Desk
```

Recommended options:

| Mode             | Behavior                                                   |
| ---------------- | ---------------------------------------------------------- |
| Setup Mode       | Phone/tablet connects to `fitRace26`; Edge web setup opens |
| Normal Mode      | Hub and Edge Nodes communicate through `fitRace26` LAN     |
| Maintenance Mode | Technician uses Edge web setup and Hub admin UI on LAN     |
| Recovery Mode    | Use AP/admin tooling or wired service access               |

---

### Recovery Mode

If Central Hub or Edge Nodes cannot join `fitRace26`, recovery should focus on AP availability, device provisioning, and wired/local service access. Devices should not automatically create their own setup SSIDs unless a future hardware support policy explicitly adds that mode.

Recovery behavior:

```text
1. Verify fitRace26 AP is powered and broadcasting
2. Verify device Wi-Fi provisioning for SSID fitRace26
3. Use wired access, local console, or AP admin panel if the node is unreachable
4. Reapply device network provisioning if needed
5. Restart network services
```

---

## 9. Device Identity and Binding

Every Edge Node should have a unique identity.

Example:

```text
node_id: treadmill-01
equipment_id: TREAD_01
equipment_type: treadmill
station_name: Treadmill Station 01
```

Recommended naming convention:

```text
{equipment_type}-{station_number}
```

Examples:

```text
treadmill-01
fanbike-01
rower-01
skierg-01
```

### Binding Relationship

```text
Central Hub
  └── Edge Node
        └── FTMS Equipment
```

Example binding table:

| Station              | Node ID      | Equipment Type | FTMS Device   |
| -------------------- | ------------ | -------------- | ------------- |
| Treadmill Station 01 | treadmill-01 | treadmill      | TREAD_01_8F3A |
| Fan Bike Station 01  | fanbike-01   | fan_bike       | BIKE_01_4A21  |
| Rowing Station 01    | rower-01     | rowing_machine | ROW_01_91BC   |
| Ski Erg Station 01   | skierg-01    | ski_erg        | SKI_01_A8D2   |

---

## 10. Race State Machine

The Central Hub owns the race state.

### Basic States

```text
IDLE
READY
RUNNING
STOPPED
```

### State Definitions

| State   | Description                                          |
| ------- | ---------------------------------------------------- |
| IDLE    | System is available but no race is configured        |
| READY   | Race parameters are configured and waiting for start |
| RUNNING | Race is active and telemetry is being scored         |
| STOPPED | Race has been manually stopped or completed          |

### Race Commands

```text
Configure Race
Countdown Start Race
Stop Race
Reset Race
```

Game Admin should use `POST /api/race/countdown-start` for live events. The
endpoint broadcasts a Dashboard countdown event first, then starts the race when
the countdown reaches Go. `POST /api/race/start` remains available for direct
API workflows and tests that intentionally bypass the venue countdown.

### Supported Race Modes

Supported race formats:

* Time trial
* Distance target
* Team challenge
* Calories target
* Power challenge

Competition mode can be `individual` or `team`. Team races support:

* `Average Progress`: ranks teams by normalized member average.
* `Team Total`: ranks teams by accumulated team progress.
* `Aggregate Progress`: a team can finish when the team score reaches the target.
* `All Members Finish`: every teammate must complete the distance or calorie
  target before the team is considered finished.

---

## 11. Telemetry Normalization

Each Edge Node should normalize equipment-specific FTMS data into a consistent telemetry schema before publishing.

### Standard Telemetry Fields

```text
node_id
equipment_id
equipment_type
instantaneous_speed_kph
cadence_rpm
power_watts
heart_rate_bpm
distance_m
elapsed_time_ms
timestamp_epoch_ms
```

### Optional Future Fields

```text
calories_kcal
resistance_level
incline_percent
stroke_rate_spm
split_time_500m_sec
watts_per_kg
work_joules
race_lane
team_id
athlete_id
```

---

## 12. Dashboard System

The dashboard is displayed on a large studio screen.

### Dashboard Responsibilities

* Show live leaderboard
* Show race timer
* Show equipment progress
* Show station status
* Show athlete or team ranking
* Show key metrics:

  * Speed
  * Power
  * Distance
  * Pace
  * Heart rate
  * Calories
* Display warnings:

  * Node offline
  * Equipment disconnected
  * No telemetry
  * Race paused
  * Network issue

The dashboard is a display-only surface for the venue screen. It must not contain
operator controls, settings, toggles, sound enable buttons, race start/stop/reset
actions, or leaderboard mode selectors. Runtime behavior changes for the
dashboard must be driven by Central Hub state or WebSocket events configured from
`/gameAdmin` or `/systemAdmin`.

The dashboard should make the live event state visible without operator input:
IDLE/READY setup state, COUNTDOWN before Go, RUNNING with live timing, and final
result state after completion. Team Battle views should also surface team
completion status, including all-members completion progress.

Dashboard presentation modes are selected from Game Admin:

* `Classic`: standard rank list for the clearest result view.
* `Race Track`: lane-style progress visualization for distance and calorie races.
* `Team Battle`: team-versus-team cards with member completion visibility.
* `Sprint Board`: compact high-energy board for short challenges.

Race start sound is also controlled from Game Admin. The default is sound on.
The Dashboard receives a `race_countdown` WebSocket event, displays the countdown,
plays the 3, 2, 1, Go audio when enabled, and starts showing race time after Go.

### Dashboard Data Source

```text
Central Hub WebSocket
```

Endpoint:

```text
/ws/dashboard
```

---

## 13. Deployment Architecture

### Development Mode

```text
Mock FTMS Generator → MQTT → Central Hub → WebSocket → Browser Dashboard
```

Used when physical machines are not available.

### Studio Test Mode

```text
Up to 5 Real Equipment → BLE / FTMS → Antenna Board → UART → Edge Node → Wi-Fi / MQTT → Central Hub → Dashboard
```

Used during field testing.

### Production Mode

```text
Multiple Edge Nodes → fitRace26 AP → Central Hub → Display + Race Control Desk
```

Used in real gym deployment.

---

## 14. Recommended Services

### Central Hub Services

```text
fitracestudio-hub.service
fitracestudio-hub-updater.service
fitracestudio-mqtt.service
```

### Edge Node Services

```text
fitracestudio-edge.service
fitracestudio-ble.service
fitracestudio-mqtt-publisher.service
fitracestudio-edge-web-config.service
```

---

## 15. Reliability Requirements

### Edge Node

The Edge Node should handle:

* BLE equipment disconnection
* FTMS notification failure
* Wi-Fi disconnection
* MQTT broker unavailable
* Local config corruption
* Power restart
* Equipment replacement

Required behavior:

```text
Auto reconnect BLE
Auto reconnect MQTT
Keep last valid config
Publish node status heartbeat
Retry with exponential backoff
Avoid crashing on malformed packets
```

---

### Central Hub

The Central Hub should handle:

* MQTT client reconnect
* Edge Node offline detection
* WebSocket client disconnect
* Dashboard browser refresh
* Race state recovery
* Shipped AP network outage
* Local storage persistence

Required behavior:

```text
Keep race state snapshot
Track node last-seen timestamp
Broadcast node status
Restart services automatically
Expose health endpoint
Support graceful shutdown
```

---

## 16. Security Considerations

### Local Network Security

Recommended baseline:

* WPA2/WPA3 Wi-Fi
* Strong `fitRace26` AP password
* Setup mode timeout
* Local admin PIN or token
* No open unauthenticated configuration endpoint in production
* No open unauthenticated reboot or shutdown endpoint in production
* Keep power actions in dry-run unless `FITRACE_POWER_COMMANDS_ENABLED=1` is set on deployed hardware

### MQTT Security

Recommended baseline:

* MQTT username/password
* Per-node credentials if possible
* Topic-level ACL
* Optional TLS if hardware resources allow

### Setup App Security

Recommended baseline:

* Require technician login or local setup PIN
* Do not expose AP credentials in ordinary setup screens
* Validate device identity
* Prevent unauthorized node rebinding
* Store credentials securely

---

## 17. Suggested Project Roadmap

### Phase 1: Local Simulation

* Mock FTMS generator
* MQTT telemetry publishing
* FastAPI Central Hub
* WebSocket dashboard stream
* Basic race state machine
* Browser console dashboard test

### Phase 2: Studio Prototype

* Real BLE / FTMS integration
* Multi-node testing
* Shipped AP `fitRace26` network integration
* Edge Node local web setup flow
* Setup App prototype
* Live dashboard UI
* Node heartbeat and status monitoring

### Phase 3: Field Pilot

* Equipment binding workflow
* Race event control panel
* Leaderboard scoring logic
* Local result storage
* Export race results
* Installer-friendly setup flow

### Phase 4: Productization

* Custom hardware enclosure
* OTA firmware/software update
* Admin dashboard
* Cloud sync option
* Multi-site management
* Commercial deployment package

---

## 18. Naming and Positioning

### Product Name

```text
FitRaceStudio
```

### Suggested Tagline

```text
Real-Time Aerobic Equipment Telemetry & Race Visualization
```

### Short Description

FitRaceStudio is a local IoT race system for connected fitness studios.
It captures real-time data from aerobic equipment, aggregates the data through a local hub, and transforms group workouts into live race experiences.

### Chinese Description

FitRaceStudio 是一套面向健身房與運動場館的本地端 IoT 有氧競賽系統。
系統可即時擷取跑步機、風扇車、划船器、滑雪機等有氧設備數據，透過中央 Hub 進行資料彙整與競賽邏輯處理，並將結果呈現在現場大螢幕，形成即時排名、進度條、功率、距離、心率等互動式競賽視覺化體驗。

---

## 19. Glossary

| Term              | Definition                                       |
| ----------------- | ------------------------------------------------ |
| Edge Node         | Equipment-side data capture device               |
| Central Hub       | Local aggregation and orchestration device       |
| BLE               | Bluetooth Low Energy                             |
| FTMS              | Fitness Machine Service                          |
| MQTT              | Lightweight publish-subscribe messaging protocol |
| Shipped AP        | Dedicated professional access point shipped with the system; SSID `fitRace26` |
| Edge Web Setup    | Local Edge Node web service used for device configuration |
| Dashboard         | Large-screen live race visualization interface   |
| Race Control Node | Control desk or kiosk for timing and ranking     |
| Telemetry         | Real-time equipment data stream                  |

---

## 20. Summary

FitRaceStudio is designed as a local-first connected fitness race platform.

Its core value is not only collecting equipment data, but converting ordinary aerobic equipment into a live, measurable, and engaging race experience.

The foundational system includes:

```text
BLE / FTMS equipment capture
Wi-Fi / MQTT telemetry transport
Central Hub local aggregation
Race state management
WebSocket dashboard streaming
Shipped AP fitRace26 network
Edge Node Web Setup configuration
Live race visualization
```

This architecture provides a practical foundation for connected fitness studios, indoor aerobic competitions, group challenges, and future digital fitness service models.
