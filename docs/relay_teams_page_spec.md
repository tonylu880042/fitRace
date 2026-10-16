# Relay Teams page (`/teams`) — spec (2026-10-16)

## Why
Relay events (8 treadmills, 3 km relay, possibly a series of races) need the
operator to type teams once, keep them, and pick which team runs on which
station for each race — then start the race from the same page. Today the
only paths are CSV roster import or a per-station textarea in Game Admin that
is lost after every Reset, and Game Admin is already crowded.

## Scope
1. Persistent relay team library (name + ordered members) on the hub.
2. New page `/teams`: edit the library, assign library teams to stations,
   start / stop / next race.
3. Remove the per-station relay textarea form from Game Admin (show the
   assigned team read-only + link to `/teams`).
4. Update the CLAUDE.md page-boundary section.

Out of scope (do NOT build): "already raced" badges, CSV→library import,
individual (non-relay) mode on this page, drag-and-drop, race settings
editing on this page.

## 1. Use case — `hub_server/usecases/relay_teams.py` (pure, no FastAPI)
`RelayTeamLibrary(store)` — `store` is any object with `load()/save()`
(same contract as `RaceSettingsStore`). Persists `{"teams": [...]}`; each
team `{"id": <uuid hex>, "name": str, "members": [str, ...]}`, list order =
creation order.
- `teams() -> list[dict]` (copies)
- `add(name, members) -> dict`, `update(team_id, name, members) -> dict`,
  `delete(team_id) -> None`
- Validation (raise `ValueError` with a stable message): name stripped,
  1..80 chars; name unique case-insensitively (update may keep its own
  name); members: strip each, drop blanks, 1..10 remaining, each ≤80 chars.
  Unknown id → `ValueError("team not found")`.

Pure function in the same module:
`plan_assignments(teams, assignments, station_numbers, relay_legs) -> list[tuple[int, dict]]`
- `assignments`: `{station_number(int): team_id or None}`; None/missing =
  station left empty.
- Raises `ValueError` if: a station is not in `station_numbers` (stations
  with a node assigned), a team id is unknown, the same team id appears on
  two stations, or a chosen team's member count != `relay_legs` (message
  names the team).
- Returns `[(station_number, team), ...]` sorted by station.
- Validate everything before returning; no partial result.

## 2. Hub wiring — `hub_server/infrastructure/fastapi/app.py`
- `resolve_data_paths()` gains `"relay_teams"`:
  `FITRACE_RELAY_TEAMS_PATH`, default `data/relay_teams.json`. Add it to the
  `build_backup_hook([...])` list so backups include it.
- `relay_team_library = RelayTeamLibrary(RaceSettingsStore(path))`.
- All endpoints below call `require_admin(request)`. `ValueError` → 400
  unless stated.
  - `GET /api/relay-teams` → `{"teams": [...]}`
  - `POST /api/relay-teams` body `{name, members}` → the team
  - `PUT /api/relay-teams/{team_id}` → the team (404 for "team not found")
  - `DELETE /api/relay-teams/{team_id}` → `{"teams": [...]}` (404 unknown)
  - `POST /api/relay-teams/assign` body `{"assignments": {"1": "<id>"|null, ...}}`
    - 409 if race state is not IDLE or READY (message tells the operator to
      press Next Race / stop first).
    - 409 if `race_manager.get_config()` is None or not
      `competition_mode == "relay"` ("save relay race settings in Game Admin
      first").
    - Run `plan_assignments` BEFORE touching any race state; a failure must
      leave registrations and state exactly as they were.
    - If state is IDLE (config present after a hub restart), call
      `apply_race_config(config)` first so the race ends READY — same reason
      as the comment in `load_next_heat`.
    - `race_manager.clear_station_registrations()`, then
      `register_athlete(station, None, team_name=team["name"], division=None,
      relay_members=team["members"])` for each planned pair.
    - `await broadcast_race_state()`; return `get_stations_status_data()`.
  - `POST /api/race/next-race`
    - 409 if RUNNING. Capture `config = race_manager.get_config()`; 409 if
      None. `reset_race_state()` then `apply_race_config(config)`; broadcast;
      return race state.
    - TRAP: plain `POST /api/race/reset` sets config to None
      (`RaceManager.reset_race`), so a reset-then-assign would fail. That is
      why this endpoint exists; it must keep the config and leave state READY
      with no registrations.
- Start / Stop reuse the existing `POST /api/race/countdown-start` and
  `POST /api/race/stop` — no new start endpoint.
- Route `GET /teams` → `RedirectResponse("/static/teams.html")`, same as
  `/gameAdmin`.

## 3. Page — `hub_server/static/teams.html`
- i18n: fetch via `/api/locales` + `/api/locales/{locale}` like
  `classAdmin.html`; add keys under a `teams.` prefix to every locale JSON in
  `hub_server/infrastructure/locales/` (real zh-TW and en-US text; satisfy
  whatever parity `tests/unit/hub/test_i18n_locales.py` /
  `test_static_page_i18n.py` require). No hardcoded zh/en strings in the
  page.
- Admin auth: same localStorage keys and `adminHeaders` pattern as
  gameAdmin.html (`fitrace.adminToken` / `fitrace.adminPassword`).
- Layout (works at tablet width):
  - **Team library**: list with edit/delete; add/edit form = team name +
    members textarea (one per line) + live "N / relay_legs members" counter.
  - **Station assignment**: one row per assigned station (from
    `/api/stations`) with a `<select>` of library teams + an empty option.
    A team chosen in one select is not offered in the others. "Apply"
    button posts `/api/relay-teams/assign` with the full mapping. Show the
    currently registered team per station from station status.
  - **Race control bar**: state badge; readiness from `/api/race/readiness`;
    Start (countdown-start) enabled only when state is READY and every
    assigned station has a registered team; Stop enabled only when RUNNING;
    Next Race enabled only when STOPPED.
  - While RUNNING: library editing and assignment are disabled.
- Keep state fresh the same way gameAdmin.html does (WebSocket and/or poll —
  reuse its pattern, don't invent a new one).
- Put decision logic in small pure functions (e.g. `availableTeamsFor(
  stationNumber, selections, teams)`, `controlState(raceState, stations)`)
  so tests can run them under `node` like the existing
  `tests/unit/hub/test_game_admin_*` tests.

## 4. Game Admin
- Remove the per-station relay registration form
  (`buildRelayRegistrationFormHtml`, `registerRelayTeam`, its CSS and i18n
  keys if unused elsewhere). For a relay race the station card shows the
  registered team name + members read-only and a link to `/teams`.
- Backend `/api/race/register` with `relay_members` stays (roster next-heat
  and tests use it).
- Update/remove only the page-JS tests that covered the removed form; keep
  every backend relay test.

## 5. CLAUDE.md
In "Page responsibility boundaries" add:
`- Teams /teams: relay team library, team→station assignment, and
Start/Stop/Next Race for relay races. Race rules and projector settings stay
in Game Admin.`
and change the Game Admin line so Start/Stop/Reset is shared with /teams.
