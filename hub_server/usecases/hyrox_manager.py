import logging
import time
from typing import Dict, Optional
from hub_server.domain.models import AthleteHyroxState, HyroxStage, HyroxConfig

logger = logging.getLogger("hub_server.hyrox_manager")

# HyroxStage enum declaration order IS the race sequence; every transition
# (auto or forced) just advances to the next entry. FINISHED is last.
STAGE_ORDER = list(HyroxStage)
STAGE_INDEX = {stage: i for i, stage in enumerate(STAGE_ORDER)}

# Physical station number -> the workout held there.
STATION_TO_WORKOUT = {
    1: HyroxStage.SKI_ERG,
    2: HyroxStage.SLED_PUSH,
    3: HyroxStage.SLED_PULL,
    4: HyroxStage.BURPEE_BROAD,
    5: HyroxStage.ROW,
    6: HyroxStage.FARMERS_CARRY,
    7: HyroxStage.SANDBAG_LUNGES,
    8: HyroxStage.WALL_BALLS,
}

RUNNING_TRACK_STATION = 9

# Target repetitions/laps for functional stages
STAGE_TARGETS = {
    HyroxStage.SKI_ERG: 1000,
    HyroxStage.SLED_PUSH: 4,
    HyroxStage.SLED_PULL: 4,
    HyroxStage.BURPEE_BROAD: 4,
    HyroxStage.ROW: 1000,
    HyroxStage.FARMERS_CARRY: 4,
    HyroxStage.SANDBAG_LUNGES: 4,
    HyroxStage.WALL_BALLS: 75,
}

# Stages whose laps are counted by alternating start/finish line crossings
LANE_STAGES = {
    HyroxStage.SLED_PUSH,
    HyroxStage.SLED_PULL,
    HyroxStage.BURPEE_BROAD,
    HyroxStage.FARMERS_CARRY,
    HyroxStage.SANDBAG_LUNGES,
}


