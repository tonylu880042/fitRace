# FitRaceStudio Feature Overview

Updated: 2026-06-18

## What FitRaceStudio Does

FitRaceStudio is a real-time fitness race system for studios, gyms, events, and group training sessions. It turns connected cardio equipment into a live competition experience with athlete self-registration, role-based administration, station assignment, race control, device monitoring, and a large-screen leaderboard.

The system is designed for in-studio use. Coaches and event operators can run races from a dedicated Game Admin page, while technical staff manage Edge Nodes, station mappings, updates, and power controls from a separate System Admin page.

## Main User Benefits

- Run live cardio races in a studio or event space.
- Show athlete progress on a large screen in real time.
- Let athletes register from their phones by station.
- Assign equipment to numbered stations for clear on-site operation.
- Separate coach race controls from technical device administration.
- Support multiple equipment types in one race.
- Monitor Edge Node status and discovered telemetry streams from the Hub.
- Provide a local Edge setup screen for equipment discovery and signal checks.
- Switch interface language for international environments.

## Supported Race Experience

FitRaceStudio supports several competition formats:

- Distance challenge
- Time challenge
- Calories challenge
- Max power challenge
- Watt-based challenge

During a race, the dashboard shows the current race state, elapsed time, athlete ranking, speed, distance, and progress percentage.

## System Screens

### Live Race Dashboard

The dashboard is designed for a studio display or event screen. It highlights the active race, leaderboard positions, station numbers, athlete names, teams, equipment labels, and progress.

![Live Race Dashboard](output/pdf/assets/dashboard.png)

### Game Admin

The Game Admin page is for coaches and on-site operators. It focuses on race setup, start/stop/reset actions, and a read-only station status view so staff can confirm which stations are ready without changing technical mappings.

### System Admin

The System Admin page is for technical staff. It centralizes Edge Node status, telemetry stream discovery, station assignment, software update actions, and Hub/system power controls.

### Athlete Self-Registration

Athletes can register from a mobile-friendly page. They can choose or upload an avatar, enter their name, add an optional team name, and submit registration for the selected station. The signup page no longer contains race or system management controls.

![Athlete Self-Registration](output/pdf/assets/signup.png)

### Edge Node Setup

The Edge Node setup screen helps staff check local signal status and discover nearby fitness equipment before or during setup.

![Edge Node Setup](output/pdf/assets/edge-setup.png)

## Feature Summary

### Live Leaderboard

- Displays current ranking during the race.
- Shows athlete name, team, station number, equipment label, distance, speed, and progress.
- Highlights the leading participant.
- Updates the race state and timer on the main display.

### Game Admin Race Control

- Select race type.
- Set target distance, time, calories, or challenge duration.
- Start, stop, close, or reset a race.
- View station readiness without changing equipment mappings.
- Keep the coach workflow simple for event staff.

### System Admin Device and Station Management

- Assign connected devices to numbered stations.
- View which stations already have athletes registered.
- See unassigned equipment before the race starts.
- Keep physical equipment layout aligned with the screen.
- Monitor Edge Node online/offline state and discovered equipment streams.

### System Maintenance

- Check available software update status.
- Download and stage Hub updates.
- Restart the Hub service when required.
- Reboot or shut down the Hub/system with confirmation controls.

### Athlete Registration

- Register by station.
- Enter athlete name and optional team name.
- Use default avatar options or upload a custom avatar.
- Update the dashboard immediately after registration.

### Equipment and Edge Setup

- View local setup status for an Edge Node.
- Check Wi-Fi signal status.
- Scan for nearby FTMS-compatible fitness equipment.
- Review discovered devices during installation or troubleshooting.

### Multi-Language Interface

- The user interface supports multiple languages.
- Operators can switch the dashboard language from the screen settings.
- Registration screens are ready for international event use.

## Typical Event Flow

1. Technical staff open System Admin during installation or maintenance.
2. Edge Nodes and equipment streams are checked.
3. Equipment streams are assigned to numbered stations.
4. Athletes scan a station QR code or open the registration page.
5. Athletes enter their name, team, and avatar.
6. Coaches open Game Admin, select a race type, and set the target.
7. The race starts and the dashboard updates live.
8. The leaderboard shows ranking, progress, and final results.

## Ideal Use Cases

- Gym floor challenges
- Studio class competitions
- Brand activation events
- Corporate wellness races
- Multi-station cardio tournaments
- Coach-led performance sessions

## Current Product Status

The current application includes the main dashboard, registration flow, Game Admin race control, System Admin device and station management, Edge setup screen, race state display, multilingual interface, update controls, power controls, and live race presentation screens.

Some hardware-specific production features, such as final equipment board integration and fully automated over-the-air update rollout, are planned as part of the productization phase.
