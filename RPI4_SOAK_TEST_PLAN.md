# Raspberry Pi 4 Dashboard Soak Test Plan

Status: Pending hardware validation
Updated: 2026-06-19

## Purpose

This plan defines the long-running real-device validation for FitRaceStudio on a
Raspberry Pi 4 venue Dashboard. The goal is to confirm that the Dashboard remains
stable during realistic event operation, not only that individual features work
once.

This test should be executed when an RPi4, display, and kiosk deployment setup
are available.

## Test Scope

Validate the RPi4 running the Dashboard in Chromium kiosk mode with:

- Live WebSocket updates.
- Race lifecycle transitions.
- Team Battle, Race Track, Sprint Board, and Classic leaderboard views.
- Start countdown visual overlay.
- Start countdown audio.
- Repeated race reset and restart.
- Sustained telemetry updates.

## Test Environment

Required hardware:

- Raspberry Pi 4.
- Official or stable equivalent power supply.
- 1080p HDMI display.
- Audio output connected to the display, speaker, or venue sound system.
- Network path to the Central Hub.
- Optional: actual Edge Nodes and fitness equipment.

Recommended runtime:

- Raspberry Pi OS with Chromium kiosk mode.
- 1920x1080 output resolution.
- Dashboard URL opened full screen.
- Browser configured to allow autoplay for local Dashboard audio.
- Hub service running the same build intended for venue deployment.

Avoid using 4K output for this validation unless 4K is a real deployment
requirement. 1080p is the target venue configuration.

## Test Duration

Use one of these durations depending on readiness:

- Basic validation: 30 minutes.
- Release validation: 60 minutes.
- Pre-event stress validation: 2 to 4 hours.

Minimum release gate for the current product stage: 60 minutes.

## Test Data Conditions

Use either actual equipment telemetry or simulated telemetry.

Minimum simulated profile:

- 4 stations.
- 2 teams.
- 1 telemetry update per station per second.
- Distance race target: 1000m.
- Calories race target: representative studio challenge target.
- Team completion modes:
  - Aggregate Progress.
  - All Members Finish.

Extended profile:

- 8 stations.
- 4 teams.
- 1 to 2 telemetry updates per station per second.
- Repeated leaderboard mode switching between races.

## Test Procedure

### 1. Startup

1. Boot the RPi4.
2. Confirm Chromium opens the Dashboard in kiosk/fullscreen mode.
3. Confirm Dashboard WebSocket status becomes online.
4. Confirm Game Admin can be opened from another device.
5. Confirm Dashboard has no operator controls.

Pass criteria:

- Dashboard loads without manual browser intervention.
- WebSocket connects within 30 seconds.
- No visible browser chrome or OS dialogs block the screen.

### 2. Individual Race Cycle

1. Configure an individual distance race.
2. Select Classic leaderboard.
3. Start with countdown sound enabled.
4. Send telemetry until participants finish.
5. Stop or allow completion.
6. Reset.

Pass criteria:

- Countdown appears and audio plays.
- Timer starts on Go.
- Leaderboard updates smoothly.
- Result state appears after stop/completion.
- Reset returns Dashboard to a clean ready/idle state.

### 3. Team Race Cycle

1. Configure Team Race.
2. Select Team Battle.
3. Use All Members Finish.
4. Start with countdown sound enabled.
5. Send telemetry where one team has unfinished members.
6. Confirm Dashboard displays members still required to finish.
7. Complete all members.
8. Stop/reset.

Pass criteria:

- Team Battle cards render correctly.
- Member completion chips update.
- Team status shows unfinished member count.
- Finished/all-in status appears when members complete.
- No manual Dashboard refresh is required.

### 4. Leaderboard Mode Rotation

Run separate short races or reset between configurations:

1. Classic.
2. Race Track.
3. Team Battle.
4. Sprint Board.

Pass criteria:

- Each mode renders without blank states once telemetry arrives.
- Switching mode from Game Admin updates Dashboard.
- No Dashboard controls are needed.
- No layout overlap at 1080p.

### 5. Audio Behavior

1. Start a race with Start Sound enabled.
2. Reset.
3. Start a race with Silent Start.
4. Reset.
5. Start a race with Start Sound enabled again.

Pass criteria:

- Sound On plays `3, 2, 1, Go`.
- Silent Start shows visual countdown without audio.
- Sound can be re-enabled without browser refresh.

### 6. Long Run

Run repeated race cycles for the selected duration:

- Recommended cadence: one race every 5 to 8 minutes.
- Use at least one Team Battle race every 15 minutes.
- Leave Dashboard open continuously.
- Do not manually refresh the browser unless recording a failure.

Pass criteria:

- Dashboard does not freeze or white-screen.
- WebSocket reconnects automatically if briefly interrupted.
- Countdown remains synchronized with race start.
- Reset reliably prepares the next race.
- Operator can continue from Game Admin only.

## Monitoring Checklist

Record observations every 10 minutes:

- Current time.
- Race count completed.
- Dashboard state.
- WebSocket status.
- Audio success/failure.
- Visible frame drops or animation stutter.
- Chromium memory usage if available.
- CPU load if available.
- RPi temperature if available.
- Any manual intervention.

Suggested commands on RPi4:

```text
vcgencmd measure_temp
top
free -h
```

Optional Chromium process check:

```text
ps -eo pid,comm,%cpu,%mem --sort=-%mem | head
```

## Pass / Fail Criteria

Release validation passes when all are true:

- Runs for 60 continuous minutes.
- Dashboard does not freeze, white-screen, or require manual refresh.
- At least 6 race cycles complete.
- At least 2 team races complete.
- Countdown appears every time.
- Sound On plays audio every time it is enabled.
- Silent Start produces no start audio.
- Reset prepares the next race.
- No obvious long-term slowdown.
- RPi4 does not overheat or throttle enough to affect visible operation.

Fail the test if any of these occur:

- Browser crashes or exits kiosk mode.
- Dashboard becomes blank.
- Dashboard stops receiving live updates and does not recover.
- Race starts before visible countdown reaches Go.
- Sound setting does not match Game Admin selection.
- Reset leaves stale race data on Dashboard.
- Operator must manually refresh Dashboard to continue.

## Evidence To Collect

For each run, save:

- Test date and duration.
- Build version or git commit.
- RPi4 model and OS version.
- Display resolution.
- Browser launch command.
- Pass/fail result.
- Notes for any failure.
- Photos or short videos of failures when possible.

## Current Status

This test is not yet executed. It is blocked on access to the real RPi4 kiosk
environment.

Until then, use the development browser regression:

```text
node scripts/verify_dashboard_ux.mjs
```

That script validates Dashboard behavior in Chromium on the development machine,
but it does not replace this RPi4 soak test.
