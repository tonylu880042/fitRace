"""Hyrox results: split computation and SQLite persistence.

See docs/hyrox_results_spec.md. Storage is stdlib sqlite3 in WAL mode -- one
embedded file, no ORM, sized for a single-node edge hub.
"""

import json
import sqlite3
from io import StringIO
from pathlib import Path
from typing import Callable, Optional

from hub_server.domain.models import HyroxStage
from hub_server.domain.hyrox_results import (
    HyroxAthleteResult,
    HyroxRaceResults,
    HyroxStageSplit,
)
from hub_server.usecases.hyrox_course_engine import SubjectState


def _is_run(stage: HyroxStage) -> bool:
    return stage.value.startswith("run_")


def build_athlete_result(
    *,
    race_id: str,
    result_token: str,
    subject_id: str,
    display_name: str,
    division: str,
    members: list[str],
    state: SubjectState,
    stage_order: list[HyroxStage],
    targets: dict[HyroxStage, tuple[str, float]],
    progress_of: Callable[[HyroxStage], float],
) -> HyroxAthleteResult:
    """Turn a finished/abandoned SubjectState into a finalized result with a
    per-station split and Roxzone breakdown (see spec section 3)."""
    race_start = state.stage_start_ms.get(HyroxStage.RUN_1)
    finished = state.status == "finished"
    splits: list[HyroxStageSplit] = []
    run_total = workout_total = roxzone_total = 0
    prev_end = race_start

    for seq, stage in enumerate(stage_order):
        ended = state.stage_end_ms.get(stage)
        if ended is None:
            break  # first incomplete stage: nothing beyond it is timed
        arrived = state.stage_arrived_ms.get(stage, prev_end)
        roxzone_before = max(0, arrived - prev_end)
        work = max(0, ended - arrived)
        split = ended - prev_end
        target_type, target_value = targets.get(stage, (None, None))
        splits.append(HyroxStageSplit(
            stage=stage, seq=seq,
            resource_id=state.stage_resource.get(stage),
            arrived_ms=arrived, ended_ms=ended,
            split_ms=split, work_ms=work, roxzone_before_ms=roxzone_before,
            cumulative_ms=ended - race_start,
            value=progress_of(stage), target=target_value,
        ))
        roxzone_total += roxzone_before
        if _is_run(stage):
            run_total += work
        else:
            workout_total += work
        prev_end = ended

    finished_at = prev_end if finished else None
    return HyroxAthleteResult(
        result_token=result_token, race_id=race_id, subject_id=subject_id,
        display_name=display_name, division=division, members=members,
        status="finished" if finished else "dnf",
        started_at_ms=race_start,
        finished_at_ms=finished_at,
        total_time_ms=(finished_at - race_start) if finished else None,
        run_total_ms=run_total, workout_total_ms=workout_total,
        roxzone_total_ms=roxzone_total,
        dnf_stage=None if finished else state.current_stage,
        splits=splits,
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS races (
    race_id TEXT PRIMARY KEY, venue_id TEXT NOT NULL, mode TEXT NOT NULL,
    course_profile_id TEXT NOT NULL, started_at_ms INTEGER, finalized_at_ms INTEGER
);
CREATE TABLE IF NOT EXISTS athlete_results (
    result_token TEXT PRIMARY KEY, race_id TEXT NOT NULL, subject_id TEXT NOT NULL,
    display_name TEXT NOT NULL, division TEXT NOT NULL, members TEXT NOT NULL,
    status TEXT NOT NULL, started_at_ms INTEGER NOT NULL, finished_at_ms INTEGER,
    total_time_ms INTEGER, run_total_ms INTEGER NOT NULL, workout_total_ms INTEGER NOT NULL,
    roxzone_total_ms INTEGER NOT NULL, dnf_stage TEXT, rank INTEGER,
    UNIQUE (race_id, subject_id)
);
CREATE INDEX IF NOT EXISTS idx_results_race ON athlete_results(race_id);
CREATE TABLE IF NOT EXISTS stage_splits (
    result_token TEXT NOT NULL, seq INTEGER NOT NULL, stage TEXT NOT NULL,
    resource_id TEXT, arrived_ms INTEGER, ended_ms INTEGER, split_ms INTEGER NOT NULL,
    work_ms INTEGER NOT NULL, roxzone_before_ms INTEGER NOT NULL, cumulative_ms INTEGER NOT NULL,
    value REAL, target REAL, PRIMARY KEY (result_token, seq)
);
"""


class HyroxResultsStore:
    def __init__(self, db_path: str = "data/hyrox.db"):
        parent = Path(db_path).parent
        if parent and str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def create_race(self, race_id, venue_id, mode, course_profile_id, started_at_ms):
        self._conn.execute(
            "INSERT OR IGNORE INTO races "
            "(race_id, venue_id, mode, course_profile_id, started_at_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (race_id, venue_id, mode, course_profile_id, started_at_ms),
        )
        self._conn.commit()

    def finalize_athlete(self, result: HyroxAthleteResult):
        """Insert one athlete's result and splits in a single transaction, then
        recompute finisher ranks for the race."""
        r = result
        with self._conn:  # transaction
            self._conn.execute(
                "INSERT OR REPLACE INTO athlete_results "
                "(result_token, race_id, subject_id, display_name, division, members, "
                " status, started_at_ms, finished_at_ms, total_time_ms, run_total_ms, "
                " workout_total_ms, roxzone_total_ms, dnf_stage, rank) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r.result_token, r.race_id, r.subject_id, r.display_name, r.division,
                 json.dumps(r.members, ensure_ascii=False), r.status, r.started_at_ms,
                 r.finished_at_ms, r.total_time_ms, r.run_total_ms, r.workout_total_ms,
                 r.roxzone_total_ms, r.dnf_stage.value if r.dnf_stage else None, None),
            )
            self._conn.execute("DELETE FROM stage_splits WHERE result_token = ?",
                               (r.result_token,))
            self._conn.executemany(
                "INSERT INTO stage_splits "
                "(result_token, seq, stage, resource_id, arrived_ms, ended_ms, split_ms, "
                " work_ms, roxzone_before_ms, cumulative_ms, value, target) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(r.result_token, s.seq, s.stage.value, s.resource_id, s.arrived_ms,
                  s.ended_ms, s.split_ms, s.work_ms, s.roxzone_before_ms, s.cumulative_ms,
                  s.value, s.target) for s in r.splits],
            )
            self._recompute_ranks(r.race_id)

    def _recompute_ranks(self, race_id: str):
        rows = self._conn.execute(
            "SELECT result_token FROM athlete_results "
            "WHERE race_id = ? AND status = 'finished' AND total_time_ms IS NOT NULL "
            "ORDER BY total_time_ms ASC",
            (race_id,),
        ).fetchall()
        for i, row in enumerate(rows, start=1):
            self._conn.execute("UPDATE athlete_results SET rank = ? WHERE result_token = ?",
                               (i, row["result_token"]))

    def get_by_token(self, token: str) -> Optional[HyroxAthleteResult]:
        row = self._conn.execute(
            "SELECT * FROM athlete_results WHERE result_token = ?", (token,)
        ).fetchone()
        return self._row_to_result(row) if row else None

    def get_race(self, race_id: str) -> Optional[HyroxRaceResults]:
        race = self._conn.execute(
            "SELECT * FROM races WHERE race_id = ?", (race_id,)
        ).fetchone()
        if race is None:
            return None
        rows = self._conn.execute(
            "SELECT * FROM athlete_results WHERE race_id = ? "
            "ORDER BY rank IS NULL, rank ASC, display_name",
            (race_id,),
        ).fetchall()
        return HyroxRaceResults(
            race_id=race["race_id"], venue_id=race["venue_id"], mode=race["mode"],
            course_profile_id=race["course_profile_id"],
            started_at_ms=race["started_at_ms"], finalized_at_ms=race["finalized_at_ms"],
            athletes=[self._row_to_result(r) for r in rows],
        )

    def _row_to_result(self, row) -> HyroxAthleteResult:
        splits = self._conn.execute(
            "SELECT * FROM stage_splits WHERE result_token = ? ORDER BY seq",
            (row["result_token"],),
        ).fetchall()
        return HyroxAthleteResult(
            result_token=row["result_token"], race_id=row["race_id"],
            subject_id=row["subject_id"], display_name=row["display_name"],
            division=row["division"], members=json.loads(row["members"]),
            status=row["status"], started_at_ms=row["started_at_ms"],
            finished_at_ms=row["finished_at_ms"], total_time_ms=row["total_time_ms"],
            run_total_ms=row["run_total_ms"], workout_total_ms=row["workout_total_ms"],
            roxzone_total_ms=row["roxzone_total_ms"],
            dnf_stage=HyroxStage(row["dnf_stage"]) if row["dnf_stage"] else None,
            rank=row["rank"],
            splits=[HyroxStageSplit(
                stage=HyroxStage(s["stage"]), seq=s["seq"], resource_id=s["resource_id"],
                arrived_ms=s["arrived_ms"], ended_ms=s["ended_ms"], split_ms=s["split_ms"],
                work_ms=s["work_ms"], roxzone_before_ms=s["roxzone_before_ms"],
                cumulative_ms=s["cumulative_ms"], value=s["value"], target=s["target"],
            ) for s in splits],
        )

    def export_csv(self, race_id: str) -> str:
        import csv
        out = StringIO()
        w = csv.writer(out)
        w.writerow(["rank", "name", "division", "status", "total_ms",
                    "run_ms", "workout_ms", "roxzone_ms", "dnf_stage"])
        race = self.get_race(race_id)
        if race:
            for a in race.athletes:
                w.writerow([a.rank, a.display_name, a.division, a.status, a.total_time_ms,
                            a.run_total_ms, a.workout_total_ms, a.roxzone_total_ms,
                            a.dnf_stage.value if a.dnf_stage else ""])
        return out.getvalue()

    def close(self):
        self._conn.close()
