"""Results layer: split computation (roxzone breakdown) and SQLite store."""

import pytest

from hub_server.domain.models import HyroxStage
from hub_server.usecases.hyrox_course_engine import SubjectState
from hub_server.usecases.hyrox_results_store import (
    HyroxResultsStore,
    build_athlete_result,
)

# A tiny 3-stage course for readable timing math: run_1, ski_erg, run_2.
ORDER = [HyroxStage.RUN_1, HyroxStage.SKI_ERG, HyroxStage.RUN_2]
TARGETS = {
    HyroxStage.RUN_1: ("distance_m", 1000.0),
    HyroxStage.SKI_ERG: ("distance_m", 1000.0),
    HyroxStage.RUN_2: ("distance_m", 1000.0),
}


def _finished_state():
    # run_1: start/arrive 0, end 100 (work 100, roxzone 0)
    # ski_erg: became-current 100, arrived 130 (30 roxzone walk), end 200 (work 70)
    # run_2: became-current 200, arrived 210 (10 roxzone), end 260 (work 50)
    s = SubjectState(subject_id="alex", current_stage=HyroxStage.FINISHED, status="finished")
    s.stage_start_ms = {HyroxStage.RUN_1: 0, HyroxStage.SKI_ERG: 100, HyroxStage.RUN_2: 200}
    s.stage_arrived_ms = {HyroxStage.RUN_1: 0, HyroxStage.SKI_ERG: 130, HyroxStage.RUN_2: 210}
    s.stage_end_ms = {HyroxStage.RUN_1: 100, HyroxStage.SKI_ERG: 200, HyroxStage.RUN_2: 260}
    s.stage_resource = {HyroxStage.RUN_1: "tm-1", HyroxStage.SKI_ERG: "ski-1", HyroxStage.RUN_2: "tm-1"}
    return s


def _result(state, token="TOK", subject_id="alex", name="Alex", **kw):
    return build_athlete_result(
        race_id="race-1", result_token=token, subject_id=subject_id, display_name=name,
        division="individual", members=[name], state=state, stage_order=ORDER,
        targets=TARGETS, progress_of=lambda stg: TARGETS[stg][1], **kw,
    )


def test_split_roxzone_breakdown_sums_to_total():
    r = _result(_finished_state())
    assert r.status == "finished"
    assert r.total_time_ms == 260               # 260 - 0
    # work: run 100+50=150, ski 70 -> workout 70; roxzone 0+30+10=40
    assert r.run_total_ms == 150
    assert r.workout_total_ms == 70
    assert r.roxzone_total_ms == 40
    assert r.run_total_ms + r.workout_total_ms + r.roxzone_total_ms == r.total_time_ms


def test_split_fields_per_stage():
    r = _result(_finished_state())
    ski = next(s for s in r.splits if s.stage == HyroxStage.SKI_ERG)
    assert ski.roxzone_before_ms == 30 and ski.work_ms == 70
    assert ski.split_ms == 100 and ski.cumulative_ms == 200
    assert ski.resource_id == "ski-1"


def test_run_1_has_no_roxzone():
    r = _result(_finished_state())
    run1 = r.splits[0]
    assert run1.roxzone_before_ms == 0 and run1.work_ms == 100


def test_split_member_tag_comes_from_stage_member():
    # Phase 10: per-leg attribution -- stage_member is recorded by the engine
    # and build_athlete_result just carries it through onto the split.
    s = _finished_state()
    s.stage_member = {
        HyroxStage.RUN_1: "TAG_1",
        HyroxStage.SKI_ERG: "TAG_1",
        HyroxStage.RUN_2: "TAG_2",
    }
    r = _result(s)
    by_stage = {sp.stage: sp.member_tag for sp in r.splits}
    assert by_stage[HyroxStage.RUN_1] == "TAG_1"
    assert by_stage[HyroxStage.SKI_ERG] == "TAG_1"
    assert by_stage[HyroxStage.RUN_2] == "TAG_2"


def test_split_member_tag_is_none_for_non_relay_or_unrecorded_stages():
    r = _result(_finished_state())  # individual, stage_member never populated
    assert all(sp.member_tag is None for sp in r.splits)


