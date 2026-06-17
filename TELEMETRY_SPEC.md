# FitRaceStudio Telemetry Specification

Status: Draft for local integration testing
Last updated: 2026-06-16

This document defines the normalized telemetry message sent from an Edge Node to the Central Hub. It is the contract between equipment capture logic, the antenna board integration layer, MQTT transport, race scoring, and dashboard visualization.

## 1. Scope

Telemetry represents one equipment stream at one point in time.

An Edge Node may expose multiple equipment streams. The physical Edge Node identity and the stream identity are different:

```text
Physical Edge Node: fitrace-edge-01
Telemetry stream:   fitrace-edge-01-01
Equipment ID:       TREAD_01
```

Central Hub ranks and binds stations by telemetry stream `node_id`, not by physical Edge Node ID.

## 2. Transport

### MQTT Broker

The MQTT broker runs on the Central Hub LAN.

```text
Host: Central Hub IP or hostname
Port: 1883
QoS: 1 recommended
Retain: false
Payload encoding: UTF-8 JSON
```

### Topic

Each telemetry stream publishes to:

```text
gym/telemetry/{node_id}
```

Example:

```text
gym/telemetry/fitrace-edge-01-03
```

The `{node_id}` topic suffix must match the payload `node_id`.

## 3. Message Schema

### Required Fields

| Field | Type | Unit | Required | Description |
| --- | --- | --- | --- | --- |
| `node_id` | string | n/a | yes | Unique telemetry stream identity. Used by Central Hub for station binding and ranking. |
| `equipment_id` | string | n/a | yes | Human/service identity for the connected equipment, for example `TREAD_01`. |
| `equipment_type` | string | n/a | yes | Normalized equipment type. See supported values below. |
| `timestamp_epoch_ms` | integer | ms since Unix epoch | yes | Time when the Edge Node produced this sample. |

### Recommended Measurement Fields

These fields should be present in every normal sample. If a device cannot provide a value, send `0` rather than omitting the field unless the field is truly not applicable.

| Field | Type | Unit | Default | Description |
| --- | --- | --- | --- | --- |
| `instantaneous_speed_kph` | number | km/h | `0.0` | Current speed. For equipment without speed, send `0.0`. |
| `cadence_rpm` | integer | rpm or strides/min | `0` | Cadence. For treadmill this may mean steps/strides per minute. |
| `power_watts` | integer | W | `0` | Current power output. |
| `heart_rate_bpm` | integer | bpm | `0` | Heart rate if available. |
| `distance_m` | number | m | `0.0` | Cumulative distance for this stream since the start of the current equipment session/race feed. |
| `elapsed_time_ms` | integer | ms | `0` | Cumulative elapsed time for this stream. |

### Optional Fields

| Field | Type | Unit | Description |
| --- | --- | --- | --- |
| `edge_node_id` | string | n/a | Physical Edge Node that produced the stream, for example `fitrace-edge-01`. Recommended for diagnostics. |
| `calories` | number | kcal-like score | Cumulative calories reported by equipment or calculated by Edge Node. If omitted, Central Hub currently estimates it from power and elapsed time. |

## 4. Supported Equipment Types

Current normalized values:

| Value | Meaning |
| --- | --- |
| `treadmill` | Treadmill |
| `fan_bike` | Fan bike / air bike |
| `spin_bike` | Spin bike |
| `indoor_bike` | Indoor bike |
| `rowing_machine` | Rowing machine |
| `rower` | Rowing machine alias |
| `ski_erg` | Ski erg |

New values may be added, but Central Hub and dashboard behavior should be checked before using them in production.

## 5. JSON Example

```json
{
  "node_id": "fitrace-edge-01-03",
  "edge_node_id": "fitrace-edge-01",
  "equipment_id": "TREAD_03",
  "equipment_type": "treadmill",
  "instantaneous_speed_kph": 10.4,
  "cadence_rpm": 162,
  "power_watts": 184,
  "heart_rate_bpm": 131,
  "distance_m": 124.6,
  "elapsed_time_ms": 45000,
  "timestamp_epoch_ms": 1781582468098
}
```

## 6. Field Semantics

### `node_id`

`node_id` identifies one independent telemetry stream. It must be stable during an event.

Good examples:

```text
fitrace-edge-01-01
fitrace-edge-01-02
fitrace-edge-02-01
```

Do not reuse the same `node_id` for two simultaneous equipment streams. Central Hub will treat them as one participant/device.

### `edge_node_id`

`edge_node_id` identifies the physical Edge Node. This is useful when one Edge Node publishes multiple streams.

Example:

```json
{
  "edge_node_id": "fitrace-edge-01",
  "node_id": "fitrace-edge-01-04"
}
```

### `equipment_id`

`equipment_id` is the field technician/operator label for a specific equipment binding. It should match labels shown in the Edge setup page and Central node status.

Examples:

```text
TREAD_01
BIKE_02
ROW_03
```

### `timestamp_epoch_ms`

The timestamp is produced by the Edge Node at the time of sample generation or packet normalization.

