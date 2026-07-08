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
