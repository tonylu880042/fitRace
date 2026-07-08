# Hyrox System Architecture & Development Plan

## 1. Core Reframe

The existing FitRace model assumes:

```text
participant -> fixed station -> fixed equipment stream -> race progress
```

That model works for single-station races because the participant stays on one device or one station for the whole competition.

Hyrox must use a different model:

```text
participant/team -> ordered course progress -> current stage -> assigned resource
```

In Hyrox, the station is not the participant identity. A station is a physical resource pool that many participants pass through over time. The system must therefore separate:

1. **Course stage** - the ordered Hyrox step, for example `run_3`, `sled_pull`, `row`.
2. **Resource group** - a physical area or equipment pool that can serve one stage, for example four shared turf lanes or eight treadmills.
3. **Resource unit** - the actual lane, machine, RFID endpoint pair, wall-ball target, or FTMS device.
4. **Athlete/team state** - the participant's current course stage, assigned resource, progress counters, and timing.

This is the most important architectural change. `station_number` alone is no longer enough because it currently mixes several meanings: physical station, equipment index, athlete binding, and course progress.

## 2. Domain Vocabulary

### Course Stage

A step in the Hyrox sequence. Stages are ordered and drive the state machine.

Examples:

- `run_1`
- `ski_erg`
- `run_2`
- `sled_push`
- `run_3`
- `sled_pull`
- `run_4`
- `burpee_broad_jump`
- `run_5`
- `row`
- `run_6`
- `farmers_carry`
- `run_7`
- `sandbag_lunges`
- `run_8`
- `wall_balls`
- `finished`

### Resource Group

A physical area or pool of equipment for a stage type.

Examples:

- `run_treadmills` - eight treadmills for running stages.
- `ski_erg_pool` - four SkiErg machines.
- `row_pool` - four rowing machines.
- `shared_turf_lanes` - four lanes reused by sled push, sled pull, burpee broad jump, farmers carry, and sandbag lunges.
- `wall_ball_targets` - four wall-ball targets.

### Resource Unit

The individual usable resource inside a group.

Examples:

- `treadmill-01`
- `rower-03`
- `turf-lane-2`
- `wallball-target-4`

### Sensor Endpoint

One telemetry source mapped to a resource unit.

Examples:

- FTMS stream from a treadmill.
- FTMS stream from rower or SkiErg.
- RFID start antenna of `turf-lane-1`.
- RFID finish antenna of `turf-lane-1`.
- Wall-ball target counter.

### Athlete Resource Assignment

The temporary claim that says which participant is currently using which resource unit for the current stage.

This must be separate from registration. A participant can be registered once, but they will hold many resource assignments over the race.

## 3. Sensor Classes

Hyrox telemetry must be normalized by sensor class before it updates race progress.

| Sensor class | Used by | Progress source | Required mapping |
| --- | --- | --- | --- |
| `ftms_machine` | treadmill, SkiErg, rower, bike if used | distance, speed, watts, time, stroke rate | stage type, resource group, resource unit, node id |
| `rfid_endpoint_pair` | sled push, sled pull, burpee broad jump, farmers carry, sandbag lunges | alternating endpoint crossings | stage candidates, shared lane id, start endpoint, finish endpoint |
| `rfid_entry_gate` | stage entry/exit gates, track lap timing | crossing time, lap count, stage entry | physical gate role, optional stage type |
| `rep_counter` | wall balls | valid rep count | stage type, target id |
| `manual_override` | operations recovery | admin action | actor, reason, timestamp |

The ingestion pipeline should not directly interpret raw MQTT payloads as Hyrox progress. It should first resolve them into a normalized `HyroxTelemetryEvent`.

## 4. Equipment Mapping Model

The system needs a persistent venue configuration that maps sensors to resource units.

