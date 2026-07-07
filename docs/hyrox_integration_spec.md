# Spec: Hyrox Extension & Sensor Integration (UHF RFID & Wall Ball)

This specification outlines the architecture, hardware topology, data transmission schemas, software implementation details, and verification plan for integrating the **Hyrox Competition Mode** into the FitRaceStudio system.

It is designed as a blueprint for coding agents to implement the next phase of development.

---

## 1. Scope & Isolation Philosophy

### 1.1 Goal
Provide a separate, premium Hyrox competition track in FitRaceStudio. The system will handle:
*   Multi-stage functional workouts alternating with running laps.
*   **UHF RFID Timing Mats** placed at the start and end of lanes to detect athlete-worn ankle tags, verifying "completed distance/laps" (走到位) for Sled Push, Sled Pull, Farmer's Carry, and Sandbag Lunges.
*   **Target-mounted Wall Ball sensors** to count and validate wall ball repetitions.

### 1.2 Isolation & Feature Flag
To prevent bloating the existing cardio race code (`RaceManager`), the Hyrox functionality must be strictly isolated.
1.  **Feature Flag**: The mode is controlled by an environment variable: `FITRACE_ENABLE_HYROX=1`. If this variable is `0` or absent, all Hyrox routes must return `404 Not Found`.
2.  **Separate Entry Points**:
    *   Dashboard: `/hyrox/dashboard` (Displays Hyrox leaderboards, grid lanes, and individual stage metrics).
    *   Game Admin: `/hyrox/admin` (Used by coaches to setup heats, assign athlete tags, and control starts).
    *   Athlete Signup: `/hyrox/signup` (Self-registration to bind name, team, avatar, and assigned RFID Tag ID).
    *   REST APIs: Prefix all Hyrox endpoints with `/api/hyrox/`.
