# FitRaceStudio Hardware Test Plan

Audience: a coding agent (or human tester) executing this plan on real hardware.
Goal: walk through every user-facing and system feature and confirm it works on
the deployed devices, not just in unit tests.

Related docs: [DEPLOYMENT.md](DEPLOYMENT.md), [TELEMETRY_SPEC.md](TELEMETRY_SPEC.md),
[SYSTEM_FEATURES.md](SYSTEM_FEATURES.md), [OTA_UPDATE.md](OTA_UPDATE.md),
[RPI4_SOAK_TEST_PLAN.md](RPI4_SOAK_TEST_PLAN.md).

---

## 0. How to execute this plan

1. Run phases **in order**. Phases 1–8 are non-destructive. Phase 9 (resilience)
   restarts services. Phase 10 (OTA) and Phase 11 (power) can change or reboot
   devices — run them **last**, and only with operator consent.
2. Record every test in a report file: `output/hardware-test-report-<date>.md`.
   For each test ID record: `PASS` / `FAIL` / `BLOCKED` / `SKIPPED`, the actual
   response (trimmed), and a note if behavior deviated.
3. A test marked **[HUMAN-ASSIST]** needs a physical action (power on a machine,
   pedal a bike, watch a screen). If no human is available, mark it `BLOCKED`
   with the reason — do not fake it with API calls.
4. If a test fails, keep going unless it blocks later phases (e.g. P2 antenna
   failure blocks P3 telemetry). Mark downstream tests `BLOCKED`.
5. Never run destructive endpoints (`reboot`, `shutdown`, OTA `apply`) outside
   their designated phase.

### Environment variables used by this plan

```bash
export HUB="http://fitrace-hub.local:8000"      # or http://<hub-ip>:8000
export EDGE="http://fitrace-edge-01.local:8001" # or http://<edge-ip>:8001
export TOKEN="<value of FITRACE_ADMIN_TOKEN on the devices>"
export AUTH="X-FitRace-Admin-Token: $TOKEN"
```

All `curl` calls assume `-s` and JSON output; pipe through `python3 -m json.tool`
when inspecting. WebSocket tests use `websocat` if available, otherwise the
inline Python snippet in P7.1.

### Test bench requirements

| Item | Requirement |
| --- | --- |
| Central Hub | RPi4/mini-PC running `fitracestudio-hub.service`, port 8000 |
| MQTT broker | Mosquitto on Hub, port 1883 |
| Edge Node | RPi with antenna board on UART (`/dev/ttyAMA0`, `/dev/ttyAMA4`), port 8001 |
| Antenna board | Powered, wired per DEPLOYMENT.md (check Pi5 UART0 gotcha) |
| FTMS equipment | At least 1 real FTMS machine (treadmill/bike/rower), powered on |
| Network | All devices on `fitRace26` AP (or shared LAN) |
| Dashboard screen | Any browser on the LAN (agent may use headless browser) |
| Tester machine | On the same LAN, with `curl`, `python3`, optionally `websocat`, `mosquitto_sub` |

---

## Phase 1 — Preflight and health

**P1.1 Hub health.**
`curl $HUB/health` → HTTP 200, JSON containing status/version fields.

**P1.2 Edge health.**
`curl $EDGE/health` → HTTP 200.

**P1.3 Hub reports its LAN IP.**
`curl $HUB/api/system/ip` → JSON with a non-loopback IP that matches the Hub's
address on the LAN.

**P1.4 mDNS discovery.**
`python3 -c "import socket; print(socket.gethostbyname('fitrace-edge-01.local'))"`
→ resolves to the Edge Node IP. Also check service discovery if `avahi-browse`
is available: `avahi-browse -t _fitrace-edge._tcp --resolve`.

**P1.5 Static pages served.** Each of these returns HTTP 200 with HTML:
`$HUB/` (redirects to dashboard), `$HUB/gameAdmin`, `$HUB/systemAdmin`,
`$HUB/static/signup.html`, `$EDGE/` (edge setup page).

**P1.6 Locales.**
`curl $HUB/api/locales` → lists available locales and a default. Fetch each
listed locale via `$HUB/api/locales/<name>` → 200 with translation map.

**P1.7 MQTT broker reachable.**
`mosquitto_sub -h <hub-ip> -t 'gym/telemetry/#' -C 1 -W 5` — with the Edge Node
running and equipment connected this receives a message; without equipment it
times out (that alone is not a failure, telemetry is verified in P3).

---

## Phase 2 — Edge Node local API and UART antenna board