```json
{
  "venue_id": "fitrace-hq",
  "course_profile": "hyrox_standard_2026",
  "resource_groups": [
    {
      "group_id": "run_treadmills",
      "resource_type": "ftms_machine_pool",
      "stage_candidates": [
        "run_1",
        "run_2",
        "run_3",
        "run_4",
        "run_5",
        "run_6",
        "run_7",
        "run_8"
      ],
      "units": [
        {
          "resource_id": "treadmill-01",
          "display_name": "Treadmill 1",
          "sensor_class": "ftms_machine",
          "node_id": "edge-01-treadmill-01",
          "equipment_type": "treadmill"
        }
      ]
    },
    {
      "group_id": "shared_turf_lanes",
      "resource_type": "rfid_lane_pool",
      "stage_candidates": [
        "sled_push",
        "sled_pull",
        "burpee_broad_jump",
        "farmers_carry",
        "sandbag_lunges"
      ],
      "units": [
        {
          "resource_id": "turf-lane-1",
          "display_name": "Turf Lane 1",
          "lane_length_m": 12.5,
          "sensor_class": "rfid_endpoint_pair",
          "start_endpoint": {
            "node_id": "rfid-reader-01",
            "antenna_id": "L1_START"
          },
          "finish_endpoint": {
            "node_id": "rfid-reader-01",
            "antenna_id": "L1_FINISH"
          }
        }
      ]
    }
  ]
}
```

## 5. Stage Target Model

Stage targets should live in a course profile, not be hardcoded in `HyroxManager`.

```json
{
  "course_profile_id": "hyrox_standard_2026",
  "stages": [
    {
      "stage": "run_1",
      "target_type": "distance_m",
      "target_value": 1000,
      "allowed_resource_groups": ["run_treadmills", "run_track"]
    },
    {
      "stage": "sled_push",
      "target_type": "lengths",
      "target_value": 4,
      "allowed_resource_groups": ["shared_turf_lanes"]
    },
    {
      "stage": "row",
      "target_type": "distance_m",
      "target_value": 1000,
      "allowed_resource_groups": ["row_pool"]
    },
    {
      "stage": "wall_balls",
      "target_type": "reps",
      "target_value": 75,
      "allowed_resource_groups": ["wall_ball_targets"]
    }
  ]
}
```

This lets the operator change venue-specific execution without changing the race sequence.

## 6. Shared Lane Stage Inference

The same four turf lanes may serve several Hyrox stages. The sensor cannot know the stage by itself. The Hub must infer the stage from athlete state.

### Required Rule

For RFID events from `shared_turf_lanes`, resolve the stage by:

```text
tag_id -> athlete/team -> current_stage -> allowed_resource_groups -> resource_id/lane_id
```

The event is valid only when:

1. The tag is registered.
2. The athlete's current stage allows the resource group.
3. The antenna belongs to one configured resource unit.
4. The participant is assigned to that lane, or dynamic lane claim is allowed and the lane is free.
5. Endpoint order alternates for length-counted stages.

The event must be rejected or flagged when:

1. The athlete is not currently in a stage that can use that resource group.
2. The lane is already claimed by another active participant.
3. The RFID read is from a neighboring lane.
4. The endpoint repeats and does not complete a length.
5. The event would skip the course sequence.

### Dynamic Claim Rule

Training mode may allow the first valid RFID endpoint read to claim a free lane.

Competition mode should prefer explicit assignment:

```text
athlete enters stage -> operator or queue assigns lane -> RFID reads must match that lane
```

This protects official timing from cross-lane ambiguity.

## 7. FTMS Equipment Pools

FTMS devices should not be modeled as fixed participant stations in Hyrox. They are resource units.

### Example: Eight Treadmills, Four Lanes

If the venue has four turf lanes, a practical setup may need eight treadmills to absorb flow. The system should support:

```text
run_treadmills:
  resource_count = 8
  stage_candidates = all run stages

shared_turf_lanes:
  resource_count = 4
  stage_candidates = sled_push, sled_pull, burpee_broad_jump, farmers_carry, sandbag_lunges
```

