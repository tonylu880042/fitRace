"""Game Admin gains a "Start new event" action that sets an event
boundary (hub-time epoch ms) on RaceManager. RaceResultsQuery.get_standings
uses this boundary to scope the overall standings to races that happened
at or after it, so a rehearsal race from two days ago (same race config)
never bleeds into today's heats.

RaceManager gains this field mirroring exactly how
start_countdown_sound_enabled/signup_qr_visible already work: a
getter/setter (here, start_new_event()), persisted via RaceSettingsStore in
_load_settings/_persist_settings, and included in get_state_snapshot().
No timestamp set yet (None) means "no boundary" -- get_standings falls back
to counting the entire history, i.e. today's existing behaviour is
unaffected until an operator opts in.
"""

import pytest

from hub_server.domain.models import RaceConfig, RaceState
from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.race_settings_store import RaceSettingsStore


def test_event_start_defaults_to_none():
    rm = RaceManager()
    assert rm.get_event_start_epoch_ms() is None
    snapshot = rm.get_state_snapshot()
    assert snapshot["event_start_epoch_ms"] is None


def test_start_new_event_sets_and_returns_the_timestamp():
    rm = RaceManager()
    ts = rm.start_new_event(now_epoch_ms=123456789)
    assert ts == 123456789
    assert rm.get_event_start_epoch_ms() == 123456789
    assert rm.get_state_snapshot()["event_start_epoch_ms"] == 123456789


def test_start_new_event_survives_a_new_manager(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    rm = RaceManager(settings_store=store)
    rm.start_new_event(now_epoch_ms=555000)

    restored = RaceManager(settings_store=RaceSettingsStore(tmp_path / "settings.json"))
    assert restored.get_event_start_epoch_ms() == 555000


def test_old_settings_file_without_key_defaults_to_none(tmp_path):
    path = tmp_path / "settings.json"
    store = RaceSettingsStore(path)
    store.save({"leaderboard_display_mode": "classic"})

    rm = RaceManager(settings_store=RaceSettingsStore(path))
    assert rm.get_event_start_epoch_ms() is None


def test_start_new_event_blocked_while_race_running():
    rm = RaceManager()
    rm._config = RaceConfig(race_type="time", duration_sec=60)
    rm._state = RaceState.RUNNING

    with pytest.raises(ValueError):
        rm.start_new_event(now_epoch_ms=999)

    # Blocked attempt must not have changed anything.
    assert rm.get_event_start_epoch_ms() is None
