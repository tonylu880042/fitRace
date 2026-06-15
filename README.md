# FitRaceStudio

**FitRaceStudio** is a local IoT aerobic race system designed for connected fitness studios.
It captures real-time telemetry from aerobic fitness equipment, aggregates the data locally, and drives live race visualization on an in-studio dashboard screen.

The system is designed for indoor fitness competitions, aerobic group challenges, studio events, and digital fitness experiences where robustness, low latency, local networking, and clear communication-channel separation are required.

---

## 1. Project Overview

FitRaceStudio connects multiple aerobic fitness machines through local Edge Nodes. Each Edge Node captures equipment telemetry via **BLE / FTMS**, publishes the data through **Wi-Fi / MQTT**, and sends it to a **Central Hub** for real-time aggregation, race state management, and dashboard visualization.

Typical supported equipment includes:

* Treadmill
* Fan Bike
* Rowing Machine
* Ski Erg
* Other FTMS-compatible aerobic equipment

The system is designed to operate inside a local studio network without depending on external cloud services during live race operation.

---

## 2. Core Design Philosophy

FitRaceStudio follows these architectural principles:

1. **Local-first operation**
   Race telemetry, dashboard display, and device communication run inside the local studio network.

2. **Async-first software architecture**
   Python services use asynchronous event-driven design to support real-time data streaming and reconnection logic.

3. **Clear communication separation**

   * BLE / FTMS: equipment-to-edge telemetry capture
   * Wi-Fi / MQTT: edge-to-hub data transport
   * WebSocket: hub-to-dashboard real-time visualization
   * Soft AP: initial setup and local network bootstrapping

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
│   ├── BLE / FTMS equipment capture
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
    └── mobile or web-based configuration interface
```

---

## 4. Communication Channels

FitRaceStudio uses different communication channels for different system responsibilities.

### 4.1 BLE / FTMS

Used between aerobic equipment and the Edge Node.

```text
Fitness Equipment → Edge Node
```

Purpose:

* Capture speed
* Capture cadence
* Capture power
* Capture distance
* Capture elapsed time
* Capture heart rate if available
* Receive FTMS notifications from compatible equipment

BLE / FTMS should only be used for equipment-side telemetry capture.
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
gym/status/{node_id}
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

### 4.4 Soft AP

Soft AP is used for device setup and local network bootstrapping.

The **Central Hub** should act as the primary Soft AP node during initial deployment.

```text
Setup App → Central Hub Soft AP → System Configuration
```

Recommended Soft AP behavior:

* Central Hub starts a local Wi-Fi access point during setup mode.
* Setup App connects to the Central Hub AP.
* User configures:

  * Studio Wi-Fi SSID
  * Studio Wi-Fi password
  * Race setup profile
  * Equipment list
  * Edge Node binding
  * Node naming
* Central Hub distributes configuration to Edge Nodes.
* After setup, devices switch to the configured studio Wi-Fi network.

Example Soft AP SSID:

```text
FitRaceStudio_Setup
```

Example default local setup URL:

```text
http://192.168.50.1/setup
```

Recommended Soft AP IP:

```text
192.168.50.1
```

Recommended DHCP range:

```text
192.168.50.100 - 192.168.50.200
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
* Manage setup mode
* Provide Soft AP during initial setup
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
* Soft AP service
* Local configuration service

---

### 5.2 Edge Node

Each aerobic machine should have one nearby Edge Node.

Recommended hardware type:

* Compact BLE-enabled IoT gateway
* Custom enclosure with BLE and Wi-Fi
* Linux-based edge device
* Industrial embedded controller

Each Edge Node should appear as a proprietary equipment data capture module rather than a development board.

Recommended responsibilities:

* Connect to fitness equipment through BLE / FTMS
* Capture real-time equipment telemetry
* Convert FTMS packets into normalized telemetry JSON
* Publish telemetry to Central Hub through Wi-Fi / MQTT
* Maintain local configuration
* Reconnect automatically after BLE or Wi-Fi interruption
* Optionally buffer short telemetry gaps

Suggested Edge Node hardware specifications:

| Item      | Recommendation                         |
| --------- | -------------------------------------- |
| CPU       | ARM Linux device                       |
| RAM       | 1 GB minimum, 2 GB preferred           |
| BLE       | BLE 5.0 preferred                      |
| Wi-Fi     | 2.4 GHz / 5 GHz supported              |
| OS        | Linux                                  |
| Power     | USB-C or DC input                      |
| Enclosure | Compact black or gray box              |
| Mounting  | Velcro, bracket, or machine-side mount |

---

### 5.3 BLE Adapter Strategy

The initial engineering design may use two BLE roles:

```text
hci0: BLE peripheral role for setup/configuration
hci1: BLE central role for FTMS equipment capture
```

However, in the customer-facing system concept, this should be abstracted as:

```text
Equipment Data Capture Node
```

Do not expose internal BLE adapter details in sales or customer-facing materials.

Engineering role separation:

| Adapter | Role       | Purpose                       |
| ------- | ---------- | ----------------------------- |
| hci0    | Peripheral | Setup / configuration channel |
| hci1    | Central    | FTMS equipment connection     |

Production simplification:

* BLE setup can eventually move to Soft AP or Wi-Fi setup.
* FTMS capture should remain BLE central mode.
* If hardware supports stable multi-role BLE, the extra dongle may be removed.
* If stability is more important, dual-adapter design is recommended.

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
POST /api/race/start
POST /api/race/stop
POST /api/race/reset
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
* Start BLE peripheral setup placeholder
* Start BLE central FTMS capture task
* Generate mock telemetry during development
* Publish telemetry to MQTT
* Maintain reconnect loops
* Keep BLE and MQTT logic separated