The scheduler or operator assigns a participant to an available treadmill for each run stage. FTMS telemetry then updates only the participant assigned to that resource.

### FTMS Resolution Rule

When an FTMS payload arrives:

```text
node_id -> resource_id -> active assignment -> athlete/team -> current_stage
```

Then validate:

1. The resource is assigned.
2. The athlete's current stage allows that resource group.
3. The FTMS metric matches the stage target type.
4. Progress is monotonic and handles device counter resets.

## 8. Proposed Domain Models

These are conceptual contracts; exact Pydantic names can be adjusted during implementation.

```python
class HyroxSensorClass(str, Enum):
    FTMS_MACHINE = "ftms_machine"
    RFID_ENDPOINT_PAIR = "rfid_endpoint_pair"
    RFID_ENTRY_GATE = "rfid_entry_gate"
    REP_COUNTER = "rep_counter"
    MANUAL_OVERRIDE = "manual_override"


class HyroxTargetType(str, Enum):
    DISTANCE_M = "distance_m"
    LENGTHS = "lengths"
    REPS = "reps"
    TIME_MS = "time_ms"
    MANUAL = "manual"


class HyroxResourceUnit(BaseModel):
    resource_id: str
    display_name: str
    sensor_class: HyroxSensorClass
    equipment_type: str | None = None
    node_id: str | None = None
    lane_length_m: float | None = None
    start_endpoint: HyroxEndpointSensor | None = None
    finish_endpoint: HyroxEndpointSensor | None = None


class HyroxResourceGroup(BaseModel):
    group_id: str
    resource_type: str
    stage_candidates: list[HyroxStage]
    units: list[HyroxResourceUnit]


class HyroxStageDefinition(BaseModel):
    stage: HyroxStage
    target_type: HyroxTargetType
    target_value: float
    allowed_resource_groups: list[str]


class HyroxResourceAssignment(BaseModel):
    assignment_id: str
    rfid_tag_id: str
    stage: HyroxStage
    resource_id: str
    status: Literal["active", "released"]
    assigned_at_epoch_ms: int
    released_at_epoch_ms: int | None = None
    source: Literal["operator", "dynamic_claim", "auto_scheduler"]
```

## 9. Ingestion Pipeline

Target pipeline:

```text
MQTT payload
  -> payload validation
  -> sensor registry resolution
  -> normalized HyroxTelemetryEvent
  -> athlete/team state lookup
  -> resource assignment validation
  -> stage progress reducer
  -> state transition evaluator
  -> websocket broadcast + audit log
```

### Normalized Event

```json
{
  "event_id": "evt_...",
  "sensor_class": "rfid_endpoint_pair",
  "resource_group_id": "shared_turf_lanes",
  "resource_id": "turf-lane-1",
  "endpoint": "finish_line",
  "tag_id": "E28011052000789A",
  "timestamp_epoch_ms": 1780000000000,
  "raw_payload": {}
}
```

All downstream logic should consume this normalized event, not raw MQTT payloads.

## 10. State Machine

The Hyrox state machine should be sequence-authoritative.

### Stage Entry

An athlete enters a stage when:

1. The previous stage is complete.
2. The next ordered stage is started by sensor evidence, operator action, or scheduled transition.
3. The stage start time is recorded.

### Stage Progress

Progress reducers differ by target type:

- `distance_m`: use FTMS delta distance or RFID track laps converted to distance.
- `lengths`: use alternating RFID endpoints inside the assigned lane.
- `reps`: use rep counter events attributed through an active resource assignment.
- `time_ms`: use elapsed stage time.
- `manual`: operator completion only.

### Stage Completion

A stage is complete when:

```text
stage_progress >= stage_target
```

Completion releases the current resource assignment and moves the athlete to the next stage.

## 11. API Surface

Add Hyrox-specific configuration APIs. Do not overload `/api/stations/assign`, because FitRace station assignment has a different meaning.

### Configuration