class HyroxManager:
    def __init__(self, config: Optional[HyroxConfig] = None):
        self._config: HyroxConfig = config or HyroxConfig()
        self._athletes: Dict[str, AthleteHyroxState] = {}  # rfid_tag_id -> AthleteHyroxState
        self._station_to_tag: Dict[int, str] = {}  # station_number -> rfid_tag_id
        self._is_active: bool = False

        # Track the last registered location of tag for sequence validation
        # tag_id -> last_registered_location ("start_line" | "finish_line")
        self._last_positions: Dict[str, str] = {}

        # tag_id -> epoch_ms of last counted running-track crossing (debounce)
        self._last_run_crossings: Dict[str, int] = {}

    def configure(self, config: HyroxConfig):
        self._config = config
        self._athletes.clear()
        self._station_to_tag.clear()
        self._is_active = False
        self._last_positions.clear()
        self._last_run_crossings.clear()

    def get_config(self) -> HyroxConfig:
        return self._config

    def get_state(self) -> dict:
        return {
            "is_active": self._is_active,
            "config": self._config.model_dump(),
            "athletes": {
                tag_id: athlete.model_dump()
                for tag_id, athlete in list(self._athletes.items())
            },
        }

    def register_athlete(
        self,
        athlete_name: str,
        rfid_tag_id: str,
        station_number: Optional[int] = None,
        team_name: Optional[str] = None,
        division: str = "individual",
    ):
        if station_number is None:
            station_number = 0

        athlete = AthleteHyroxState(
            athlete_name=athlete_name,
            team_name=team_name,
            station_number=station_number,
            division=division,
            current_stage=HyroxStage.RUN_1,
        )
        self._athletes[rfid_tag_id] = athlete
        if station_number > 0:
            self._station_to_tag[station_number] = rfid_tag_id

    def start_race(self):
        self._is_active = True
        now_ms = int(time.time() * 1000)
        for athlete in self._athletes.values():
            athlete.stage_start_times[HyroxStage.RUN_1.value] = now_ms

    def _bind_station(self, tag_id: str, athlete: AthleteHyroxState, station_number: int):
        """Rebind athlete to the station they were just read at."""
        current_holder = self._station_to_tag.get(station_number)
        if current_holder and current_holder != tag_id:
            holder = self._athletes.get(current_holder)
            # Don't let a passing tag steal the binding from an athlete
            # mid-wall-balls: their sensor reps are attributed via this map.
            if holder and holder.current_stage == HyroxStage.WALL_BALLS:
                return

        old_station = athlete.station_number
        if old_station > 0 and self._station_to_tag.get(old_station) == tag_id:
            self._station_to_tag.pop(old_station, None)

        athlete.station_number = station_number
        self._station_to_tag[station_number] = tag_id

    def register_tag_crossing(
        self,
        tag_id: str,
        location: str,
        rssi: float,
        timestamp_ms: int,
        station_number: Optional[int] = None,
    ):
        """
        Registers tag crossing at start or finish line mat.
        """
        if not self._is_active:
            return

        # Spillover filter (cross-talk prevention)
        if rssi < self._config.rssi_threshold_dbm:
            logger.debug(f"Filtered signal from tag {tag_id} due to low RSSI: {rssi}")
            return

        athlete = self._athletes.get(tag_id)
        if not athlete:
            return

        # Dynamically assign station number based on active RFID reader location
        if station_number is not None and station_number > 0:
            self._bind_station(tag_id, athlete, station_number)

        current_stage = athlete.current_stage
        is_running_track = (
            location in ("running_track", "track")
            or station_number == RUNNING_TRACK_STATION
        )

        # 1. Running lap counting
        if current_stage.value.startswith("run_") and is_running_track:
            last_crossing = self._last_run_crossings.get(tag_id, 0)
            if timestamp_ms - last_crossing < self._config.run_lap_debounce_ms:
                return
            self._last_run_crossings[tag_id] = timestamp_ms

            stage_name = current_stage.value
            athlete.stage_laps[stage_name] = athlete.stage_laps.get(stage_name, 0) + 1
            return

        # 2. Run -> workstation auto-transition: jump to whatever workout the
        # athlete was read at, even out of sequence (sensor truth wins).
        if current_stage.value.startswith("run_"):
            workout = STATION_TO_WORKOUT.get(station_number)
            if workout:
                self._enter_stage(athlete, tag_id, workout, timestamp_ms)
                # The entry read doubles as the first lane position, so the
                # next crossing at the opposite mat counts as length 1
                if workout in LANE_STAGES:
                    self._last_positions[tag_id] = location
            return

        # 3. Workstation -> run auto-transition (cardio/lengths fallback).
        # Excludes WALL_BALLS: crossing the track must not finish the race.
        next_stage = self._next_stage(current_stage)
        if is_running_track and next_stage and next_stage.value.startswith("run_"):
            self._enter_stage(athlete, tag_id, next_stage, timestamp_ms)
            return

        # 4. Lap counting for workout lanes
        if current_stage not in LANE_STAGES:
            return

        # Sequence validation: same mat twice in a row is a duplicate read
        last_pos = self._last_positions.get(tag_id)
        if last_pos == location:
            return

        self._last_positions[tag_id] = location

        if last_pos is None:
            # First crossing registers position but doesn't count as a complete length
            return

        # Alternating crossing completes a length
        stage_name = current_stage.value
        completed_laps = athlete.stage_laps.get(stage_name, 0) + 1
        athlete.stage_laps[stage_name] = completed_laps

        if completed_laps >= STAGE_TARGETS.get(current_stage, 4):
            self._advance(athlete, tag_id, timestamp_ms)

    def register_wallball_rep(self, station_number: int, timestamp_ms: int):
        """
        Registers a valid wall ball rep for the athlete bound to this station.
        """
        if not self._is_active:
            return

        tag_id = self._station_to_tag.get(station_number)
        if not tag_id:
            return

        athlete = self._athletes.get(tag_id)
        if not athlete or athlete.current_stage != HyroxStage.WALL_BALLS:
            return

        stage_name = HyroxStage.WALL_BALLS.value
        completed_reps = athlete.stage_laps.get(stage_name, 0) + 1
        athlete.stage_laps[stage_name] = completed_reps

        if completed_reps >= STAGE_TARGETS[HyroxStage.WALL_BALLS]:
            self._advance(athlete, tag_id, timestamp_ms)

    def complete_current_stage(self, tag_id: str, timestamp_ms: int) -> bool:
        """
        Force completes the current stage of the athlete and transitions to
        the next stage. Returns False if the race is inactive, the tag is
        unknown, or the athlete has already finished.
        """
        if not self._is_active:
            return False

        athlete = self._athletes.get(tag_id)
        if not athlete or athlete.current_stage == HyroxStage.FINISHED:
            return False

        self._advance(athlete, tag_id, timestamp_ms)
        return True

    @staticmethod
    def _next_stage(stage: HyroxStage) -> Optional[HyroxStage]:
        idx = STAGE_INDEX[stage]
        return STAGE_ORDER[idx + 1] if idx + 1 < len(STAGE_ORDER) else None

    def _advance(self, athlete: AthleteHyroxState, tag_id: str, timestamp_ms: int):
        next_stage = self._next_stage(athlete.current_stage)
        if next_stage:
            self._enter_stage(athlete, tag_id, next_stage, timestamp_ms)

    def _enter_stage(
        self,
        athlete: AthleteHyroxState,
        tag_id: str,
        next_stage: HyroxStage,
        timestamp_ms: int,
    ):
        athlete.stage_end_times[athlete.current_stage.value] = timestamp_ms
        athlete.current_stage = next_stage
        self._last_positions.pop(tag_id, None)

        if next_stage == HyroxStage.FINISHED:
            start_time = athlete.stage_start_times.get(HyroxStage.RUN_1.value, timestamp_ms)
            athlete.total_elapsed_time_ms = timestamp_ms - start_time
        else:
            athlete.stage_start_times[next_stage.value] = timestamp_ms