3.  **Use Case Separation**:
    *   Do **NOT** add Hyrox code to `RaceManager`.
    *   Create a new [HyroxManager](file:///Users/tunghunglu/projects/fitRace/hub_server/usecases/hyrox_manager.py) class that runs a parallel state machine for functional stages.

### 1.3 Visual Theme Consistency
*   **Design Alignment**: The Hyrox frontend pages must 100% inherit the aesthetic rules from [DESIGN.md](file:///Users/tunghunglu/projects/fitRace/DESIGN.md), preserving the Volt Neon Yellow `#e2ff3b` highlights, Deep Coal Black `#09090b` viewport backgrounds, and Charcoal Asphalt Gray `#18181b` grid cards to ensure visual brand alignment across both system tracks.

### 1.4 Registration Division & Record Settings
1.  **Competition Formats**: During registration/setup, the system must allow configuring the race division to align with official international Hyrox formats:
    *   `individual` (個人組): A single athlete completes all run laps and functional workouts.
    *   `doubles` (雙人組): A pair of athletes completes the challenge together, splitting workout reps/laps.
    *   `relay` (四人接力組): A team of 4 athletes completes the challenge, split by transitions.
2.  **Record/Session Types**: To assist gym operators in data retention and progress analytics, the coach must specify whether the session is:
    *   `training` (訓練模式): Focuses on logging personal performance statistics, cumulative progression, and analytics.
    *   `competition` (競賽模式): Standard high-visibility leaderboard rankings for active event heats.


---

## 2. Hardware Topology & Ingestion Flow

The hardware deployment consists of athlete-worn passive tags, lane-bound floor timing mats, target sensors, Edge Nodes (Raspberry Pi), and the Central Hub.

```text
[ Athlete / Tag ]            [ Lane Antennas / Mats ]        [ Edge Node (RPi) ]         [ Central Hub ]
+-------------------+        +--------------------+          +--------------------+      +--------------------+
| Ankle Strap Tag   | -----> | Start line Mat     | --UART-> | edge_node          | -MQ-> | hub_server         |
| (Passive UHF RFID)|        | Finish line Mat    |          | (rfid_parser.py)   | -TT-> | (mqtt_subscriber)  |
+-------------------+        +--------------------+          +--------------------+      +--------------------+
                                                                                            ^
+-------------------+                                                                       |
| Wall Ball Target  | ------------------------------------Wi-Fi / MQTT----------------------+
| (Vibration + ToF) |
+-------------------+
```

### 2.1 UHF RFID Floor Mat Setup
*   **Tags**: Passive UHF EPC Gen2 ankle strap tags (made of TPU/soft foam to prevent signal absorption by the athlete's body).
*   **Floor Mats**: Rubberized timing mats containing circular-polarized patch antennas laid flat on the floor at the **Start Line** and **Finish Line** of each lane.
*   **Reader**: A multi-channel reader module (e.g. Impinj E710-based) connected to the Edge Node via UART.
*   **Lane Configuration**: Each antenna is bound to a specific lane and position. For example, reader channel 1 goes to Lane 1 Start, channel 2 to Lane 1 Finish.

### 2.2 Wall Ball Target Setup
*   **Sensors**: Target-mounted rather than ball-mounted to prevent structural damage. A piezoelectric vibration sensor detects impact on the board, combined with an upward-pointing Time-of-Flight (ToF) laser distance sensor to verify the ball reached the required height (9ft/10ft).
*   **Processor**: ESP32 controller on the target board, transmitting successful repetitions directly to the Hub over Wi-Fi via MQTT.

---

## 3. Data Transmission Schemas (MQTT)

### 3.1 RFID Tag Crossing
Published by the Edge Node when a tag is detected crossing a floor mat:
*   **Topic**: `gym/telemetry/rfid/{station_id}`
*   **Payload**:
```json
{
  "node_id": "rfid-reader-01",
  "edge_node_id": "edge-node-rfid-01",
  "equipment_type": "rfid_timing_mat",
  "location": "start_line",
  "antenna_id": "L1_START",
  "tag_id": "E28011052000789A",
  "rssi": -48.5,
  "timestamp_epoch_ms": 1780000000000
}
```
*Note on Ingestion Filtering (Cross-talk Prevention)*:
Adjacent lanes can occasionally detect tags from neighboring lanes. The Edge Node and Hub must filter out telemetry rows where `rssi < -60` (or a calibrated threshold) to isolate lanes.

### 3.2 Wall Ball Counter
Published by the ESP32 wall ball target sensor on successful hits:
*   **Topic**: `gym/telemetry/wallball/{station_id}`
*   **Payload**:
```json
{
  "node_id": "wallball-target-01",
  "equipment_type": "wallball_sensor",
  "station_number": 1,
  "event": "valid_rep",
  "timestamp_epoch_ms": 1780000000000
}
```

---

## 4. Software Implementation Details

### 4.1 Domain Models
In [hub_server/domain/models.py](file:///Users/tunghunglu/projects/fitRace/hub_server/domain/models.py), define the Hyrox entities:

```python
from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Literal

class HyroxStage(str, Enum):
    RUN_1 = "run_1"
    SLED_PUSH = "sled_push"
    RUN_2 = "run_2"
    SLED_PULL = "sled_pull"
    RUN_3 = "run_3"
    BURPEE_BROAD = "burpee_broad"
    RUN_4 = "run_4"
    ROW = "row"
    RUN_5 = "run_5"
    FARMERS_CARRY = "farmers_carry"
    RUN_6 = "run_6"
    SANDBAG_LUNGES = "sandbag_lunges"
    RUN_7 = "run_7"
    WALL_BALLS = "wall_balls"
    FINISHED = "finished"

class AthleteTagBinding(BaseModel):
    athlete_name: str
    team_name: Optional[str] = None
    rfid_tag_id: str
    station_number: Optional[int] = None


class HyroxConfig(BaseModel):
    competition_mode: Literal["individual", "doubles", "relay"] = Field(
        "individual", description="Hyrox race format"
    )
    session_type: Literal["training", "competition"] = Field(
        "training", description="Type of session (training or official competition)"
    )

class AthleteHyroxState(BaseModel):
    athlete_name: str
    station_number: int
    current_stage: HyroxStage = HyroxStage.RUN_1
    stage_laps: Dict[str, int] = Field(default_factory=dict) # stage_name -> completed_laps/reps
    stage_start_times: Dict[str, int] = Field(default_factory=dict) # stage_name -> epoch_ms
    stage_end_times: Dict[str, int] = Field(default_factory=dict) # stage_name -> epoch_ms
    total_elapsed_time_ms: int = 0
```


### 4.2 Use Case: HyroxManager
Create [hub_server/usecases/hyrox_manager.py](file:///Users/tunghunglu/projects/fitRace/hub_server/usecases/hyrox_manager.py) to manage the state machine:

```python
import logging
from typing import Dict, Optional
from hub_server.domain.models import AthleteHyroxState, HyroxStage

logger = logging.getLogger("hub_server.hyrox_manager")

class HyroxManager:
    def __init__(self):
        self._athletes: Dict[str, AthleteHyroxState] = {} # rfid_tag_id -> AthleteHyroxState
        self._station_to_tag: Dict[int, str] = {} # station_number -> rfid_tag_id
        self._is_active: bool = False

        # Define target laps/reps per stage
        self.STAGE_TARGETS = {
            HyroxStage.SLED_PUSH: 4,      # 4 lengths of 20m
            HyroxStage.SLED_PULL: 4,      # 4 lengths of 20m
            HyroxStage.BURPEE_BROAD: 4,   # 4 lengths of 20m
            HyroxStage.FARMERS_CARRY: 4,  # 4 lengths of 20m
            HyroxStage.SANDBAG_LUNGES: 4, # 4 lengths of 20m
            HyroxStage.WALL_BALLS: 75,    # 75 successful throws
        }

        # Track the last registered location of tag for sequence validation
        # tag_id -> last_registered_location ("start_line" | "finish_line")
        self._last_positions: Dict[str, str] = {}

    def start_race(self):
        self._is_active = True
        # Set start timestamps for RUN_1 for all registered athletes

    def register_tag_crossing(
        self,
        tag_id: str,
        location: str,
        rssi: float,
        timestamp_ms: int,
        station_number: Optional[int] = None,
    ):
        """
        Processes tag crossings on timing mats.
        Filters out cross-talk, validates sequence, updates laps, and handles stage progression.
        Also dynamically binds the athlete to the reading station_number.
        """
        if not self._is_active:
            return

        # 1. Spillover filter (cross-talk prevention)
        if rssi < -60.0:
            logger.debug(f"Filtered signal from tag {tag_id} due to low RSSI: {rssi}")
            return

        athlete = self._athletes.get(tag_id)
        if not athlete:
            return

        current_stage = athlete.current_stage

        # Only process RFID crossings if the athlete is in a physical lane stage
        lane_stages = {
            HyroxStage.SLED_PUSH,
            HyroxStage.SLED_PULL,
            HyroxStage.BURPEE_BROAD,
            HyroxStage.FARMERS_CARRY,
            HyroxStage.SANDBAG_LUNGES
        }

        if current_stage not in lane_stages:
            return

        # 2. Sequence Validation (verify movement between start & finish)
        last_pos = self._last_positions.get(tag_id)
        if last_pos == location:
            # Duplicate reading or athlete didn't complete the full length
            return

        # Valid lap completed
        self._last_positions[tag_id] = location
        stage_name = current_stage.value
        athlete.stage_laps[stage_name] = athlete.stage_laps.get(stage_name, 0) + 1

        # Check completion
        target = self.STAGE_TARGETS.get(current_stage, 4)
        if athlete.stage_laps[stage_name] >= target:
            self._transition_to_next_stage(athlete, timestamp_ms)

    def register_wallball_rep(self, station_number: int, timestamp_ms: int):
        """
        Registers a valid wall ball rep for the athlete bound to this station.
        """
        if not self._is_active:
            return

        tag_id = self._station_to_tag.get(station_number)
        if not tag_id:
            return

        athlete = self._athletes.get(tag_id)
        if not athlete or athlete.current_stage != HyroxStage.WALL_BALLS:
            return

        stage_name = HyroxStage.WALL_BALLS.value
        athlete.stage_laps[stage_name] = athlete.stage_laps.get(stage_name, 0) + 1

        target = self.STAGE_TARGETS.get(HyroxStage.WALL_BALLS, 75)
        if athlete.stage_laps[stage_name] >= target:
            self._transition_to_next_stage(athlete, timestamp_ms)

    def _transition_to_next_stage(self, athlete: AthleteHyroxState, timestamp_ms: int):
        # Progress athlete to the next stage in the HyroxStage enum sequence.
        # Record stage end time and next stage start time.
        pass
```

### 4.3 Adapt MQTT Subscriber
In [hub_server/adapters/mqtt_subscriber.py](file:///Users/tunghunglu/projects/fitRace/hub_server/adapters/mqtt_subscriber.py), route incoming sensors to the manager:

```python
# Inside MqttSubscriber.start_listening:
self._mqtt_client._client.subscribe("gym/telemetry/rfid/+")
self._mqtt_client._client.subscribe("gym/telemetry/wallball/+")

# Inside MqttSubscriber._on_message:
if topic.startswith("gym/telemetry/rfid/"):
    # Parse payload as RFID telemetry
    # Call hyrox_manager.register_tag_crossing(...)
elif topic.startswith("gym/telemetry/wallball/"):
    # Parse payload as Wall Ball event
    # Call hyrox_manager.register_wallball_rep(...)
```

---

## 5. TDD Verification Plan

To implement this functionality following the project's strict TDD pipeline, developer agents must implement the following tests *prior* to executing source code changes.

### 5.1 Unit Tests (Red-Green Pipeline)
*   **File**: `tests/unit/hub/test_hyrox_manager.py`
    *   **Test Case 1 (`test_rfid_spillover_filter`)**:
        Feed RFID events with `rssi = -75.0`. Verify the manager discards the event and athlete lap counts remain at `0`. Feed another event with `rssi = -42.0` and verify it gets registered.
    *   **Test Case 2 (`test_rfid_sequence_validation`)**:
        Simulate an athlete crossing the `start_line` twice consecutively without hitting the `finish_line`. Verify the lap counter remains at `0`. Simulate crossing sequence `Start -> Finish -> Start -> Finish` and verify lap counter increments to `2`.
    *   **Test Case 3 (`test_stage_transitions`)**:
        Simulate an athlete completing 4 laps of Sled Push. Verify that the athlete's `current_stage` transitions automatically to `RUN_2` and triggers WebSocket broadcasts.
    *   **Test Case 4 (`test_wallball_counts`)**:
        Simulate wall ball event payloads on station 2. Verify that only the athlete bound to station 2 receives counts, and other athletes are unaffected.

### 5.2 Integration Tests
*   **File**: `tests/integration/test_hyrox_sensor_stream.py`
    *   **Test Case**: Start Uvicorn, mock the MQTT Client connection, publish a sequence of RFID and Wallball payloads over the network, and assert:
        1.  Database records update state correctly.
        2.  WebSocket connections to `/ws/dashboard` receive JSON payloads reflecting stage transitions and lap counts.

### 5.3 Force-Completion REST Endpoint
*   **POST `/api/hyrox/complete-stage`**: Used by the coach UI or manually triggered when RFID sensors fail or to advance stationary workouts (`SKI_ERG`, `ROW`).
    *   **Payload**:
        ```json
        {
          "rfid_tag_id": "EPC_TONY_AUTO"
        }
        ```
