# FitRaceStudio Feature Overview

Updated: 2026-06-19

## Product Summary

FitRaceStudio is a local-first fitness race system for studios, gyms, and event
operators. It receives real-time telemetry from connected cardio equipment,
scores individual or team races, and presents the competition on a large
Dashboard screen.

The system is organized around clear on-site responsibilities:

- Dashboard: display-only venue screen.
- Game Admin: coach and operator race control.
- System Admin: technical setup, station mapping, updates, and power actions.
- Signup: athlete self-registration by station.

## Dashboard

The Dashboard is designed for a screen that participants and spectators can see.
It must not contain settings, toggles, start/stop buttons, sound controls, or
leaderboard selectors.

The Dashboard displays:

- Race state and timer.
- Individual or team leaderboard.
- Athlete names, station numbers, team names, equipment labels, and avatars.
- Progress, speed, power, distance, calories, and finish time where available.
- QR code entry points for registration and Game Admin access.
- Edge Node status for quick venue awareness.
- Countdown overlay before the race starts.
- Race stage banner for IDLE, READY, COUNTDOWN, RUNNING, and final result states.
- Team Battle completion markers such as Finished, All In, and members still
  required to finish.

Dashboard behavior is driven by Central Hub state and WebSocket events. Operators
change behavior from Game Admin or System Admin, not from the Dashboard itself.

## Game Admin

Game Admin is the coach-operated race desk. It controls the live event without
exposing technical station assignment or system power controls.

Game Admin supports:

- Race type selection:
  - Distance Challenge
  - Calories Challenge
  - Time Challenge
  - Max Power Challenge
- Competition mode:
  - Individual Race
  - Team Race
- Team scoring:
  - Average Progress
  - Team Total
- Team completion:
  - Aggregate Progress
  - All Members Finish
- Leaderboard presentation:
  - Classic
  - Race Track
  - Team Battle
  - Sprint Board
- Start countdown sound:
  - Play 3, 2, 1, Go
  - Silent Start
- Operator Unlock for protected race actions when access protection is enabled.

The Start Race action uses the Dashboard countdown flow. Game Admin sends a
countdown-start command, the Dashboard shows the countdown, and the race timer
starts on Go. While the countdown is active, the Start button is locked and the
operator sees a visible countdown status.

## Team Race Rules

Team names are entered during athlete registration. A race becomes a team race
when Game Admin selects `Team Race`.

Team scoring defines how progress is ranked:

- Average Progress ranks teams by normalized average progress across members.
- Team Total ranks teams by accumulated team progress.

Team completion defines when a team is considered finished:

- Aggregate Progress lets the team finish when the team score reaches the target.
- All Members Finish requires every teammate to complete the target.

For distance and calories races, All Members Finish means each team member must
complete the full target distance or target calories. If one member has not
finished, the team is not considered finished even if other teammates have strong
scores.

## Leaderboard Modes

Classic is the default and remains the clearest result view. Additional modes
make live races more engaging without changing the scoring rules.

- Classic: standard ranking list for clear final results.
- Race Track: lane-style progress visualization, best for distance and calorie
  races.
- Team Battle: team cards that emphasize team-versus-team momentum and member
  completion count.
- Sprint Board: compact high-energy board for short races and quick challenges.

The selected mode is stored in Central Hub race state and broadcast to the
Dashboard. The Dashboard only renders the selected view.

## Start Audio And Countdown

The start sound defaults to on. Game Admin can choose whether the next start
plays the human voice `3, 2, 1, Go` cue.

The audio is played from the Dashboard because that is the venue display attached
to the main speaker. Game Admin does not play the start sound locally.

The countdown flow improves fairness because participants do not need to watch
the screen at the exact moment the operator presses Start Race. The timer begins
after the countdown reaches Go.

The Dashboard also reflects the countdown and race lifecycle visually. During
countdown it shows a dedicated event state, during the race it shows live timing
and target context, and after completion it locks into a result state.

## System Admin

System Admin is for technical staff and installation work.

System Admin supports:

- Edge Node online/offline monitoring.
- Discovered telemetry stream review.
- Station assignment and unassignment.
- Software update status, download, install, and apply actions.
- Hub/system power controls with confirmation.
- Maintenance Unlock for protected system actions when access protection is enabled.

These controls are intentionally kept out of Dashboard and Game Admin.

## Athlete Signup

Signup is the athlete-facing registration page.

Athletes can:

- Open a station-specific signup link or QR code.
- Enter athlete name.
- Enter optional team name.
- Select or upload an avatar.
- Submit registration for their station.

Uploaded avatars are compressed on the athlete's phone before submission. The
signup page rejects source photos larger than 8MB, crops to a square avatar,
encodes a small WebP image, and targets a 32KB upload budget. The Hub validates
the WebP payload and stores it, but does not perform image resizing or
compression.

Signup does not include race controls or system controls.

## Typical Event Flow

1. Technical staff open System Admin and confirm Edge Nodes are online.
2. Equipment streams are assigned to station numbers.
3. Athletes open the station signup page and register names, teams, and avatars.
4. The coach opens Game Admin.
5. The coach selects race type, competition mode, target, leaderboard view, and
   start sound setting.
6. The coach reviews station readiness and team rule guidance.
7. The coach presses Start Race.
8. Dashboard shows the countdown and plays 3, 2, 1, Go when enabled.
9. Race scoring starts on Go.
10. Dashboard presents the live leaderboard and final result.

## Current Product Status

The current version includes role-separated screens, live Dashboard rendering,
individual and team race scoring, multiple leaderboard visual modes, start
countdown audio, athlete signup, station assignment, Edge Node monitoring,
software update controls, and local-first race operation.

Planned productization work includes deeper hardware integration validation,
more event sound cues, final-result export, and production update rollout
automation.
