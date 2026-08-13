import pytest

from hub_server.domain.class_models import ClassPlan
from hub_server.domain.models import RaceConfig, RaceState
from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.race_settings_store import RaceSettingsStore


def _plan(*durations):
    return ClassPlan(segments=[{"kind": "work", "duration_sec": d} for d in durations])


# -- session mode basics ---------------------------------------------------


def test_session_mode_defaults_to_race():
    manager = RaceManager()
    assert manager.get_session_mode() == "race"


def test_set_session_mode_to_class_and_back():
    manager = RaceManager()
    manager.set_session_mode("class")
    assert manager.get_session_mode() == "class"
    manager.set_session_mode("race")
    assert manager.get_session_mode() == "race"


def test_set_session_mode_rejects_unknown_value():
    manager = RaceManager()
    with pytest.raises(ValueError):
        manager.set_session_mode("relay")


def test_set_session_mode_rejects_switch_to_class_while_running():
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    manager.register_node("node-01", "Runner A")
    manager.start_race()
    assert manager.get_state() == RaceState.RUNNING
    with pytest.raises(ValueError):
        manager.set_session_mode("class")
    # Mode is untouched by the rejected attempt.
    assert manager.get_session_mode() == "race"


def test_set_session_mode_rejects_switch_to_race_while_running():
    manager = RaceManager()
    manager.configure_class(_plan(60, 60))
    manager.start_race()
    assert manager.get_state() == RaceState.RUNNING
    assert manager.get_session_mode() == "class"
    with pytest.raises(ValueError):
        manager.set_session_mode("race")
    assert manager.get_session_mode() == "class"


# -- class lifecycle ---------------------------------------------------


def test_configure_class_sets_plan_mode_and_moves_to_ready():
    manager = RaceManager()
    plan = _plan(300, 1200, 300)
    manager.configure_class(plan)
    assert manager.get_state() == RaceState.READY
    assert manager.get_session_mode() == "class"
    assert manager.get_class_plan().total_duration_sec == 1800


def test_configure_class_from_stopped_clears_transient_state_like_configure():
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    manager.register_node("node-01", "Runner A")
    manager.start_race()
    manager.update_telemetry(
        {"node_id": "node-01", "distance_m": 10.0, "elapsed_time_ms": 1000}
    )
    manager.stop_race()
    assert manager.get_state() == RaceState.STOPPED

    manager.configure_class(_plan(60, 60))

    assert manager.get_state() == RaceState.READY
    assert manager.get_registered_nodes() == {}
    assert manager.get_leaderboard_progress() == {}


def test_start_race_rejects_class_with_no_plan_configured():
    manager = RaceManager()
    # Get to READY via a normal race configure, then flip session mode to
    # class without ever calling configure_class -- no plan is set.
    manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    manager.set_session_mode("class")
    assert manager.get_state() == RaceState.READY
    with pytest.raises(ValueError):
        manager.start_race()


def test_start_race_succeeds_for_class_with_plan_configured():
    manager = RaceManager()
    manager.configure_class(_plan(60, 60))
    manager.start_race()
    assert manager.get_state() == RaceState.RUNNING


def test_reset_race_clears_plan_and_returns_session_mode_to_race():
    manager = RaceManager()
    manager.configure_class(_plan(60, 60))
    manager.start_race()
    manager.reset_race()
    assert manager.get_state() == RaceState.IDLE
    assert manager.get_session_mode() == "race"
    assert manager.get_class_plan() is None


def test_race_configure_sets_session_mode_back_to_race():
    manager = RaceManager()
    manager.configure_class(_plan(60, 60))
    assert manager.get_session_mode() == "class"
    manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    assert manager.get_session_mode() == "race"


# -- the highest-value test: a class never auto-stops ----------------------


def test_class_never_auto_stops_even_when_telemetry_far_exceeds_plan_duration():
    manager = RaceManager()
    plan = _plan(5, 5)  # total 10s
    manager.configure_class(plan)
    manager.register_node("node-01", "Athlete A")
    manager.register_node("node-02", "Athlete B")
    manager.start_race()
    assert manager.get_state() == RaceState.RUNNING

    # Feed telemetry WAY past the plan's total duration (10s) for both
    # participants -- a race would have auto-stopped by now.
    progress = manager.update_telemetry(
        {"node_id": "node-01", "distance_m": 5000.0, "elapsed_time_ms": 3_600_000}
    )
    assert manager.get_state() == RaceState.RUNNING
    assert progress["node-01"]["finished_time_ms"] is None

    progress = manager.update_telemetry(
        {"node_id": "node-02", "distance_m": 6000.0, "elapsed_time_ms": 3_600_000}
    )
    # Both "participants" are miles past the plan's duration -- a race
    # would have transitioned to STOPPED here. A class must not.
    assert manager.get_state() == RaceState.RUNNING
    assert progress["node-01"]["finished_time_ms"] is None
    assert progress["node-02"]["finished_time_ms"] is None
    assert manager.get_end_time_epoch_ms() is None

    # Only the coach can stop it.
    manager.stop_race()
    assert manager.get_state() == RaceState.STOPPED