def test_dnf_stops_at_abandoned_stage():
    s = _finished_state()
    s.status = "abandoned"
    s.current_stage = HyroxStage.RUN_2
    del s.stage_end_ms[HyroxStage.RUN_2]        # abandoned before finishing run_2
    r = _result(s)
    assert r.status == "dnf"
    assert r.total_time_ms is None
    assert r.dnf_stage == HyroxStage.RUN_2
    assert [sp.stage for sp in r.splits] == [HyroxStage.RUN_1, HyroxStage.SKI_ERG]


def test_terminal_result_requires_an_explicit_start_timestamp():
    state = SubjectState(
        subject_id="alex",
        current_stage=HyroxStage.RUN_1,
        status="abandoned",
    )

    with pytest.raises(ValueError, match="start timestamp"):
        _result(state)


def test_missing_arrival_falls_back_to_prev_end():
    # Operator force-complete leaves no arrival: roxzone 0, work == split.
    s = _finished_state()
    del s.stage_arrived_ms[HyroxStage.SKI_ERG]
    r = _result(s)
    ski = next(sp for sp in r.splits if sp.stage == HyroxStage.SKI_ERG)
    assert ski.roxzone_before_ms == 0 and ski.work_ms == ski.split_ms == 100


def test_store_roundtrip_and_ranking(tmp_path):
    store = HyroxResultsStore(db_path=str(tmp_path / "t.db"))
    store.create_race("race-1", "hq", "competition", "hyrox_standard_2026", 0)

    fast = _result(_finished_state(), token="FAST", subject_id="alex", name="Alex")  # 260
    slow_state = _finished_state()
    slow_state.stage_end_ms[HyroxStage.RUN_2] = 400            # slower finish
    slow = _result(slow_state, token="SLOW", subject_id="bella", name="Bella")       # 400
    store.finalize_athlete(slow)
    store.finalize_athlete(fast)

    # Token lookup returns the athlete with splits.
    got = store.get_by_token("FAST")
    assert got.total_time_ms == 260 and len(got.splits) == 3

    # Ranks: faster total ranks first regardless of insert order.
    assert store.get_by_token("FAST").rank == 1
    assert store.get_by_token("SLOW").rank == 2

    race = store.get_race("race-1")
    assert [a.result_token for a in race.athletes] == ["FAST", "SLOW"]

    csv = store.export_csv("race-1")
    assert "Alex" in csv and "rank" in csv
    store.close()


def test_store_unknown_token_is_none(tmp_path):
    store = HyroxResultsStore(db_path=str(tmp_path / "t.db"))
    assert store.get_by_token("NOPE") is None
    store.close()


def test_record_diagnostic_and_get_diagnostics(tmp_path):
    # Phase 7: the durable audit log, independent of the in-memory last-20
    # lists the state APIs use.
    store = HyroxResultsStore(db_path=str(tmp_path / "t.db"))
    store.create_race("race-1", "hq", "competition", "hyrox_standard_2026", 0)
    store.record_diagnostic("race-1", "conflict", "treadmill-01", "occupied", 10)
    store.record_diagnostic("race-1", "anonymous_no_binding", "treadmill-02", "no binding", 20)
    store.record_diagnostic("race-2", "conflict", "lane-1", "wrong race", 5)

    events = store.get_diagnostics("race-1")

    assert len(events) == 2
    assert events[0]["kind"] == "conflict" and events[0]["timestamp_epoch_ms"] == 10
    assert events[1]["kind"] == "anonymous_no_binding"
    assert store.get_diagnostics("unknown-race") == []
    store.close()


def test_penalty_sums_into_total_ms_for_finished_athlete():
    from hub_server.usecases.hyrox_course_engine import Penalty

    state = _finished_state()
    state.penalties = [
        Penalty(penalty_ms=10_000, reason="warning ignored", issued_at_epoch_ms=150),
        Penalty(penalty_ms=5_000, reason="second breach", issued_at_epoch_ms=180),
    ]
    r = _result(state)
    assert r.status == "finished"
    assert r.total_time_ms == 260 + 15_000
    assert [p.penalty_ms for p in r.penalties] == [10_000, 5_000]


