import pytest
from hub_server.domain.models import AthleteHyroxState, HyroxStage
from hub_server.usecases.hyrox_manager import HyroxManager


def test_hyrox_manager_initial_state():
    manager = HyroxManager()
    assert manager._is_active is False
    assert len(manager._athletes) == 0


def test_hyrox_manager_registration_and_activation():
    manager = HyroxManager()

    # Register an athlete tag binding
    manager.register_athlete(
        athlete_name="Tony",
        rfid_tag_id="E28011052000789A",
        station_number=1,
        team_name="Alpha"
    )

    assert len(manager._athletes) == 1
    athlete = manager._athletes["E28011052000789A"]
    assert athlete.athlete_name == "Tony"
    assert athlete.current_stage == HyroxStage.RUN_1

    # Start the race
    manager.start_race()
    assert manager._is_active is True
    assert HyroxStage.RUN_1.value in athlete.stage_start_times


def test_hyrox_manager_spillover_filter():
    manager = HyroxManager()
    manager.register_athlete("Tony", "E28011052000789A", 1)
    manager.start_race()

    # Transition to SLED_PUSH first for testing RFID
    athlete = manager._athletes["E28011052000789A"]
    athlete.current_stage = HyroxStage.SLED_PUSH

    # Tag crossing with low RSSI (< -60 dBm)
    manager.register_tag_crossing(
        tag_id="E28011052000789A",
        location="start_line",
        rssi=-75.0,
        timestamp_ms=1000
    )
    assert athlete.stage_laps.get(HyroxStage.SLED_PUSH.value, 0) == 0

    # Tag crossing with high RSSI (>= -60 dBm)
    manager.register_tag_crossing(
        tag_id="E28011052000789A",
        location="start_line",
        rssi=-45.0,
        timestamp_ms=2000
    )
    # Just starting doesn't count as a complete length (must cross to finish line)
    assert athlete.stage_laps.get(HyroxStage.SLED_PUSH.value, 0) == 0


def test_hyrox_manager_sequence_validation():
    manager = HyroxManager()
    manager.register_athlete("Tony", "E28011052000789A", 1)
    manager.start_race()

    athlete = manager._athletes["E28011052000789A"]
    athlete.current_stage = HyroxStage.SLED_PUSH

    # 1. First crossing at Start Line
    manager.register_tag_crossing("E28011052000789A", "start_line", -45.0, 1000)
    assert athlete.stage_laps.get(HyroxStage.SLED_PUSH.value, 0) == 0

    # 2. Duplicate crossing at Start Line (should be ignored)
    manager.register_tag_crossing("E28011052000789A", "start_line", -45.0, 2000)
    assert athlete.stage_laps.get(HyroxStage.SLED_PUSH.value, 0) == 0

    # 3. Crossing at Finish Line (completes 1st lap/length)
    manager.register_tag_crossing("E28011052000789A", "finish_line", -45.0, 3000)
    assert athlete.stage_laps.get(HyroxStage.SLED_PUSH.value, 0) == 1

    # 4. Duplicate crossing at Finish Line (should be ignored)
    manager.register_tag_crossing("E28011052000789A", "finish_line", -45.0, 4000)
    assert athlete.stage_laps.get(HyroxStage.SLED_PUSH.value, 0) == 1

    # 5. Crossing back at Start Line (completes 2nd lap/length)
    manager.register_tag_crossing("E28011052000789A", "start_line", -45.0, 5000)
    assert athlete.stage_laps.get(HyroxStage.SLED_PUSH.value, 0) == 2