Auth note: edge setup/diagnostic endpoints require the `$AUTH` header when
`FITRACE_ADMIN_TOKEN` is set on the Edge service.

**P2.1 Read config.**
`curl -H "$AUTH" $EDGE/api/config` → matches the deployed `config.json`
(node_id, mqtt_host, antenna_channels, equipment_bindings).

**P2.2 Write config round-trip.**
POST the exact JSON from P2.1 back to `$EDGE/api/config` → 200. Re-read and
confirm unchanged. (Do not modify values; this only proves the write path.)

**P2.3 Wi-Fi status.**
`curl -H "$AUTH" $EDGE/api/wifi/status` → shows SSID (expected `fitRace26` in a
shipped setup), signal, and connected state.

**P2.4 Antenna config.**
`curl -H "$AUTH" $EDGE/api/antenna/config` → lists both UART channels with
ports/baudrate matching config.

**P2.5 Antenna PING.**
```bash
curl -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"port":"/dev/ttyAMA0","command":"ping"}' $EDGE/api/antenna/command
```
→ 200 with raw UART lines and a parsed response (`PONG`/`BOOT:*`). Repeat for
`/dev/ttyAMA4`. If this fails on a Pi 5, check the UART0 console gotcha in
DEPLOYMENT.md before marking FAIL.

**P2.6 Antenna VERSION and STATUS.**
Same call with `"command":"version"` and `"command":"status"` → parsed firmware
version / status objects.

**P2.7 Antenna SCAN.** [HUMAN-ASSIST: FTMS equipment powered on, in BLE range]
`"command":"scan"` → scan results include the target FTMS device name from
`equipment_bindings[].ble_target`. Device `TYPE` is one of
`TMILL|BIKE|ROWER|ELLIP|UNKNOWN`.

**P2.8 Antenna CONNECT / REPORT / DISCONNECT.** [HUMAN-ASSIST]
1. `"command":"connect"` with the bound target → connected acknowledgment.
2. `"command":"report","report_interval_ms":1000` → accepted (valid range
   100–10000; also verify `50` is rejected).
3. Raw UART lines show periodic FTMS data frames while a human moves the
   equipment.
4. `"command":"disconnect_all"` → clean disconnect.

**P2.9 BLE diagnostic scan (troubleshooting path).**
`curl -H "$AUTH" $EDGE/api/ble/scan` → 200; if the host has BLE hardware,
results include FTMS service UUID `00001826-...`. On antenna-only hardware a
clean "no adapter" error is acceptable — record which.

**P2.10 Edge event log.**
`curl -H "$AUTH" $EDGE/api/monitor/events` → returns recent events including
the antenna commands issued above.

---

## Phase 3 — End-to-end telemetry pipeline

This is the core hardware path: Equipment → BLE/FTMS → antenna board → UART →
Edge Node → MQTT → Hub.

**P3.1 Edge publishes telemetry.** [HUMAN-ASSIST: use the equipment]
With the binding from P2.8 connected:
`mosquitto_sub -h <hub-ip> -t 'gym/telemetry/#' -C 5 -W 30` → ≥1 message per
second per active machine. Payload contains all TELEMETRY_SPEC.md fields:
`node_id`, `equipment_id`, `equipment_type`, `instantaneous_speed_kph`,
`cadence_rpm`, `power_watts`, `heart_rate_bpm`, `distance_m`,
`elapsed_time_ms`, `timestamp_epoch_ms`.

**P3.2 Values are sane.** While a human uses the machine at moderate effort:
speed/power/cadence are non-zero and change over time; `distance_m` is
monotonically increasing; `timestamp_epoch_ms` is within ±10s of tester clock.

**P3.3 Edge status heartbeat.**
`mosquitto_sub -h <hub-ip> -t 'fitrace/nodes/#' -C 1 -W 30` → node status
message for the Edge Node.

**P3.4 Hub sees the node.**
`curl $HUB/api/nodes` → the Edge Node's telemetry streams appear with recent
last-seen timestamps and online status.

**P3.5 Hub diagnostics injection (pipeline self-test).**
Only if `FITRACE_ENABLE_DIAGNOSTICS=1` on the Hub:
```bash
curl -H "$AUTH" -H "Content-Type: application/json" -d '{
  "node_id":"diag-01","equipment_type":"treadmill","distance_m":50,
  "elapsed_time_ms":30000,"instantaneous_speed_kph":6.0,
  "cadence_rpm":60,"power_watts":100}' $HUB/api/diagnostics/telemetry
```
→ `"status":"passed"` with checks api/race_manager/websocket_broadcast ok.
If diagnostics are disabled, expect 404 and record SKIPPED.