```text
GET  /api/hyrox/course-profile
PUT  /api/hyrox/course-profile
GET  /api/hyrox/venue-config
PUT  /api/hyrox/venue-config
POST /api/hyrox/venue-config/validate
```

### Assignments

```text
GET  /api/hyrox/resources
GET  /api/hyrox/resources/availability
POST /api/hyrox/assignments
PATCH /api/hyrox/assignments/{assignment_id}/release
POST /api/hyrox/assignments/auto
```

### Race Operations

```text
POST /api/hyrox/register
POST /api/hyrox/start
POST /api/hyrox/complete-stage
GET  /api/hyrox/state
GET  /api/hyrox/audit-events
```

## 12. UI Implications

The System Admin UI should become a venue setup console:

1. **Course Profile** - stage order and targets.
2. **Resource Groups** - treadmill pool, row pool, SkiErg pool, turf lanes, wall-ball targets.
3. **Sensor Pairing** - node/antenna/channel mapping to resources.
4. **Validation** - duplicate sensors, missing endpoints, resource group mismatch, capacity warnings.
5. **Readiness** - tells the operator whether the course is competition-ready.

The Hyrox Admin UI should become an operations console:

1. Heat roster and RFID tag binding.
2. Current stage per athlete/team.
3. Resource assignment board.
4. Manual override with reason.
5. Lane/device conflict warnings.

## 13. Migration Strategy From Current Implementation

### Current Limitation

Current `HyroxManager` uses:

- Hardcoded `STATION_TO_WORKOUT`.
- Hardcoded `STAGE_TARGETS`.
- `_station_to_tag` as a temporary attribution map.
- RFID payload `station_number` to infer workout type.
- Shared `_last_positions[tag_id]` without lane/resource context.

This is sufficient for a simulator, but it will not handle real Hyrox traffic with shared lanes and FTMS resource pools.

### Migration Principle

Do not rewrite everything at once. Introduce the new model behind the existing Hyrox feature flag and keep current endpoints working while adding a resource-aware path.

## 14. Development Plan

### Phase 1 - Contracts and Validation

**Goal:** Introduce the new model without changing live behavior.

Tasks:

1. Add domain models for course profiles, resource groups, resource units, sensor endpoints, and resource assignments.
2. Add validation rules for duplicate sensors, missing RFID endpoint pairs, invalid stage candidates, and target/resource mismatch.
3. Add unit tests for venue config validation.
4. Keep current `/api/hyrox/configure` behavior intact.

Acceptance:

- Invalid duplicate antenna mappings are rejected.
- Shared turf lanes can declare multiple stage candidates.
- FTMS resources can be mapped to run, row, and ski stages.
- Existing Hyrox simulator tests still pass.

### Phase 2 - Sensor Registry and Normalized Events

**Goal:** Resolve raw telemetry into resource-aware events.

Tasks:

1. Build a `HyroxSensorRegistry` from venue config.
2. Add normalized `HyroxTelemetryEvent` contracts.
3. Update MQTT RFID and FTMS ingestion to optionally resolve Hyrox resources.
4. Add tests for RFID endpoint resolution and FTMS node resolution.

Acceptance:

- `node_id + antenna_id` resolves to one lane endpoint.
- `node_id` resolves to one FTMS resource unit.
- Unknown sensors are ignored or surfaced as diagnostics without changing athlete progress.

### Phase 3 - Resource Assignments

**Goal:** Attribute sensor events to the right athlete/team through active assignments.

Tasks:

1. Add assignment APIs and in-memory assignment store.
2. Support explicit operator assignment for competition mode.
3. Support dynamic lane claim for training mode.
4. Prevent two active athletes from claiming the same resource.

Acceptance:

- FTMS data from `treadmill-01` only updates the athlete assigned to `treadmill-01`.
- RFID lane reads only update the athlete assigned to that lane.
- Lane conflict produces a diagnostic event.

### Phase 4 - Stage Reducers

