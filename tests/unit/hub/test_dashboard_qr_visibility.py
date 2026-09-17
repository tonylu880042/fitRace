"""Operators at some events don't want spectators reaching sign-up or the
Game Admin control page from the venue dashboard QR codes. RaceManager gains
two independent, durable booleans -- signup_qr_visible and admin_qr_visible
-- mirroring exactly how start_countdown_sound_enabled already works: a
field with a getter/setter, persisted via RaceSettingsStore in
_load_settings/_persist_settings, and included in get_state_snapshot().

Both default True so existing installs (and existing race_settings.json
files written before this feature existed) keep showing both QRs.
"""

from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.race_settings_store import RaceSettingsStore


def test_qr_visibility_defaults_true():
    rm = RaceManager()
    assert rm.get_signup_qr_visible() is True
    assert rm.get_admin_qr_visible() is True
    snapshot = rm.get_state_snapshot()
    assert snapshot["signup_qr_visible"] is True
    assert snapshot["admin_qr_visible"] is True


def test_setters_update_independently():
    rm = RaceManager()
    rm.set_signup_qr_visible(False)
    assert rm.get_signup_qr_visible() is False
    assert rm.get_admin_qr_visible() is True

    rm.set_admin_qr_visible(False)
    assert rm.get_signup_qr_visible() is False
    assert rm.get_admin_qr_visible() is False

    rm.set_signup_qr_visible(True)
    assert rm.get_signup_qr_visible() is True
    assert rm.get_admin_qr_visible() is False


def test_qr_visibility_survives_a_new_manager(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    rm = RaceManager(settings_store=store)
    rm.set_signup_qr_visible(False)
    rm.set_admin_qr_visible(False)

    restored = RaceManager(settings_store=RaceSettingsStore(tmp_path / "settings.json"))
    assert restored.get_signup_qr_visible() is False
    assert restored.get_admin_qr_visible() is False


def test_old_settings_file_without_keys_defaults_true(tmp_path):
    path = tmp_path / "settings.json"
    store = RaceSettingsStore(path)
    # Simulate a settings.json written by a hub build before this feature
    # existed: only an unrelated durable setting is present.
    store.save({"leaderboard_display_mode": "classic"})

    rm = RaceManager(settings_store=RaceSettingsStore(path))
    assert rm.get_signup_qr_visible() is True
    assert rm.get_admin_qr_visible() is True