---

## Phase 4 — Stations and athlete signup

**P4.1 List stations.**
`curl $HUB/api/stations` → station list (initially unassigned).

**P4.2 Assign a station.**
```bash
curl -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"station_number":1,"node_id":"<telemetry node_id from P3.4>"}' \
  $HUB/api/stations/assign
```
→ 200; `GET /api/stations` shows station 1 bound to that node.

**P4.3 Duplicate assignment rejected.**
Assign the same `node_id` to station 2 → HTTP 409.

**P4.4 Athlete registration (no auth — athlete-facing).**
```bash
curl -H "Content-Type: application/json" \
  -d '{"station_number":1,"athlete_name":"Test Runner","team_name":"Team A"}' \
  $HUB/api/race/register
```
→ 200; stations status shows the athlete on station 1; a
`registration_success` event is broadcast (verify during P7).

**P4.5 Avatar upload.**
Register again with `avatar_base64` set to a small valid WebP (agent may
generate one with Pillow: `Image.new("RGB",(64,64)).save(buf,"WEBP")`).
→ 200 and `$HUB/static/avatars/station_1.webp` is fetchable.
Then register with an invalid payload (`"avatar_base64":"notwebp"`) → 400.

**P4.6 Signup page renders.** [HUMAN-ASSIST or headless browser]
Open `$HUB/static/signup.html?station=1` on a phone/browser → form loads,
station number pre-filled.

---

## Phase 5 — Individual race lifecycle

Run with **real telemetry** from the machine connected in P3. A human operates
the equipment during RUNNING states. Before each configuration, reset:
`curl -X POST -H "$AUTH" $HUB/api/race/reset`.

**P5.1 Readiness gate.**
`curl $HUB/api/race/readiness` → reports readiness; with no race configured,
`POST /api/race/start` must fail with 400.

**P5.2 Distance race, full lifecycle.** [HUMAN-ASSIST]
1. Configure:
   ```bash
   curl -H "$AUTH" -H "Content-Type: application/json" \
     -d '{"race_type":"distance","target_value":100}' $HUB/api/race/configure
   ```
   → state `READY` in response and in `GET /api/race/state`.
2. `POST /api/race/countdown-start` (with `$AUTH`) → returns after ~3s with
   state `RUNNING`. A second `countdown-start` issued during the countdown →
   409 (test with two parallel curl calls).
3. Human covers ≥100 m on the machine → race auto-finishes for that athlete;
   `GET /api/race/state` leaderboard shows finish time.
4. `POST /api/race/stop` → state `STOPPED`.
5. `GET /api/race/results` → contains the finished race with athlete name,
   station, and final metrics.
6. Verify persistence: the results file (default `data/race_results.jsonl` on
   the Hub, or `FITRACE_RACE_RESULTS_PATH`) has a new line for this race.
7. `POST /api/race/close` then `POST /api/race/reset` → state returns to IDLE.

**P5.3 Calories race.** Configure `{"race_type":"calories","target_value":5}`,
start, verify progress accumulates from real telemetry, stop, reset.

**P5.4 Time race.** Configure `{"race_type":"time","duration_sec":60}`, start
via `POST /api/race/start` (direct start path must also work), confirm the race
auto-stops at ~60s (poll `GET /api/race/state`), reset.

**P5.5 Max power race.** Configure
`{"race_type":"max_power","duration_sec":30}`, start; leaderboard ranks by
peak power; auto-stops; reset.

**P5.6 Invalid configurations rejected.** Each returns 400:
- `{"race_type":"distance","target_value":0}`
- `{"race_type":"nosuchtype","target_value":100}`
- `{"race_type":"time"}` (missing duration)

**P5.7 State machine guards.** Each returns 400/409:
- `POST /api/race/stop` while IDLE
- `POST /api/race/countdown-start` while RUNNING
- `POST /api/race/configure` while RUNNING

---

## Phase 6 — Team races

Precondition: ≥2 stations assigned (P4.2) with athletes on ≥2 teams (P4.4). If
only one physical machine exists, mark multi-machine tests `BLOCKED` — team
scoring correctness on synthetic data is already covered by unit tests.

**P6.1 Team race — Average Progress / Aggregate Progress.** [HUMAN-ASSIST]
```bash
curl -H "$AUTH" -H "Content-Type: application/json" -d '{
  "race_type":"distance","target_value":200,"competition_mode":"team",
  "team_scoring_policy":"average_progress",
  "team_completion_policy":"aggregate_progress"}' $HUB/api/race/configure
```
Start; verify `GET /api/race/state` ranks teams (not individuals) and team
progress equals the normalized member average. Team finishes when aggregate
target reached.

