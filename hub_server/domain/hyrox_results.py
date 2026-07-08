"""Hyrox result contracts: per-station splits and finalized athlete results.

See docs/hyrox_results_spec.md. Pure data models -- computation lives in
hub_server/usecases/hyrox_results_store.py.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from hub_server.domain.models import HyroxStage


class HyroxStageSplit(BaseModel):
    stage: HyroxStage
    seq: int                          # stage order, 0..15
    resource_id: Optional[str] = None
    arrived_ms: Optional[int] = None
    ended_ms: Optional[int] = None
    split_ms: int = 0                 # ended - previous ended (roxzone + work)
    work_ms: int = 0                  # ended - arrived
    roxzone_before_ms: int = 0        # arrived - previous ended
    cumulative_ms: int = 0            # athlete start -> this stage end
    value: Optional[float] = None     # completed distance / lengths / reps
    target: Optional[float] = None


class HyroxAthleteResult(BaseModel):
    result_token: str
    race_id: str
    subject_id: str
    display_name: str
    division: Literal["individual", "doubles", "relay"] = "individual"
    members: list[str] = Field(default_factory=list)
    status: Literal["finished", "dnf"]
    started_at_ms: int
    finished_at_ms: Optional[int] = None
    total_time_ms: Optional[int] = None
    run_total_ms: int = 0
    workout_total_ms: int = 0
    roxzone_total_ms: int = 0
    dnf_stage: Optional[HyroxStage] = None
    rank: Optional[int] = None
    splits: list[HyroxStageSplit] = Field(default_factory=list)


class HyroxRaceResults(BaseModel):
    race_id: str
    venue_id: str
    mode: Literal["training", "competition"]
    course_profile_id: str
    started_at_ms: Optional[int] = None
    finalized_at_ms: Optional[int] = None
    athletes: list[HyroxAthleteResult] = Field(default_factory=list)
