"""A relay race lets a team's members take turns on the SAME treadmill
(station), running one leg each. Config-side, this only needs two things
on top of the existing RaceConfig: a "relay" competition_mode and a
relay_legs count (team size, 2..10) that splits the target distance into
equal legs.

This module covers the validation matrix (see
hub_server/domain/models.py::RaceConfig.validate_relay_legs) and the
settings-persistence round trip through RaceManager (see
race_manager.py::_persist_settings/_load_settings, which already
round-trips the whole RaceConfig via model_dump/model_validate -- this
just proves relay_legs survives that trip like every other field).
"""

import pytest
from pydantic import ValidationError

from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager
from hub_server.usecases.race_settings_store import RaceSettingsStore


def test_relay_config_requires_distance_race_type():
    with pytest.raises(
        ValidationError, match="Relay races must use race_type distance"
    ):
        RaceConfig(
            race_type="time", competition_mode="relay", relay_legs=4, duration_sec=60
        )


def test_relay_config_requires_relay_legs():
    with pytest.raises(ValidationError, match="relay_legs is required"):
        RaceConfig(race_type="distance", competition_mode="relay", target_value=1000)


def test_relay_config_rejects_legs_below_minimum():
    with pytest.raises(ValidationError):
        RaceConfig(
            race_type="distance",
            competition_mode="relay",
            relay_legs=1,
            target_value=1000,
        )


def test_relay_config_rejects_legs_above_maximum():
    with pytest.raises(ValidationError):
        RaceConfig(
            race_type="distance",
            competition_mode="relay",
            relay_legs=11,
            target_value=1000,
        )


def test_relay_config_accepts_valid_leg_counts():
    for legs in (2, 4, 10):
        config = RaceConfig(
            race_type="distance",
            competition_mode="relay",
            relay_legs=legs,
            target_value=1000,
        )
        assert config.relay_legs == legs
        assert config.competition_mode == "relay"


def test_non_relay_config_forces_relay_legs_to_none():
    config = RaceConfig(
        race_type="distance",
        competition_mode="individual",
        relay_legs=4,
        target_value=1000,
    )
    assert config.relay_legs is None


def test_team_config_also_forces_relay_legs_to_none():
    config = RaceConfig(
        race_type="distance",
        competition_mode="team",
        relay_legs=4,
        target_value=1000,
    )
    assert config.relay_legs is None


def test_relay_settings_round_trip_through_a_restart(tmp_path):
    store = RaceSettingsStore(tmp_path / "settings.json")
    rm = RaceManager(settings_store=store)
    rm.configure(
        RaceConfig(
            race_type="distance",
            competition_mode="relay",
            relay_legs=4,
            target_value=1000,
        )
    )

    restored = RaceManager(settings_store=RaceSettingsStore(tmp_path / "settings.json"))
    config = restored.get_config()
    assert config.competition_mode == "relay"
    assert config.relay_legs == 4