**P6.2 Team race — Team Total / All Members Finish.** [HUMAN-ASSIST]
Same but `"team_scoring_policy":"team_total",
"team_completion_policy":"all_members"`. Verify the team is **not** marked
finished until every member reaches the target, even if one member has passed
it.

**P6.3 Invalid policy combination rejected** (e.g. `all_members` with
`race_type":"time"`) → 400.

---

## Phase 7 — Dashboard and WebSocket events

**P7.1 WebSocket connect.** Use `websocat ws://<hub-ip>:8000/ws/dashboard` or:
```python
python3 - <<'EOF'
import asyncio, websockets, json
async def main():
    async with websockets.connect("ws://<hub-ip>:8000/ws/dashboard") as ws:
        for _ in range(10):
            print(json.dumps(json.loads(await asyncio.wait_for(ws.recv(), 30)))[:200])
asyncio.run(main())
EOF
```
While a race runs, progress messages arrive continuously (~telemetry rate).

**P7.2 Event coverage.** Keep the WS listener open through one full P5.2-style
race and confirm these event types are all observed:
`race_countdown` (with `audio_url`, `duration_ms`, `play_sound`),
`state_change` (READY→RUNNING→STOPPED), progress updates, `race_event`
(finish events), `registration_success` (trigger a P4.4 registration while
listening).

**P7.3 Leaderboard display modes.** For each of the four modes:
```bash
curl -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"mode":"<classic|race_track|team_battle|sprint_board>"}' \
  $HUB/api/leaderboard/display
```
→ 200, WS listener receives the mode change, and an invalid mode → 400.

**P7.4 Start sound toggle.**
`POST /api/race/start-sound` with `{"enabled":false}` → next `race_countdown`
event has `"play_sound":false`. Restore to `true` afterwards.

**P7.5 Dashboard visual check.** [HUMAN-ASSIST or headless browser screenshot]
Open `$HUB/` on the venue screen (or capture with a headless browser) at each
stage: IDLE, READY, COUNTDOWN, RUNNING, RESULT. Verify: stage banner correct,
leaderboard shows athlete names/avatars/teams, countdown overlay appears, no
operator controls are visible anywhere on the Dashboard, Edge Node status and
QR codes render. In Team Battle mode verify Finished / All In markers.

**P7.6 Countdown audio.** [HUMAN-ASSIST]
With sound enabled and the screen's speaker on, start a race from Game Admin →
"3, 2, 1, Go" plays from the Dashboard device; race timer starts at Go.

**P7.7 Game Admin page.** [HUMAN-ASSIST or headless browser]
From `$HUB/gameAdmin`: select each race type, competition mode, team policies,
leaderboard mode, sound setting; press Start Race → Start button locks during
countdown and shows countdown status. Confirm Game Admin exposes no station
assignment or power controls.

**P7.8 System Admin page.** [HUMAN-ASSIST or headless browser]
From `$HUB/systemAdmin`: node online/offline list matches P3.4, station
assign/unassign works, update status panel renders, power controls demand
confirmation.

---

## Phase 8 — Security and auth

Precondition: `FITRACE_ADMIN_TOKEN` is set on Hub and Edge. If it is not set,
record every P8 test as FAIL — production devices must not ship without it.

**P8.1 Hub admin endpoints reject missing/wrong token** (expect 401/403):
`POST /api/race/configure`, `POST /api/race/start`, `POST /api/race/stop`,
`POST /api/stations/assign`, `POST /api/leaderboard/display`,
`POST /api/updates/check`, `POST /api/system/power/restart-service` — each
called without `$AUTH` and with `X-FitRace-Admin-Token: wrong`.

**P8.2 Edge admin endpoints reject missing token**: `POST /api/config`,
`POST /api/antenna/command`, `GET /api/ble/scan`.

**P8.3 Athlete-facing endpoints stay open**: `POST /api/race/register`,
`GET /api/race/state`, `GET /health` work without a token.

**P8.4 Test-telemetry endpoint disabled in production.**
With `FITRACE_ENABLE_TEST_TELEMETRY` unset and `TESTING` unset on the Hub,
`POST /api/test/telemetry` → 404.

**P8.5 Power actions are dry-run by default.**
`curl $HUB/api/system/power/status` and `$EDGE/api/system/power/status` →
report whether real power commands are enabled
(`FITRACE_POWER_COMMANDS_ENABLED`). Record the value; real actions are only
tested in Phase 11.

