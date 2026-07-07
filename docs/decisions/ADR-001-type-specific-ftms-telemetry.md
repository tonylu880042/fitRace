# ADR-001: Preserve Type-Specific FTMS Telemetry

## Status
Accepted

## Date
2026-07-06

## Context
The UART antenna board reports FTMS data as `FTMS:<MAC>,<TYPE>,{json};`.
The board-defined `TYPE` changes the shape of the JSON payload:

- `TMILL`, `BIKE`, and `ELLIP` are speed-based and report `instantaneous_speed`.
- `ROWER` reports `stroke_rate` and `instantaneous_pace` instead of speed.
- `UNKNOWN` may only report `rssi`.

FitRace Central Hub still needs a stable common telemetry shape for race scoring,
station assignment, and dashboard rendering. If the Edge Node only publishes the
common fields, device-specific values such as rower pace, antenna RSSI, and
board-reported total energy are lost.

## Decision
Edge Node telemetry will publish two layers:

1. Common normalized fields used by Central Hub scoring:
   `instantaneous_speed_kph`, `cadence_rpm`, `power_watts`, `distance_m`,
   `elapsed_time_ms`, and `calories`.
2. Type-specific FTMS fields used for diagnostics and future device-specific UI:
   `mac_address`, `ftms_type`, `rssi`, `pace_sec_per_500m`,
   `total_energy_kcal`, `ftms_payload`, and `raw_payload`.

`ftms_payload` is shaped by board `TYPE`:

- `kind=speed` for `TMILL`, `BIKE`, and `ELLIP`
- `kind=rower` for `ROWER`
- `kind=unknown` for `UNKNOWN`

Central Hub will accept and preserve these fields when ingesting MQTT telemetry,
while existing race scoring continues to use the common normalized metrics.

## Alternatives Considered

### Only Publish Raw UART JSON
- Pros: No information loss.
- Cons: Central Hub and dashboard would need to understand every board type and
  firmware field before scoring. This couples race logic to board firmware.
- Rejected: Too fragile for existing dashboard and race code.

### Only Publish Common Normalized Fields
- Pros: Simple and compatible with current scoring.
- Cons: Loses rower pace, RSSI, total energy, and future device-specific fields.
- Rejected: Insufficient for installation diagnostics and device-specific UI.

### Separate MQTT Topics Per Device Type
- Pros: Strongly separates schemas.
- Cons: More subscriber logic and harder station assignment.
- Rejected: Unnecessary while one telemetry envelope can carry both common and
  type-specific data.

## Consequences
- Existing Central Hub scoring remains compatible.
- Edge Node MQTT payloads become richer and larger.
- Future dashboards can display rower pace, board RSSI, and calories directly.
- Any new antenna board `TYPE` should add a new typed payload shape while still
  filling the common normalized fields as far as possible.