def test_hyrox_manager_stage_transitions():
    manager = HyroxManager()
    manager.register_athlete("Tony", "E28011052000789A", 1)
    manager.start_race()

    athlete = manager._athletes["E28011052000789A"]

    # Manually transition running stages for test setup
    # 1. Complete RUN_1
    manager.complete_current_stage("E28011052000789A", timestamp_ms=10000)
    assert athlete.current_stage == HyroxStage.SKI_ERG

    # Complete SKI_ERG workout stage
    manager.complete_current_stage("E28011052000789A", timestamp_ms=10100)
    assert athlete.current_stage == HyroxStage.RUN_2

    # Complete RUN_2 stage
    manager.complete_current_stage("E28011052000789A", timestamp_ms=10500)
    assert athlete.current_stage == HyroxStage.SLED_PUSH

    # Now push the sled: 4 laps/lengths required
    manager.register_tag_crossing("E28011052000789A", "start_line", -40.0, 11000)
    manager.register_tag_crossing("E28011052000789A", "finish_line", -40.0, 12000) # Lap 1
    manager.register_tag_crossing("E28011052000789A", "start_line", -40.0, 13000)  # Lap 2
    manager.register_tag_crossing("E28011052000789A", "finish_line", -40.0, 14000) # Lap 3
    manager.register_tag_crossing("E28011052000789A", "start_line", -40.0, 15000)  # Lap 4

    # Should transition to RUN_3 automatically upon hitting target (4 laps)
    assert athlete.current_stage == HyroxStage.RUN_3
    assert athlete.stage_laps.get(HyroxStage.SLED_PUSH.value) == 4
    assert HyroxStage.SLED_PUSH.value in athlete.stage_end_times
    assert HyroxStage.RUN_3.value in athlete.stage_start_times


def test_hyrox_manager_wallball_counts():
    manager = HyroxManager()
    manager.register_athlete("Tony", "E28011052000789A", 1)
    manager.start_race()

    athlete = manager._athletes["E28011052000789A"]
    athlete.current_stage = HyroxStage.WALL_BALLS

    # Record wall ball reps
    manager.register_wallball_rep(station_number=1, timestamp_ms=20000)
    assert athlete.stage_laps.get(HyroxStage.WALL_BALLS.value, 0) == 1

    manager.register_wallball_rep(station_number=1, timestamp_ms=21000)
    assert athlete.stage_laps.get(HyroxStage.WALL_BALLS.value, 0) == 2

    # Simulate hitting the target of 75 reps
    athlete.stage_laps[HyroxStage.WALL_BALLS.value] = 74
    manager.register_wallball_rep(station_number=1, timestamp_ms=22000)

    # Should transition to FINISHED
    assert athlete.current_stage == HyroxStage.FINISHED
    assert athlete.stage_laps.get(HyroxStage.WALL_BALLS.value) == 75


def test_hyrox_manager_configuration():
    from hub_server.domain.models import HyroxConfig
    config = HyroxConfig(competition_mode="doubles", session_type="competition")
    manager = HyroxManager(config=config)

    assert manager.get_config().competition_mode == "doubles"
    assert manager.get_config().session_type == "competition"

    # Test on-the-fly reconfiguration
    new_config = HyroxConfig(competition_mode="relay", session_type="training")
    manager.configure(new_config)
    assert manager.get_config().competition_mode == "relay"
    assert manager.get_config().session_type == "training"


def test_hyrox_manager_complete_current_stage():
    manager = HyroxManager()
    manager.register_athlete("Tony", "E28011052000789A", 1)
    manager.start_race()

    athlete = manager._athletes["E28011052000789A"]

    # Initially in RUN_1
    assert athlete.current_stage == HyroxStage.RUN_1

    # Force complete RUN_1 -> SKI_ERG
    manager.complete_current_stage("E28011052000789A", timestamp_ms=5000)
    assert athlete.current_stage == HyroxStage.SKI_ERG
    assert athlete.stage_end_times[HyroxStage.RUN_1.value] == 5000
    assert athlete.stage_start_times[HyroxStage.SKI_ERG.value] == 5000

    # Force complete SKI_ERG -> RUN_2
    manager.complete_current_stage("E28011052000789A", timestamp_ms=10000)
    assert athlete.current_stage == HyroxStage.RUN_2