---

## Phase 9 — Resilience and recovery

Requires SSH access to Hub and Edge. Restore everything before moving on.

**P9.1 Edge offline detection.**
`sudo systemctl stop fitracestudio-edge.service` on the Edge →
within the heartbeat timeout `GET $HUB/api/nodes` marks it offline and the
Dashboard/System Admin shows the warning. Restart the service → node returns
online without operator action.

**P9.2 Equipment BLE drop and auto-reconnect.** [HUMAN-ASSIST]
Power-cycle the FTMS machine mid-telemetry → Edge reconnects via the antenna
board without service restart; telemetry resumes; `GET $EDGE/api/monitor/events`
logs the disconnect/reconnect.

**P9.3 MQTT broker restart.**
`sudo systemctl restart mosquitto` on the Hub → both Edge publisher and Hub
subscriber reconnect automatically; telemetry resumes within ~30s; no service
crashes (`systemctl status` both).

**P9.4 Dashboard refresh mid-race.**
During a RUNNING race, reload the Dashboard browser → it reconnects to
`/ws/dashboard` and re-renders current race state (not IDLE).

**P9.5 Hub restart with a configured race.**
Configure a race to READY, `sudo systemctl restart fitracestudio-hub.service`,
then `GET /api/race/state`. Record the observed behavior. Losing in-memory
state is currently expected — the test verifies the Hub comes back healthy,
Edge reconnects, and past results in `race_results.jsonl` survive.

**P9.6 Wi-Fi drop on Edge.** [HUMAN-ASSIST]
Briefly cut the Edge's network (unplug antenna-side AP or `sudo ip link set
wlan0 down; sleep 20; sudo ip link set wlan0 up`) → Edge rejoins, MQTT
reconnects, telemetry resumes, Hub marks node online again.

**P9.7 Malformed MQTT payload does not crash the Hub.**
`mosquitto_pub -h <hub-ip> -t gym/telemetry/bogus -m 'not json'` and
`-m '{}'` → Hub service stays up, `GET /health` still 200.

---

## Phase 10 — OTA update flow (operator consent required)

Follow [CLOUD_UPDATE_TEST_PLAN.md](CLOUD_UPDATE_TEST_PLAN.md) for full detail;
this phase is the smoke pass.

**P10.1** `GET $HUB/api/updates/status` → current version, channel, auto-check
state.
**P10.2** `POST $HUB/api/updates/check` (with `$AUTH`) → manifest fetched (or a
clean "no update / no network" result — record which).
**P10.3** If an update artifact is staged for testing:
`POST /api/updates/download` → artifact downloaded and signature verified
(invalid signature must be rejected).
**P10.4** Only with operator consent: `POST /api/updates/install/hub` then
`POST /api/updates/apply/hub` → service restarts on the new version;
`GET /health` reports the new version; a race can still be configured and run
(rerun P5.2 as post-update smoke).

---

## Phase 11 — Power actions (destructive, run last)

Only with operator consent and physical access to recover the devices. Check
P8.5 first: if power commands are in dry-run, each call should return a dry-run
acknowledgment without acting — verify exactly that and stop here.

If `FITRACE_POWER_COMMANDS_ENABLED=1`:

**P11.1** `POST $HUB/api/system/power/restart-service` → hub service restarts,
health returns within 60s.
**P11.2** `POST $EDGE/api/system/power/restart-service` → edge service
restarts, telemetry resumes.
**P11.3** `POST $EDGE/api/system/power/reboot` → device reboots, rejoins
`fitRace26`, services autostart, node returns in `GET $HUB/api/nodes`.
**P11.4** `POST $HUB/api/system/power/reboot` → Hub reboots; full stack
(broker, hub service, dashboard) recovers unattended.
**P11.5** `shutdown` endpoints: verify only if a human is present to power the
hardware back on; otherwise SKIPPED.

---

## Final report template

```markdown
# Hardware Test Report — <date>
Hub version: … | Edge version: … | Antenna FW: … | Tester: …

| ID | Result | Notes |
| --- | --- | --- |
| P1.1 | PASS | |
| …  | | |

## Failures and anomalies
- <test id>: expected …, observed …, logs …

## Blocked / skipped
- <test id>: reason

## Verdict
READY FOR EVENT / NOT READY — blocking issues: …
```

Verdict rule: NOT READY if any of these fail — the P3 telemetry pipeline, the
P5.2 full race lifecycle, P7.2 event coverage, or any P8 auth test.
