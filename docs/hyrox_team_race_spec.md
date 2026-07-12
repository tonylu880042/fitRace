# Hyrox Team Racing, DQ & Reinstate Specification

Status: approved design, not yet implemented.
Continues the phase numbering of `hyrox_system_architecture_plan.md` (Phases 8-11).

Decisions in this document were made against the official HYROX rulebooks
(Season 25/26): the Team Relay rulebook (Transition Zone exchange, four timing
chips per team), the Doubles rulebook (both partners run every 1 km together,
station work split freely, one partner on the equipment at a time), and the
Singles rulebook (DNF = athlete's own decision to stop; incomplete station =
disqualification; movement-standard breaches = warning then time penalty).

## 1. Scope

Four features, in implementation order (smallest risk first):

1. **Phase 8 — DQ, time penalties, reinstate.** Rule-aligned terminal states
   and the operator undo the architecture plan already mandates (Section 10).
2. **Phase 9 — Competition gate-claim.** Resolves Open Questions 1 and 5:
   entry-gate tap claims a free resource in competition mode.
3. **Phase 10 — Relay Transition-Zone exchange.**
4. **Phase 11 — Doubles full semantics** (two treadmills per team on runs,
   merged station progress).

## 2. Phase 8 - DQ, Time Penalties, Reinstate

### Rule basis

The official rules distinguish three outcomes we currently collapse into one:

- **DNF** — the athlete decides to stop or leaves the course. This is exactly
  today's `abandoned` status (athlete button / operator). No change.
- **DQ** — a rules violation called by a judge: leaving a station before the
  work is complete, repeated movement-standard breaches, equipment misuse.
  Judge-initiated, not athlete-initiated. We do not model this today.
- **Time penalty** — a non-terminal sanction (warning first, penalty on the
  second breach) added to the final time.

### Design

**Status.** `SubjectState.status` gains `"disqualified"`. Terminal exactly like
`abandoned`: freeze `current_stage`, release the open assignment(s), ignore all
further sensor events, finalize into the results store with status `dq` and a
required `dq_reason` string. `AssignmentCloseReason` gains `DISQUALIFIED`.

**Penalties.** Per-subject list of `{penalty_ms, reason, issued_at_epoch_ms}`.
Stored on the engine state, included in the snapshot, and summed into
`total_ms` at finalization. Persisted to a new `penalties` table keyed by
`result_token` so the result page can itemize them.

**Reinstate.** Operator correction for a mistaken DNF or DQ (button misfire,
overturned call). Not a rule concept — an audited timing-ops action:

1. Guard: `status in {abandoned, disqualified}`.
2. Action: `status = racing`; `current_stage` stays at the frozen stage;
   progress and stage timestamps are untouched (they were frozen, not
   cleared).
3. If the subject was already finalized: delete its `athlete_results` row (and
   splits/penalties), remove it from the in-memory finalized set, recompute
   ranks.
4. Resources are NOT auto-restored — the athlete re-claims via gate tap or the
   operator re-assigns. (By the time a mistake is noticed the equipment has
   usually moved on.)
5. Audit: a durable `reinstate` diagnostic with operator source, prior status,
   and prior terminal timestamp.

### API

```text
POST /api/hyrox/dq         {subject_id, reason}              # admin
POST /api/hyrox/penalty    {subject_id, penalty_ms, reason}  # admin
POST /api/hyrox/reinstate  {subject_id}                      # admin
```

## 3. Phase 9 - Competition Gate-Claim

Every athlete wears an RFID tag and every claimable unit already has (or can
have) an `entry_gate` reader, so the system can know who is on which unit
without an operator. Extend the dynamic-claim path (today training-only) to
competition mode with the same guards it already applies:

- the tag resolves to a registered, racing subject;
- the claimed group serves the subject's **current** stage (in-sequence);
- the resource is free.

Operator assignment remains available and always wins (an operator `assign`
can supersede a gate claim; the reverse is rejected with a diagnostic).
Out-of-sequence or occupied taps keep producing the existing diagnostics.
This closes Open Question 1 (no auto-scheduler — the athlete's own tap is the
scheduler) and Open Question 5 (wall-ball targets claim by entry gate, with
operator assignment as the fallback).

## 4. Phase 10 - Relay Transition-Zone Exchange

### Rule basis

Team Relay: 4 members, each responsible for 2 legs; a leg is `1 km run + 1
station`. The exchange happens in a dedicated Transition Zone: the finishing
member taps the next member, who may only start their run after the tap. Every
member has their own timing chip.

### Design

**Venue.** `HyroxVenueConfig` gains `exchange_zones: list[HyroxEndpointSensor]`
(top-level, like nothing else occupies them — validated against duplicate
addresses exactly as abandon endpoints are). New sensor class
`EXCHANGE_ZONE`; the registry indexes and normalizes it following the
abandon-zone pattern, and reads reaching `ingest_rfid` on an exchange address
route to a new `ingest_exchange` (same hub-side routing trick as abandon — no
edge firmware change).