**Goal:** Replace hardcoded stage progress with target-type reducers.

Tasks:

1. Implement `distance_m` reducer for FTMS distance.
2. Implement `lengths` reducer for alternating RFID endpoints per `tag_id + stage + resource_id`.
3. Implement `reps` reducer for wall-ball targets.
4. Preserve manual completion as an override path.

Acceptance:

- Sled push/pull/burpee/farmers/sandbag progress is counted per assigned lane and current stage.
- Same physical lane can correctly serve different stages based on athlete current stage.
- Duplicate same-endpoint RFID reads do not increment lengths.
- FTMS counter resets do not decrease progress.

### Phase 5 - Course State Machine

**Goal:** Make stage order and targets config-driven.

Tasks:

1. Move `STAGE_ORDER` and `STAGE_TARGETS` into course profile config.
2. Enforce ordered transitions by default.
3. Add explicit out-of-sequence diagnostic events.
4. Release resource assignment when a stage completes.

Acceptance:

- An athlete cannot skip from `run_2` to `row` through sensor noise.
- Shared-lane RFID reads infer the stage from athlete state, not station number.
- Stage completion reliably releases the occupied resource.

### Phase 6 - UI Integration

**Goal:** Make system setup and race operations usable.

Tasks:

1. Replace the current local-only Hyrox lane settings draft with a backend-backed venue config editor.
2. Add readiness validation to System Admin.
3. Add resource assignment board to Hyrox Admin.
4. Add diagnostics panels for rejected events, conflicts, and unknown sensors.

Acceptance:

- Operator can configure eight treadmills and four turf lanes.
- Operator can validate readiness before starting a heat.
- Operator can see which athlete is assigned to which resource.
- Rejected sensor events are explainable.

### Phase 7 - Persistence and Recovery

**Goal:** Survive restarts and support event auditability.

Tasks:

1. Persist course profile, venue config, assignments, athlete state, and audit events.
2. Add import/export for venue setup.
3. Add recovery flow for restarting the Hub mid-event.

Acceptance:

- A configured venue survives Hub restart.
- Active race state can be restored or explicitly abandoned.
- Audit log can explain scoring decisions after the event.

## 15. Testing Strategy

### Unit Tests

- Venue config validation.
- Sensor registry lookup.
- Assignment conflict detection.
- RFID length reducer.
- FTMS distance reducer.
- Course state transition guard.

### Integration Tests

- Configure venue with eight treadmills and four turf lanes.
- Register multiple athletes.
- Assign athletes to resources.
- Simulate FTMS run, RFID turf stage, FTMS row, wall-ball reps.
- Verify stage progression and resource release.

### Simulator Updates

The Hyrox simulator should evolve from station-number scripts to resource-aware scripts:

```text
athlete -> current stage -> resource assignment -> sensor event stream
```

It should simulate:

- Multiple athletes queued for limited resources.
- Eight treadmill resources and four turf lanes.
- Shared turf lanes used by different stages.
- RFID cross-talk and duplicate reads.
- FTMS distance and reset events.

## 16. Open Questions

1. Should competition mode require operator assignment for every resource, or allow auto-scheduler assignment when a resource is free?
2. For running, will the real venue use treadmills, track RFID lap gates, or both?
3. Should doubles and relay be modeled as one `team_id` with member tags, or as multiple athlete records under one team state?
4. Do we need heat-level capacity planning before start, for example maximum active participants by resource bottleneck?
5. Should wall-ball targets be claimable by RFID entry gate, operator assignment, or both?

## 17. Recommended Next Step

Implement Phase 1 and Phase 2 first. They create the contracts and sensor-resolution layer without forcing a full rewrite of the current simulator-backed Hyrox manager.

The first implementation PR should not touch the UI. It should add the backend contracts, validation tests, and sensor registry. Once those contracts are stable, the System Admin and Hyrox Admin screens can safely persist real settings instead of local drafts.
