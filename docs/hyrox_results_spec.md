# Hyrox Results & Retrieval Specification

Defines per-station split capture, persisted race results, and per-athlete
result retrieval for the resource-aware Hyrox system. Complements
`hyrox_system_architecture_plan.md`; the persistence part is the concrete shape
of that plan's Phase 7.

## 1. Goal

Today the backend times every stage in memory but nothing is persisted, exposed
as a clean result, or downloadable. This spec adds:

1. Per-station splits, including a Roxzone (transition) breakdown.
2. Persisted, finalized results per athlete/team and per race.
3. A per-athlete retrieval link handed out at signup, usable after the race.
4. Operator-facing full-results export.

## 2. What Already Exists

`SubjectState` already records per stage:

- `stage_start_ms[stage]` — when the athlete *became* this stage (set on the
  previous stage's completion; for `run_1` it is the athlete's first activity).
- `stage_end_ms[stage]` — when this stage completed.

So a per-stage split (`end - start`) and the total time are already derivable.
Two things are missing to match official reporting and to survive a restart:

- an **arrival** timestamp per station (to separate Roxzone from work), and
- **persistence** plus a **results API**.

## 3. Roxzone Model

Official Hyrox runs one continuous clock: the total time **includes** Roxzone
(the transition time between the run track and the workout stations). Roxzone is
never subtracted — but official results **report it as a separate line** next to
the running and workout totals.

Our model can match this because every workout unit has an entry-gate reader and
every lane's first crossing marks arrival. Capture an **arrival** time per
stage and split each stage into transition + work:

```
arrived_ms[X]        = first activity at station X (entry-gate tap, or first
                       lane crossing; for run_1 it equals the athlete's start)
roxzone_before_ms[X] = arrived_ms[X] - stage_end_ms[X-1]   (0 for run_1)
work_ms[X]           = stage_end_ms[X] - arrived_ms[X]
stage_split_ms[X]    = stage_end_ms[X] - stage_end_ms[X-1]
                     = roxzone_before_ms[X] + work_ms[X]
```

Reported buckets (all summing to the total):

```
run_total_ms     = sum(work_ms[X] for run stages)
workout_total_ms = sum(work_ms[X] for workout stages)
roxzone_total_ms = sum(roxzone_before_ms[X] for all stages)
total_time_ms    = run_total + workout_total + roxzone_total
```

This requires one new piece of state: `arrived_ms` per (subject, stage),
stamped on the first attributed activity of each stage (the same hook that
already exists for `run_1` start — generalize it to every stage entry).

## 4. Data Model

```python
class HyroxStageSplit(BaseModel):
    stage: HyroxStage
    resource_id: str | None          # machine / lane used
    arrived_ms: int | None
    ended_ms: int | None
    split_ms: int                    # end - previous end (roxzone + work)
    work_ms: int
    roxzone_before_ms: int
    cumulative_ms: int               # from athlete start to this stage end
    value: float | None              # completed distance / lengths / reps
    target: float | None


class HyroxAthleteResult(BaseModel):
    subject_id: str
    display_name: str
    division: Literal["individual", "doubles", "relay"]
    members: list[str]
    status: Literal["finished", "dnf"]
    started_at_ms: int
    finished_at_ms: int | None       # None for dnf
    total_time_ms: int | None
    run_total_ms: int
    workout_total_ms: int
    roxzone_total_ms: int
    dnf_stage: HyroxStage | None      # where a dnf abandoned
    rank: int | None                  # finishers by total_time; dnf unranked
    splits: list[HyroxStageSplit]
    result_token: str                 # unguessable, issued at registration


class HyroxRaceResults(BaseModel):
    race_id: str
    venue_id: str
    mode: Literal["training", "competition"]
    course_profile_id: str
    started_at_ms: int | None
    finalized_at_ms: int | None
    athletes: list[HyroxAthleteResult]
```

## 5. Persistence (Phase 7)

Storage is **SQLite** via the Python stdlib `sqlite3` — no new dependency, no
ORM, one embedded file. This suits a single-node edge hub with tiny data volume
and a single writer, while giving indexed token lookups, ranking queries, cross-
race history, and atomic transactions that hand-rolled files do not. A networked
DB (Postgres/MySQL) is only warranted if multiple hubs ever write a shared store.

- One database file: `data/hyrox.db`. Open in **WAL mode**
  (`PRAGMA journal_mode=WAL`, `synchronous=NORMAL`) so a single writer and many
  readers run concurrently and survive power loss on edge storage.
- A race row is created when a venue is configured / the race starts, keyed by
  `race_id`.
- An athlete's result is **finalized the moment they finish or are marked DNF**:
  one transaction inserts the `athlete_results` row and its `stage_splits`.
  Written once, immutable — competition-grade audit. In-memory state stays the
  live source; the DB is the durable record.
