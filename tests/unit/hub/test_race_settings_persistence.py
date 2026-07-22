from hub_server.domain.models import RaceConfig, RaceState
from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.race_settings_store import RaceSettingsStore


def test_durable_settings_survive_a_new_manager(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    rm = RaceManager(settings_store=store)
    rm.assign_station(1, "node-01")
    rm.assign_station(2, "node-02")
    rm.set_leaderboard_display_mode("race_track")
    rm.set_start_countdown_sound_enabled(False)
    rm.configure(RaceConfig(race_type="distance", target_value=100, duration_sec=0))

    # A fresh manager pointed at the same file (simulates a hub restart).
    restored = RaceManager(settings_store=RaceSettingsStore(tmp_path / "settings.json"))
    status = restored.get_stations_status()
    assert status["stations"][1]["node_id"] == "node-01"
    assert status["stations"][2]["node_id"] == "node-02"
    assert restored.get_leaderboard_display_mode() == "race_track"
    assert restored.get_start_countdown_sound_enabled() is False
    assert restored.get_config().race_type == "distance"
    # Transient state is NOT resumed: a restart always comes back IDLE.
    assert restored.get_state() == RaceState.IDLE


def test_unassign_is_persisted(tmp_path):
    path = tmp_path / "settings.json"
    rm = RaceManager(settings_store=RaceSettingsStore(path))
    rm.assign_station(1, "node-01")
    rm.assign_station(1, None)  # unassign
    restored = RaceManager(settings_store=RaceSettingsStore(path))
    assert restored.get_stations_status()["stations"] == {}


def test_no_store_means_no_persistence_and_no_file(tmp_path):
    # Default construction (used across the test suite) must not touch disk.
    rm = RaceManager()
    rm.assign_station(1, "node-01")
    assert not list(tmp_path.iterdir())


def test_corrupt_settings_file_is_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not valid json")
    rm = RaceManager(settings_store=RaceSettingsStore(path))
    assert rm.get_stations_status()["stations"] == {}
    assert rm.get_leaderboard_display_mode() == "classic"
