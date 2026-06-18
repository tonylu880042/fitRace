# FitRaceStudio Demo Video Plan

Updated: 2026-06-18

## Goal

Create short product demo videos that quickly communicate the FitRaceStudio experience to customers:

- athlete self-registration
- coach race operation through `gameAdmin`
- live leaderboard and race excitement
- technical device/station management through `systemAdmin`
- Edge Node/device status visibility

The main video should feel like a product showcase, not a slow tutorial.

## Main Video Strategy

The primary customer-facing video should show one complete race flow using the most intuitive race mode:

- Main mode: `Distance Challenge`
- Target length: 45-60 seconds
- Live race segment: keep the simulated competition under 20 seconds
- Use realistic simulated athletes, stations, teams, and telemetry
- Emphasize motion, leaderboard changes, and operational clarity

Do not fully demonstrate every race mode in the main video. Showing all modes end-to-end would make the video too long and dilute the message.

## Suggested Main Video Structure

1. Dashboard hook, 5 seconds
   - Show the live leaderboard and race-ready visual style.
   - Text overlay: "Live cardio racing for studios and events"

2. Athlete signup, 8 seconds
   - Show mobile registration with station context, name, team, avatar, and submit.
   - Text overlay: "Athletes register from their phones"

3. Coach race setup, 7 seconds
   - Show `gameAdmin`, select `Distance Challenge`, set target, start race.
   - Text overlay: "Coaches control the race from Game Admin"

4. Live race, 15-20 seconds
   - Show leaderboard movement, progress bars, ranking changes, race timer, and final state.
   - Text overlay: "Real-time ranking, progress, and results"

5. Race modes quick pass, 5 seconds
   - Quickly show the race type selector or short UI cuts for available modes.
   - Text overlay: "Multiple challenge formats"

6. System administration, 5 seconds
   - Show `systemAdmin` with Edge Nodes, Station Assignment, and system controls.
   - Text overlay: "Technical setup stays in System Admin"

## Race Mode Coverage

Main video:

- Fully demonstrate `Distance Challenge`.
- Briefly show that other modes exist.

Secondary short clips can cover individual modes later:

- `Time Challenge`: 15-20 seconds
- `Calories Challenge`: 15-20 seconds
- `Max Power Challenge`: 15-20 seconds
- `Watt-based Challenge`: only include after the UI and backend behavior are confirmed production-ready

## Explanatory Text in Video

Yes, explanatory text should be included, but it should be short and visual. The goal is to guide attention without turning the video into a slide deck.

Recommended rules:

- Use short overlays, 3-7 words when possible.
- Put text near the relevant UI area without covering key controls or leaderboard data.
- Use FitRaceStudio's visual language: dark panels, high-contrast white text, lime accent, restrained red/blue for role cues.
- Keep each overlay on screen long enough to read, usually 2-3 seconds.
- Avoid long paragraphs, feature lists, or tutorial-style instructions in the main video.
- Prefer action-oriented copy.

Good overlay examples:

- "Live race leaderboard"
- "Athletes register by station"
- "Coach starts the race"
- "Real-time progress tracking"
- "Station setup in System Admin"
- "Edge Nodes stay visible"
- "Multiple race formats"

## Suggested Video Package

Create a small set of focused clips:

- `01_overview.mp4`: 45-60 seconds, customer-facing overview
- `02_signup.mp4`: 20-30 seconds, athlete registration
- `03_game_admin_race.mp4`: 20-30 seconds, coach race setup and start
- `04_system_admin_nodes.mp4`: 20-30 seconds, Edge Nodes and Station Assignment
- `05_live_race.mp4`: under 20 seconds, high-energy leaderboard movement

## Demo Data Guidance

Use simulated but realistic data:

- 4-6 athletes
- station numbers 1-6
- team names or studio names
- several equipment streams mapped to stations
- telemetry that changes ranking during the race

The simulation should make the race feel alive:

- progress bars move visibly
- rankings change at least once
- one athlete pulls ahead near the end
- final result appears quickly

## Production Notes

Preferred automation approach:

- Use browser automation to open dashboard, signup, `gameAdmin`, and `systemAdmin`.
- Seed or simulate athletes, stations, and telemetry.
- Record browser viewport video or capture image frames and compose MP4.
- Save outputs under `output/videos/`.

Keep the first implementation pragmatic. A polished first pass can use scripted browser interactions, simulated telemetry, and simple text overlays.