def test_class_never_auto_stops_even_with_a_leftover_race_config():
    # Realistic hardware reuse: the hub was previously configured for a
    # race on this same process (leaving a RaceConfig behind -- configure()
    # and configure_class() never clear the other mode's leftovers), then
    # the coach switches the same session to class mode. The auto-stop
    # guard must key off session_mode, not off whether a RaceConfig
    # happens to still be sitting around.
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="time", duration_sec=5))
    manager.configure_class(_plan(5, 5))  # total 10s
    manager.register_node("node-01", "Athlete A")
    manager.start_race()
    assert manager.get_state() == RaceState.RUNNING

    # elapsed_time_ms far exceeds the leftover config's duration_sec*1000
    # (5000ms) -- a race in this state would already have auto-stopped.
    progress = manager.update_telemetry(
        {"node_id": "node-01", "distance_m": 10.0, "elapsed_time_ms": 3_600_000}
    )
    assert manager.get_state() == RaceState.RUNNING
    assert progress["node-01"]["finished_time_ms"] is None
    assert manager.get_end_time_epoch_ms() is None


def test_class_progress_percent_tracks_plan_elapsed_clamped_to_100():
    manager = RaceManager()
    plan = _plan(10, 10)  # total 20s
    manager.configure_class(plan)
    manager.register_node("node-01", "Athlete A")
    manager.start_race()

    progress = manager.update_telemetry(
        {"node_id": "node-01", "distance_m": 1.0, "elapsed_time_ms": 10_000}
    )
    assert progress["node-01"]["progress_percent"] == 50.0

    # Way past total duration -- clamped to 100, not left uncapped.
    progress = manager.update_telemetry(
        {"node_id": "node-01", "distance_m": 2.0, "elapsed_time_ms": 999_999}
    )
    assert progress["node-01"]["progress_percent"] == 100.0
    # distance/power/calories are still computed normally, untouched.
    assert progress["node-01"]["distance_m"] == 2.0


# -- regression guard: a race still auto-stops exactly as before -----------


def test_race_still_auto_stops_exactly_as_before():
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    manager.register_node("node-01", "Runner A")
    manager.start_race()

    progress = manager.update_telemetry(
        {"node_id": "node-01", "distance_m": 100.0, "elapsed_time_ms": 18000}
    )
    assert manager.get_state() == RaceState.STOPPED
    assert progress["node-01"]["finished_time_ms"] == 18000
    assert manager.get_end_time_epoch_ms() is not None


# -- state snapshot ---------------------------------------------------


def test_state_snapshot_includes_session_mode_and_class_plan():
    manager = RaceManager()
    plan = _plan(60, 60)
    manager.configure_class(plan)
    snapshot = manager.get_state_snapshot()
    assert snapshot["session_mode"] == "class"
    assert snapshot["class_plan"]["segments"][0]["duration_sec"] == 60


def test_state_snapshot_class_plan_is_none_for_race_mode():
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    snapshot = manager.get_state_snapshot()
    assert snapshot["session_mode"] == "race"
    assert snapshot["class_plan"] is None
    assert "class_segment" not in snapshot or snapshot.get("class_segment") is None


def test_state_snapshot_includes_class_segment_while_running():
    manager = RaceManager()
    plan = _plan(60, 60)
    manager.configure_class(plan)
    manager.start_race()
    snapshot = manager.get_state_snapshot()
    assert snapshot["session_mode"] == "class"
    segment = snapshot["class_segment"]
    assert segment["index"] == 0
    assert segment["kind"] == "work"
    assert segment["finished"] is False


def test_state_snapshot_no_class_segment_when_not_running():
    manager = RaceManager()
    plan = _plan(60, 60)
    manager.configure_class(plan)
    snapshot = manager.get_state_snapshot()
    assert snapshot["session_mode"] == "class"
    assert snapshot.get("class_segment") is None


# -- settings persistence / backward compatibility --------------------


def test_durable_settings_persist_session_mode_and_class_plan(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    manager = RaceManager(settings_store=store)
    manager.configure_class(_plan(300, 1200, 300))

    restored = RaceManager(settings_store=RaceSettingsStore(tmp_path / "settings.json"))
    assert restored.get_session_mode() == "class"
    assert restored.get_class_plan().total_duration_sec == 1800
    # Transient race state is not resumed.
    assert restored.get_state() == RaceState.IDLE


def test_old_settings_file_without_session_mode_or_class_plan_defaults(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    # Simulate a 0.2.1 settings.json written before this feature existed --
    # neither "session_mode" nor "class_plan" keys are present.
    store.save(
        {
            "stations": {},
            "leaderboard_display_mode": "classic",
            "start_countdown_sound_enabled": True,
            "config": None,
        }
    )
    manager = RaceManager(settings_store=RaceSettingsStore(tmp_path / "settings.json"))
    assert manager.get_session_mode() == "race"
    assert manager.get_class_plan() is None