def test_hyrox_manager_auto_rfid_transitions():
    manager = HyroxManager()
    manager.register_athlete("Tony", "E28011052000789A", 1)
    manager.start_race()

    athlete = manager._athletes["E28011052000789A"]

    # 1. In RUN_1, detected at SKI_ERG (Station 1) -> transitions to SKI_ERG
    manager.register_tag_crossing("E28011052000789A", "start_line", -42.0, 5000, station_number=1)
    assert athlete.current_stage == HyroxStage.SKI_ERG

    # 2. In SKI_ERG, detected at running track (location="running_track") -> transitions to RUN_2
    manager.register_tag_crossing("E28011052000789A", "running_track", -42.0, 10000, station_number=9)
    assert athlete.current_stage == HyroxStage.RUN_2

    # 3. In RUN_2, detected at Sled Push (Station 2) -> transitions to SLED_PUSH
    manager.register_tag_crossing("E28011052000789A", "start_line", -42.0, 15000, station_number=2)
    assert athlete.current_stage == HyroxStage.SLED_PUSH

    # The entry read counts as the first lane position: reaching the opposite
    # mat is length 1 (no extra crossing needed)
    manager.register_tag_crossing("E28011052000789A", "finish_line", -42.0, 16000, station_number=2)
    assert athlete.stage_laps[HyroxStage.SLED_PUSH.value] == 1


def test_hyrox_manager_run_lap_debounce_keeps_stage_times_clean():
    manager = HyroxManager()
    manager.register_athlete("Tony", "E28011052000789A", 1)
    manager.start_race()
    athlete = manager._athletes["E28011052000789A"]

    # Two reads within the debounce window count as one lap
    manager.register_tag_crossing("E28011052000789A", "running_track", -42.0, 10000)
    manager.register_tag_crossing("E28011052000789A", "running_track", -42.0, 10500)
    assert athlete.stage_laps[HyroxStage.RUN_1.value] == 1

    # A read past the window counts a second lap
    manager.register_tag_crossing("E28011052000789A", "running_track", -42.0, 12000)
    assert athlete.stage_laps[HyroxStage.RUN_1.value] == 2

    # stage_start_times holds only real stage keys (no debounce bookkeeping)
    assert set(athlete.stage_start_times) == {HyroxStage.RUN_1.value}


def test_hyrox_manager_wallball_binding_not_stolen_by_passing_tag():
    manager = HyroxManager()
    manager.register_athlete("Tony", "TAG_TONY", 8)
    manager.register_athlete("Bob", "TAG_BOB")
    manager.start_race()

    tony = manager._athletes["TAG_TONY"]
    tony.current_stage = HyroxStage.WALL_BALLS

    # Bob's tag is read at station 8 while Tony is mid-wall-balls
    manager.register_tag_crossing("TAG_BOB", "start_line", -42.0, 5000, station_number=8)

    # Station 8 stays bound to Tony; his reps keep counting
    assert manager._station_to_tag[8] == "TAG_TONY"
    manager.register_wallball_rep(station_number=8, timestamp_ms=6000)
    assert tony.stage_laps[HyroxStage.WALL_BALLS.value] == 1


def test_hyrox_manager_wallball_track_crossing_does_not_finish_race():
    manager = HyroxManager()
    manager.register_athlete("Tony", "TAG_TONY", 8)
    manager.start_race()
    athlete = manager._athletes["TAG_TONY"]
    athlete.current_stage = HyroxStage.WALL_BALLS

    # Crossing the running track during wall balls must not advance to FINISHED
    manager.register_tag_crossing("TAG_TONY", "running_track", -42.0, 5000, station_number=9)
    assert athlete.current_stage == HyroxStage.WALL_BALLS


def test_hyrox_manager_complete_current_stage_reports_failure():
    manager = HyroxManager()

    # Race not active
    assert manager.complete_current_stage("UNKNOWN", 1000) is False

    manager.register_athlete("Tony", "TAG_TONY", 1)
    manager.start_race()

    # Unknown tag
    assert manager.complete_current_stage("UNKNOWN", 1000) is False

    # Known tag succeeds
    assert manager.complete_current_stage("TAG_TONY", 1000) is True

    # Already finished
    manager._athletes["TAG_TONY"].current_stage = HyroxStage.FINISHED
    assert manager.complete_current_stage("TAG_TONY", 2000) is False