**Team state.** The engine's `SubjectState` gains `active_member_tag`
(snapshot-persisted). Initially unset; the first member to produce activity
sets it. Roster validation: `relay` requires exactly 4 member tags, `doubles`
2, `individual` 1.

**Exchange transition.** On a TZ read of `tag_id`:

1. Resolve the tag to a team; reject with a diagnostic if unknown
   (`exchange_unknown_tag`) or not a member (`exchange_not_member`).
2. Guard: team `status == racing` (`exchange_terminal` otherwise) and
   `tag_id != active_member_tag` (`exchange_same_tag`).
3. Guard — leg boundary: the team's `current_stage` is a run that has not
   started yet (`stage_start_ms` absent and no open assignment). A tap
   mid-run or mid-station is rejected (`exchange_mid_stage`); the incoming
   member cannot take over in the middle of a leg.
4. Action: `active_member_tag = tag_id`; durable `exchange` audit diagnostic
   (team, from-tag, to-tag, stage, timestamp).

**Attribution tightening (relay only).** Course reads from a member tag that
is not the active member are rejected with a diagnostic
(`inactive_member_read`) instead of being attributed — the rulebook's "only
the tapped-in member races" enforced in software. Abandon-zone taps stay open
to ANY member tag: one member abandoning is a whole-team DNF (plan Section
10), and the interlock still requires membership of the assigned team.

**Per-leg attribution.** `stage_resource` gains a sibling map
`stage_member: dict[stage, tag]` recorded at stage start from
`active_member_tag`. `stage_splits` gains a nullable `member_tag` column so
the result page can show who ran which leg (closes the results-spec open item
on per-leg splits).

## 5. Phase 11 - Doubles Full Semantics

### Rule basis

Doubles: both partners run every 1 km segment together and enter/leave every
station together; station work is split freely with one partner on the
equipment at a time.

### Design

**Runs — two concurrent assignments.** The per-resource invariant (one open
assignment per resource) is untouched. The per-subject invariant is relaxed
for doubles subjects on run stages only: up to two open assignments, one per
member tag (`active_tag_id` distinguishes them). Each partner gate-taps their
own treadmill. Distance is tracked per `(subject, stage, member_tag)` — the
progress tracker's distance key gains the tag for doubles runs — and the run
stage completes only when **both** members reach the target. One partner
finishing releases their treadmill immediately; the stage (and Roxzone clock)
waits for the other.

**Stations — merged progress.** One resource, one assignment, exactly as
today. Either member's tag may claim/bind the station, and rep/length events
attribute to the team regardless of which partner is working (the tracker
already keys on subject; no change). The "enter and exit together" and "fair
share of work" rules are judging concerns, out of software scope.

**Abandon/DQ.** Either member's abandon tap DNFs the team (already the
model). A DQ or DNF releases **all** open assignments of the subject.

**Explicitly out of scope:** enforcing the 15-second togetherness rule on
runs, per-partner work-share accounting inside stations.

## 6. Snapshot & Migration Notes

- Snapshot version bumps to 2: adds `active_member_tag`, `stage_member`,
  penalties, doubles' multi-assignment lists, and the tag-keyed distance
  records. `restore()` must accept version 1 (all new fields defaulted).
- SQLite: `athlete_results.status` gains the `dq` value (TEXT column — no
  migration); new `penalties` table; `stage_splits.member_tag` added via
  `ALTER TABLE ... ADD COLUMN` guarded by a pragma check.
- `hyrox_venue_admin.html` template gains an `exchange_zones` example entry.
- Simulator: add a relay team (4 tags, TZ taps between legs) and a doubles
  pair (parallel treadmill runs) to the mock roster.

## 7. Test Strategy

- Phase 8: DQ freezes stage and finalizes as `dq`; penalty sums into
  `total_ms` and itemizes; reinstate un-finalizes, recomputes ranks, and is
  idempotent-guarded; reinstated athlete can resume via gate claim.
- Phase 9: competition gate tap claims free in-sequence resource; occupied and
  out-of-sequence taps rejected; operator assign supersedes a gate claim.
- Phase 10: TZ tap at leg boundary switches the active member; mid-stage,
  same-tag, non-member taps rejected with diagnostics; inactive-member course
  reads rejected; per-leg member recorded in splits; snapshot round-trips
  `active_member_tag`.
- Phase 11: two treadmills concurrently assigned to one doubles team; run
  completes only when both reach 1 km; station reps merge across partners;
  abandon by either partner releases both assignments; snapshot round-trips
  the dual-assignment state.