### Main modules

```text
edge_node/
├── app/
│   ├── main.py
│   ├── runner.py
│   ├── config.py
│   ├── mqtt_client.py
│   ├── ble_central.py
│   ├── ble_peripheral.py
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
* Initialize BLE peripheral configuration server
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
* Define equipment identity
* Define mock mode

Example config fields:

```json
{
  "node_id": "treadmill-01",
  "equipment_id": "TREAD_01",
  "equipment_type": "treadmill",
  "mock_mode": true
}
```

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

#### `ble_peripheral.py`

BLE peripheral setup placeholder.

Responsibilities:

* Expose local setup interface through BLE peripheral mode
* Accept setup configuration from mobile app
* Support Wi-Fi credentials and equipment binding

Current implementation:

* Placeholder async background task

Future direction:

* Setup flow may be migrated primarily to Central Hub Soft AP
* Edge BLE peripheral setup may remain as fallback or technician mode

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

* Connect to Central Hub Soft AP
* Configure studio Wi-Fi
* Register Edge Nodes
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
SSID
Password
Connection Status
Signal Strength
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

## 8. Soft AP Design

The Central Hub should act as the setup authority.

### Why Soft AP Should Be Defined on the Central Hub

Soft AP should be hosted by the Central Hub rather than every Edge Node because:

1. **Simpler user experience**
   User connects to one setup Wi-Fi only.

2. **Centralized configuration**
   Hub manages all equipment nodes and race configuration.

3. **Cleaner installation flow**
   Installer does not need to connect to each Edge Node separately.

4. **Better customer expectation**
   The system feels like a single studio solution, not a collection of independent devices.

5. **Reduced support complexity**
   Network setup, node binding, and race setup are controlled from one place.

---

### Recommended Soft AP Mode

```text
SSID: FitRaceStudio_Setup
Gateway IP: 192.168.50.1
Setup URL: http://192.168.50.1/setup
DHCP Range: 192.168.50.100 - 192.168.50.200
```

### Setup Flow

```text
1. Central Hub boots in Setup Mode
2. Central Hub starts Soft AP: FitRaceStudio_Setup
3. Technician connects phone/tablet to Soft AP
4. Technician opens setup page or app
5. Technician enters studio Wi-Fi credentials
6. Technician scans or registers Edge Nodes
7. Technician binds equipment to each Edge Node
8. Central Hub saves configuration
9. Central Hub distributes configuration to Edge Nodes
10. System switches to normal operating mode
11. Edge Nodes publish telemetry to Central Hub through Wi-Fi / MQTT
12. Race dashboard becomes available
```

---

### Normal Operation Mode

After setup, the system should use the studio LAN.

```text
Edge Nodes → Studio Wi-Fi → Central Hub → Dashboard Screen
```

The Central Hub may keep a hidden or disabled Soft AP depending on product policy.

Recommended options:

| Mode             | Behavior                                  |
| ---------------- | ----------------------------------------- |
| Setup Mode       | Soft AP enabled                           |
| Normal Mode      | Soft AP disabled                          |
| Maintenance Mode | Soft AP enabled temporarily               |
| Recovery Mode    | Soft AP enabled if Wi-Fi connection fails |

---

### Recovery Mode

If the Central Hub cannot connect to the configured studio Wi-Fi, it should automatically enter recovery setup mode.

Recovery behavior:

```text
1. Try configured studio Wi-Fi
2. If failed after timeout, start FitRaceStudio_Setup Soft AP
3. Allow technician to reconnect and update Wi-Fi settings
4. Save new configuration
5. Restart network services
```

Recommended timeout:

```text
60 - 120 seconds
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
Start Race
Stop Race
Reset Race
```

### Future Race Modes

Possible race formats:

* Time trial
* Distance target
* Relay race
* Team challenge
* Station rotation
* Multi-equipment circuit
* Calories target
* Power challenge

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
Real Equipment → BLE / FTMS → Edge Node → Wi-Fi / MQTT → Central Hub → Dashboard
```

Used during field testing.

### Production Mode

```text
Multiple Edge Nodes → Studio Wi-Fi → Central Hub → Display + Race Control Desk
```

Used in real gym deployment.

---

## 14. Recommended Services

### Central Hub Services

```text
fitracestudio-hub-api.service
fitracestudio-mqtt.service
fitracestudio-softap.service
fitracestudio-dashboard.service
```

### Edge Node Services

```text
fitracestudio-edge.service
fitracestudio-ble.service
fitracestudio-mqtt-publisher.service
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
* Soft AP recovery mode
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
* Strong Soft AP password
* Setup mode timeout
* Disable Soft AP during normal operation
* Local admin PIN or token
* No open unauthenticated configuration endpoint in production

### MQTT Security

Recommended baseline:

* MQTT username/password
* Per-node credentials if possible
* Topic-level ACL
* Optional TLS if hardware resources allow

### Setup App Security

Recommended baseline:

* Require technician login or local setup PIN
* Mask Wi-Fi password
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
* Central Hub Soft AP setup flow
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
| Soft AP           | Software Access Point used for setup             |
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
Soft AP setup flow
Setup App configuration
Live race visualization
```

This architecture provides a practical foundation for connected fitness studios, indoor aerobic competitions, group challenges, and future digital fitness service models.