- Before `configure_venue` resets for a new race, the current race is finalized
  and flushed so results are never lost to a reset.
- On Hub restart mid-race, finalized results survive in the DB; in-flight
  athletes are recovered or explicitly abandoned per the architecture plan's
  Phase 7 recovery flow.
- Each race also writes a CSV/JSON export (Section 7) as a second, offline
  backup — cheap insurance against SD-card corruption on the edge box.

### Schema

```sql
CREATE TABLE races (
    race_id           TEXT PRIMARY KEY,
    venue_id          TEXT NOT NULL,
    mode              TEXT NOT NULL,           -- training | competition
    course_profile_id TEXT NOT NULL,
    started_at_ms     INTEGER,
    finalized_at_ms   INTEGER
);

CREATE TABLE athlete_results (
    result_token     TEXT PRIMARY KEY,         -- unguessable retrieval key
    race_id          TEXT NOT NULL REFERENCES races(race_id),
    subject_id       TEXT NOT NULL,
    display_name     TEXT NOT NULL,
    division         TEXT NOT NULL,            -- individual | doubles | relay
    members          TEXT NOT NULL,            -- JSON array of member names
    status           TEXT NOT NULL,            -- finished | dnf
    started_at_ms    INTEGER NOT NULL,
    finished_at_ms   INTEGER,
    total_time_ms    INTEGER,
    run_total_ms     INTEGER NOT NULL,
    workout_total_ms INTEGER NOT NULL,
    roxzone_total_ms INTEGER NOT NULL,
    dnf_stage        TEXT,
    rank             INTEGER,
    UNIQUE (race_id, subject_id)
);
CREATE INDEX idx_results_race ON athlete_results(race_id);

CREATE TABLE stage_splits (
    result_token      TEXT NOT NULL REFERENCES athlete_results(result_token),
    seq               INTEGER NOT NULL,        -- stage order 0..15
    stage             TEXT NOT NULL,
    resource_id       TEXT,
    arrived_ms        INTEGER,
    ended_ms          INTEGER,
    split_ms          INTEGER NOT NULL,
    work_ms           INTEGER NOT NULL,
    roxzone_before_ms INTEGER NOT NULL,
    cumulative_ms     INTEGER NOT NULL,
    value             REAL,
    target            REAL,
    PRIMARY KEY (result_token, seq)
);
```

`rank` is recomputed over `athlete_results` for a race whenever a finisher is
added (an ordered query by `total_time_ms`); it is a derived convenience column,
not a source of truth. Token lookup is the `athlete_results` primary-key index;
a full field is `WHERE race_id = ? ORDER BY rank`.

## 6. Retrieval Link (issued at signup)

- At registration the backend generates an unguessable `result_token` (≥16 url-
  safe chars) per athlete and returns `{ result_token, result_url }`.
- The signup success screen shows the link (and a QR code) for the athlete to
  save; after the race they open it to view and download their own result.
- A token (not a bib number) is used so that per-athlete splits are not
  enumerable by guessing. The live public leaderboard stays public; the
  detailed personal result with splits and download is gated by the token.

## 7. API & Pages

```
GET  /api/hyrox/result/{token}               # one athlete's result (public link)
GET  /hyrox/result/{token}                    # result page: splits + download
GET  /api/hyrox/results/{race_id}             # full field (admin)
GET  /api/hyrox/results/{race_id}/export.csv  # full-field CSV export (admin)
```

Download formats: personal result as CSV and JSON; full field as CSV. PDF is
optional and lower priority.

The result page shows: total time and rank, the run/workout/roxzone buckets, and
the 16-stage split table (stage, arrival, work, roxzone-before, cumulative,
resource used).

## 8. Decisions Taken (adjustable in review)

1. **Roxzone**: included in the total, reported as a separate bucket, derived
   from per-station arrival times. (Confirmed.)
2. **Visibility**: per-athlete token link for detailed splits + download; the
   live leaderboard stays public. (Recommended default.)
3. **Finalization timing**: write each athlete's result on finish/DNF, not only
   at race end. (Recommended default.)
4. **race_id**: operator-named at venue setup (e.g. `2026-hyrox-taipei-heat-3`),
   falling back to an auto id, so results are easy to find later. (Recommended
   default.)
5. **Storage**: SQLite via stdlib `sqlite3` (WAL mode), one `data/hyrox.db`
   file, no ORM — right-sized for a single-node edge hub. A networked DB is out
   of scope unless multiple hubs ever share a store. (Confirmed.)

## 9. Open / Future

- Team (doubles/relay) split attribution: whole-team splits vs per-leg member
  splits — the roster has member tags, so per-leg is possible later.
- Penalties / no-rep adjustments, if judged manually.
- Cross-race athlete history (a person across multiple heats/events).
