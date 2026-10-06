# Game Admin — progressive disclosure spec (2026-10-07)

## Problem
`hub_server/static/gameAdmin.html` shows every setting at once. Fields that
don't apply to the current choice are only greyed out (`is-disabled` +
"Applies to team races only" note), challenge-mode sub-settings show while
challenge mode is off, and the "Live Presentation" block mixes display,
automation, data-boundary and session-mode controls. Operators can't tell
what matters for the race they are setting up.

Goal: a setting appears only when an earlier choice makes it relevant.
Hidden ≠ disabled — irrelevant fields must not render at all.

## Design

### One pure decision function, one pass
Add a pure function `disclosureState(input)` in the page script:

```
input  = { competitionMode, challengeModeOn, sessionMode, raceState, hasRoster }
output = { relayLegs, teamScoring, teamCompletion, challengeSettings,
           switchToRaceMode, rosterLists, teamRuleCard }   // all booleans = visible
```

Computed in ONE pass (CLAUDE.md "prefer one partition pass"): e.g. relay vs
team fields come from a single `competitionMode` switch, not two independent
predicates. Add `syncVisibility()` that reads the DOM/state, calls
`disclosureState`, and applies each result with the existing
`.field-collapsed` class (or the `hidden` attribute for non-`.field`
elements). Call it from every place that today calls
`syncCompetitionFields`, from `saveChallengeMode`/the challenge select's
change, and from the race-state / roster render paths (WebSocket + poll).

Remove the now-dead disable-for-irrelevance code in `syncCompetitionFields`
(relay-legs / team-scoring `is-disabled` + their "applies to … only" notes).
KEEP `completionFieldState`'s existing behaviour: when Team is selected and
the race type is time-based, Completion Rule stays visible-but-disabled with
its explanatory note — that is a real constraint, not irrelevance.

### Visibility rules
| ID | Element(s) | Visible iff |
|----|-----------|-------------|
| R1 | `#relay-legs-field` | competition = `relay` |
| R2 | `#team-scoring-field`, `#team-completion-field` | competition = `team` |
| R3 | `#challenge-duration`, `#challenge-min-result`, `#challenge-start-wait` fields + challenge note | challenge mode = on |
| R4 | `#btn-switch-to-race-mode` field (button + note) | `state.race.session_mode !== "race"` |
| R5 | Current Heat, Next Heat, Load Next Heat action bar, Pending, Done, Absent blocks | roster has ≥1 entry (use `state.roster` counts). Walk-in Registration and the import toolbar stay always visible. |
| R6 | Team Rule guidance card | competition ≠ `individual` |

Hiding a field must never reset its value: the race-config payload and the
challenge-mode payload must send exactly what they send today. Add a test that
toggles competition team → individual → team and asserts the team policy
values survive and the save payload is unchanged vs. before this change.

### R7 — collapse Race Rules while RUNNING
Wrap the Race Rules block body in a native `<details id="rules-details" open>`
with the existing title as `<summary>`. On the state *transition* into
RUNNING set `open = false`; on the transition out of RUNNING set
`open = true`. Only on transitions — a manual toggle by the operator during a
race must not be overridden by the next poll/WS tick. Test both transitions
and the "no override on repeated RUNNING tick" case.

### R8 — regroup
1. **Live Presentation** keeps: Leaderboard View, Start Sound, both QR
   toggles, Show Live Data While Idle.
2. New control-block **Auto Start (Challenge Mode)** — `#challenge-block`:
   the challenge select + its R3 sub-fields + note. Moved out of Live
   Presentation.
3. `#btn-switch-to-race-mode` field stays in the race panel (R4 governs it).
4. New native `<details id="race-advanced">` (closed by default) at the
   bottom of the Race Control panel's left column, summary "Advanced":
   Overall Standings Event (Start New Event) and the Local Preference
   Refresh select (remove the separate `#local-block`).
5. New native `<details id="roster-advanced">` (closed by default) at the
   bottom of the Roster & Heats panel, summary "Advanced": move
   `#btn-clear-results` and `#btn-clear-roster` out of `#roster-toolbar`
   into it. Their modals, ids and RUNNING-disable logic are unchanged.

Element ids that existing JS references must be kept.

### i18n
New strings (Auto Start title, Advanced summary, anything else added) follow
the page's existing `data-i18n` + translation-dict mechanism and every locale
file that existing tests require (see `tests/unit/hub/test_static_page_i18n.py`
and `hub_server/infrastructure/locales/`). No hardcoded zh/en text. Remove
translation keys that become unused (e.g. `text.relay_field_note`,
`text.team_field_note`) only if nothing else references them.

## Out of scope
- Dashboard `index.html`, Class Admin, System Admin, any backend Python.
- Filtering Leaderboard View options by competition mode (team_battle renders
  for individual races today; not a confirmed problem).

## Tests
Follow the existing pattern in `tests/unit/hub/test_game_admin_race_rule_guidance.py`:
extract `disclosureState` (comments stripped) and run it under `node -e`
asserting real return values for every rule row, including all three
competition modes. Markup/regroup tests must parse structure (element X is
inside `<details id=...>` / not inside `#roster-toolbar`), not grep for a
substring that a comment could satisfy. Existing tests that asserted the old
greyed-out behaviour or old toolbar placement may be updated — list each one
and why in the report.