def test_disqualified_state_finalizes_as_dq_with_reason():
    s = _finished_state()
    s.status = "disqualified"
    s.dq_reason = "left station before work complete"
    s.current_stage = HyroxStage.SKI_ERG
    del s.stage_end_ms[HyroxStage.SKI_ERG]
    del s.stage_end_ms[HyroxStage.RUN_2]

    r = _result(s)

    assert r.status == "dq"
    assert r.dq_reason == "left station before work complete"
    assert r.total_time_ms is None
    assert r.dnf_stage == HyroxStage.SKI_ERG


def test_penalty_does_not_affect_dnf_or_dq_total(tmp_path):
    from hub_server.usecases.hyrox_course_engine import Penalty

    s = _finished_state()
    s.status = "abandoned"
    s.current_stage = HyroxStage.RUN_2
    del s.stage_end_ms[HyroxStage.RUN_2]
    s.penalties = [Penalty(penalty_ms=1_000, reason="x", issued_at_epoch_ms=5)]

    r = _result(s)
    assert r.status == "dnf"
    assert r.total_time_ms is None
    assert [p.penalty_ms for p in r.penalties] == [1_000]  # still itemized


def test_store_persists_penalties_and_dq_reason_and_ranks_by_penalized_total(tmp_path):
    from hub_server.usecases.hyrox_course_engine import Penalty

    store = HyroxResultsStore(db_path=str(tmp_path / "t.db"))
    store.create_race("race-1", "hq", "competition", "hyrox_standard_2026", 0)

    fast_state = _finished_state()  # total 260
    fast_state.penalties = [Penalty(penalty_ms=200, reason="late gate", issued_at_epoch_ms=10)]
    fast = _result(fast_state, token="FAST", subject_id="alex", name="Alex")  # 260 + 200 = 460

    slow_state = _finished_state()
    slow_state.stage_end_ms[HyroxStage.RUN_2] = 400  # total 400, no penalty
    slow = _result(slow_state, token="SLOW", subject_id="bella", name="Bella")

    store.finalize_athlete(fast)
    store.finalize_athlete(slow)

    got_fast = store.get_by_token("FAST")
    assert got_fast.total_time_ms == 460
    assert len(got_fast.penalties) == 1
    assert got_fast.penalties[0].penalty_ms == 200
    assert got_fast.penalties[0].reason == "late gate"

    # Slow athlete (400, unpenalized) now ranks ahead of the penalized fast one (460).
    assert store.get_by_token("SLOW").rank == 1
    assert store.get_by_token("FAST").rank == 2
    store.close()


def test_store_delete_athlete_removes_row_splits_penalties_and_recomputes_ranks(tmp_path):
    from hub_server.usecases.hyrox_course_engine import Penalty

    store = HyroxResultsStore(db_path=str(tmp_path / "t.db"))
    store.create_race("race-1", "hq", "competition", "hyrox_standard_2026", 0)

    fast_state = _finished_state()
    fast_state.penalties = [Penalty(penalty_ms=100, reason="x", issued_at_epoch_ms=1)]
    fast = _result(fast_state, token="FAST", subject_id="alex", name="Alex")

    slow_state = _finished_state()
    slow_state.stage_end_ms[HyroxStage.RUN_2] = 400
    slow = _result(slow_state, token="SLOW", subject_id="bella", name="Bella")

    store.finalize_athlete(fast)
    store.finalize_athlete(slow)
    assert store.get_by_token("FAST").rank == 1

    assert store.delete_athlete("FAST") is True
    assert store.get_by_token("FAST") is None
    race = store.get_race("race-1")
    assert [a.result_token for a in race.athletes] == ["SLOW"]
    assert store.get_by_token("SLOW").rank == 1  # recomputed after the deletion

    assert store.delete_athlete("NOPE") is False
    store.close()