Central Hub currently scores by `elapsed_time_ms` and cumulative metrics, not by arrival time. Still, timestamps are required for diagnostics, buffering, and future data quality checks.

### `distance_m`

`distance_m` is cumulative meters for the telemetry stream.

Expected behavior:

- It should increase monotonically while the equipment is active.
- It should not reset during a running race.
- If the equipment resets unexpectedly, the Edge Node should either recover the cumulative value or report an error/status outside the telemetry payload.

### `elapsed_time_ms`

`elapsed_time_ms` is cumulative active elapsed time for the stream.

Expected behavior:

- It should increase monotonically while the equipment is active.
- It should be in milliseconds.
- It is used as finish time for distance/calories races.

### `calories`

`calories` is optional in the current implementation.

If omitted, Central Hub estimates:

```text
calories = power_watts * (elapsed_time_ms / 1000) / 1000
```

This is a simple score estimate, not a physiology-grade kcal calculation. For production devices that provide calories through FTMS or antenna board data, Edge Node should send cumulative `calories` directly.

## 7. Validation Rules

Edge Node should enforce these before publishing:

| Rule | Requirement |
| --- | --- |
| Required identity | `node_id`, `equipment_id`, `equipment_type`, and `timestamp_epoch_ms` must be present. |
| Non-negative values | Speed, cadence, power, heart rate, distance, elapsed time, and calories must not be negative. |
| Stable stream ID | `node_id` must remain stable for the binding during a race. |
| Cumulative metrics | `distance_m`, `elapsed_time_ms`, and `calories` should be cumulative, not interval deltas. |
| JSON only | Payload must be a JSON object, not an array or nested envelope. |
| Topic match | MQTT topic suffix `{node_id}` should match payload `node_id`. |

## 8. Central Hub Scoring Behavior

Central Hub subscribes to:

```text
gym/telemetry/#
```

When telemetry arrives:

1. Central reads `node_id`.
2. Central records `node_id -> equipment_type` as an active node.
3. If the race is not `RUNNING`, Central does not score progress.
4. If the race is `RUNNING`, Central updates leaderboard progress for that `node_id`.

### Distance Race

Uses:

```text
distance_m / target_value
```

Finish time is the first `elapsed_time_ms` where progress reaches or exceeds 100%.

### Calories Race

Uses:

```text
calories / target_value
```

If `calories` is missing, Central uses the current estimate described above.

### Time Race

Uses:

```text
elapsed_time_ms / (duration_sec * 1000)
```

Ranking then depends on the dashboard sort rules for the selected race type.

### Max Power Race

Central tracks:

```text
max_power_watts = max(previous max_power_watts, current power_watts)
```

Duration progress still uses `elapsed_time_ms`.

## 9. Finish Locking

For distance and calories races, when a stream reaches 100% progress:

- Central stores `finished_time_ms`.
- Later telemetry for the same `node_id` will not change distance, calories, or elapsed time used for final ranking.
- Speed and current power are shown as `0` after finish lock.

This prevents late packets from changing a finished athlete's result.

## 10. Publishing Frequency

Recommended telemetry frequency:

```text
2 Hz per stream (one sample every 500 ms)
```

Acceptable range for field testing:

```text
1-5 Hz per stream
```

For many streams on one Edge Node, the Edge Node may stagger publishing to avoid bursts, but each stream should still publish regularly.

## 11. Error Handling

Telemetry payloads should contain only normalized measurement data. Connection status, RSSI, antenna channel state, and error codes belong in the Edge Node status heartbeat, not in the telemetry payload.

Use node status topic:

```text
fitrace/nodes/{edge_node_id}/status
```

for fields such as:

- stream status
- antenna channel
- RSSI
- last telemetry timestamp
- error code

## 12. Compatibility Notes

Current implementation details:

- Edge model: `edge_node.domain.models.TelemetryData`
- Edge MQTT topic: `gym/telemetry/{node_id}`
- Central subscriber: `hub_server.adapters.mqtt_subscriber.MqttSubscriber`
- Central scoring: `hub_server.usecases.race_manager.RaceManager.update_telemetry`

The current local test setup can publish 10 treadmill streams from one local Edge Node. Product hardware planning may still define a lower normal operating limit depending on antenna board capacity.

## 13. Minimal Payload

This is the smallest valid payload shape for Central to identify a stream and avoid schema errors at the Edge model layer:

```json
{
  "node_id": "fitrace-edge-01-01",
  "equipment_id": "TREAD_01",
  "equipment_type": "treadmill",
  "timestamp_epoch_ms": 1781582468098
}
```

For race scoring, the recommended payload should include measurement fields:

```json
{
  "node_id": "fitrace-edge-01-01",
  "edge_node_id": "fitrace-edge-01",
  "equipment_id": "TREAD_01",
  "equipment_type": "treadmill",
  "instantaneous_speed_kph": 10.2,
  "cadence_rpm": 160,
  "power_watts": 180,
  "heart_rate_bpm": 130,
  "distance_m": 50.3,
  "elapsed_time_ms": 18000,
  "timestamp_epoch_ms": 1781582468098
}
```