def test_migration_adds_dq_reason_column_to_pre_phase8_db(tmp_path):
    # Simulate a production DB created before Phase 8: build the schema by
    # hand without the dq_reason column, then open it through the store.
    import sqlite3

    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE athlete_results (
            result_token TEXT PRIMARY KEY, race_id TEXT NOT NULL, subject_id TEXT NOT NULL,
            display_name TEXT NOT NULL, division TEXT NOT NULL, members TEXT NOT NULL,
            status TEXT NOT NULL, started_at_ms INTEGER NOT NULL, finished_at_ms INTEGER,
            total_time_ms INTEGER, run_total_ms INTEGER NOT NULL, workout_total_ms INTEGER NOT NULL,
            roxzone_total_ms INTEGER NOT NULL, dnf_stage TEXT, rank INTEGER,
            UNIQUE (race_id, subject_id)
        );
    """)
    conn.commit()
    conn.close()

    store = HyroxResultsStore(db_path=db_path)  # opening must migrate in place
    cols = {row[1] for row in store._conn.execute("PRAGMA table_info(athlete_results)")}
    assert "dq_reason" in cols
    tables = {row[0] for row in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "penalties" in tables

    # And the store is now fully usable, including the new columns.
    store.create_race("race-1", "hq", "competition", "hyrox_standard_2026", 0)
    r = _result(_finished_state(), token="A", subject_id="alex")
    store.finalize_athlete(r)
    assert store.get_by_token("A").dq_reason is None
    store.close()


def test_migration_adds_member_tag_column_to_pre_phase10_stage_splits(tmp_path):
    # Simulate a production DB created before Phase 10: stage_splits without
    # the member_tag column (dq_reason/penalties already present, as any
    # post-Phase-8 DB would have).
    import sqlite3

    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE athlete_results (
            result_token TEXT PRIMARY KEY, race_id TEXT NOT NULL, subject_id TEXT NOT NULL,
            display_name TEXT NOT NULL, division TEXT NOT NULL, members TEXT NOT NULL,
            status TEXT NOT NULL, started_at_ms INTEGER NOT NULL, finished_at_ms INTEGER,
            total_time_ms INTEGER, run_total_ms INTEGER NOT NULL, workout_total_ms INTEGER NOT NULL,
            roxzone_total_ms INTEGER NOT NULL, dnf_stage TEXT, rank INTEGER, dq_reason TEXT,
            UNIQUE (race_id, subject_id)
        );
        CREATE TABLE stage_splits (
            result_token TEXT NOT NULL, seq INTEGER NOT NULL, stage TEXT NOT NULL,
            resource_id TEXT, arrived_ms INTEGER, ended_ms INTEGER, split_ms INTEGER NOT NULL,
            work_ms INTEGER NOT NULL, roxzone_before_ms INTEGER NOT NULL,
            cumulative_ms INTEGER NOT NULL, value REAL, target REAL,
            PRIMARY KEY (result_token, seq)
        );
    """)
    conn.commit()
    conn.close()

    store = HyroxResultsStore(db_path=db_path)  # opening must migrate in place
    cols = {row[1] for row in store._conn.execute("PRAGMA table_info(stage_splits)")}
    assert "member_tag" in cols

    # The store is now fully usable, including relay per-leg member_tag.
    store.create_race("race-1", "hq", "competition", "hyrox_standard_2026", 0)
    s = _finished_state()
    s.stage_member = {HyroxStage.RUN_1: "TAG_1"}
    r = _result(s, token="A", subject_id="alex")
    store.finalize_athlete(r)
    got = store.get_by_token("A")
    run1 = next(sp for sp in got.splits if sp.stage == HyroxStage.RUN_1)
    assert run1.member_tag == "TAG_1"
    store.close()


def test_list_races_newest_first_with_finalized_count(tmp_path):
    store = HyroxResultsStore(db_path=str(tmp_path / "t.db"))
    store.create_race("race-early", "hq", "training", "hyrox_standard_2026", 100)
    store.create_race("race-late", "hq", "competition", "hyrox_standard_2026", 200)
    # race-early has no finalized athletes; race-late has one.
    late_result = _result(_finished_state(), token="B", subject_id="bella")
    late_result = late_result.model_copy(update={"race_id": "race-late"})
    store.finalize_athlete(late_result)

    races = store.list_races()

    race_ids = [r["race_id"] for r in races]
    assert race_ids[0] == "race-late"  # newest (highest started_at_ms) first
    by_id = {r["race_id"]: r for r in races}
    assert by_id["race-late"]["finalized_count"] == 1
    assert by_id["race-early"]["finalized_count"] == 0
    store.close()
