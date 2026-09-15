"""Domain-level coverage for "race groups" (Batch 1a): letting ONE race run
several equipment groups at once, each with its own race_type and target
(e.g. treadmills race 800 m while rowers race 500 m and fan bikes race
30 kcal, all from the same start).

This module covers only hub_server/domain/models.py: the new RaceGroup
model, RaceConfig's "mixed" race_type plumbing around it, and the two pure
helper methods (group_index_for/scoped_config) that let every existing
per-race_type usecase stay untouched -- they just get handed a scoped,
plain-individual RaceConfig for the group in question.

RaceManager/readiness/event-engine wiring is covered by later commits in
this batch, not here.
"""

import pytest
from pydantic import ValidationError

from hub_server.domain.models import RaceConfig, RaceGroup

# ---------------------------------------------------------------------------
# RaceGroup validation
# ---------------------------------------------------------------------------


def test_race_group_requires_at_least_one_equipment_type():
    with pytest.raises(ValidationError):
        RaceGroup(equipment_types=[], race_type="distance", target_value=800.0)


def test_race_group_strips_whitespace_from_equipment_types():
    group = RaceGroup(
        equipment_types=["  treadmill  "], race_type="distance", target_value=800.0
    )
    assert group.equipment_types == ["treadmill"]


def test_race_group_rejects_blank_equipment_type():
    with pytest.raises(ValidationError):
        RaceGroup(equipment_types=["   "], race_type="distance", target_value=800.0)


def test_race_group_rejects_unknown_equipment_type():
    with pytest.raises(ValidationError):
        RaceGroup(equipment_types=["unknown"], race_type="distance", target_value=800.0)


def test_race_group_rejects_duplicate_equipment_types_within_group():
    with pytest.raises(ValidationError):
        RaceGroup(
            equipment_types=["treadmill", "treadmill"],
            race_type="distance",
            target_value=800.0,
        )


def test_race_group_rejects_mixed_as_its_own_race_type():
    with pytest.raises(ValidationError):
        RaceGroup(equipment_types=["treadmill"], race_type="mixed", target_value=800.0)


def test_race_group_requires_target_value_for_distance():
    with pytest.raises(ValidationError, match="target_value must be greater than 0"):
        RaceGroup(equipment_types=["treadmill"], race_type="distance", target_value=0.0)


def test_race_group_requires_target_value_for_calories():
    with pytest.raises(ValidationError, match="target_value must be greater than 0"):
        RaceGroup(equipment_types=["fan_bike"], race_type="calories", target_value=0.0)


def test_race_group_requires_duration_for_time():
    with pytest.raises(ValidationError, match="duration_sec must be greater than 0"):
        RaceGroup(equipment_types=["rower"], race_type="time", duration_sec=0)


def test_race_group_requires_duration_for_max_power():
    with pytest.raises(ValidationError, match="duration_sec must be greater than 0"):
        RaceGroup(equipment_types=["bike"], race_type="max_power", duration_sec=0)


def test_race_group_accepts_valid_distance_group():
    group = RaceGroup(
        equipment_types=["treadmill"], race_type="distance", target_value=800.0
    )
    assert group.equipment_types == ["treadmill"]
    assert group.race_type == "distance"
    assert group.target_value == 800.0


# ---------------------------------------------------------------------------
# RaceConfig <-> groups plumbing
# ---------------------------------------------------------------------------


def _group(equipment_types, race_type, target_value=0.0, duration_sec=0):
    return RaceGroup(
        equipment_types=equipment_types,
        race_type=race_type,
        target_value=target_value,
        duration_sec=duration_sec,
    )


def test_mixed_race_requires_at_least_two_groups():
    with pytest.raises(ValidationError, match="at least 2 groups"):
        RaceConfig(
            race_type="mixed",
            groups=[_group(["treadmill"], "distance", target_value=800.0)],
        )


def test_mixed_race_with_two_groups_is_valid():
    config = RaceConfig(
        race_type="mixed",
        groups=[
            _group(["treadmill"], "distance", target_value=800.0),
            _group(["rower"], "distance", target_value=500.0),
        ],
    )
    assert config.race_type == "mixed"
    assert len(config.groups) == 2


def test_non_mixed_race_type_rejects_nonempty_groups():
    with pytest.raises(ValidationError, match="groups"):
        RaceConfig(
            race_type="distance",
            target_value=1000.0,
            groups=[_group(["treadmill"], "distance", target_value=800.0)],
        )


def test_mixed_race_requires_individual_competition_mode():
    with pytest.raises(ValidationError, match="individual"):
        RaceConfig(
            race_type="mixed",
            competition_mode="team",
            groups=[
                _group(["treadmill"], "distance", target_value=800.0),
                _group(["rower"], "distance", target_value=500.0),
            ],
        )


def test_mixed_race_rejects_equipment_type_in_more_than_one_group():
    with pytest.raises(ValidationError, match="more than one group"):
        RaceConfig(
            race_type="mixed",
            groups=[
                _group(["treadmill"], "distance", target_value=800.0),
                _group(["treadmill", "rower"], "distance", target_value=500.0),
            ],
        )


def test_mixed_race_rejects_more_than_eight_groups():
    groups = [_group([f"equip-{i}"], "distance", target_value=100.0) for i in range(9)]
    with pytest.raises(ValidationError):
        RaceConfig(race_type="mixed", groups=groups)


def test_mixed_race_skips_top_level_target_rule():
    # A plain (non-mixed) distance/calories race requires target_value > 0
    # at the top level; a mixed race carries its targets in the groups and
    # must NOT be rejected for target_value == 0 / duration_sec == 0.
    config = RaceConfig(
        race_type="mixed",
        target_value=0.0,
        duration_sec=0,
        groups=[
            _group(["treadmill"], "distance", target_value=800.0),
            _group(["rower"], "time", duration_sec=300),
        ],
    )
    assert config.target_value == 0.0
    assert config.duration_sec == 0


def test_race_config_groups_defaults_to_empty_list():
    config = RaceConfig(race_type="distance", target_value=1000.0)
    assert config.groups == []


# ---------------------------------------------------------------------------
# group_index_for
# ---------------------------------------------------------------------------


def test_group_index_for_returns_matching_group_index():
    config = RaceConfig(
        race_type="mixed",
        groups=[
            _group(["treadmill"], "distance", target_value=800.0),
            _group(["rower"], "distance", target_value=500.0),
        ],
    )
    assert config.group_index_for("treadmill") == 0
    assert config.group_index_for("rower") == 1


def test_group_index_for_returns_none_when_not_mixed():
    config = RaceConfig(race_type="distance", target_value=1000.0)
    assert config.group_index_for("treadmill") is None


def test_group_index_for_returns_none_for_unmatched_equipment_type():
    config = RaceConfig(
        race_type="mixed",
        groups=[
            _group(["treadmill"], "distance", target_value=800.0),
            _group(["rower"], "distance", target_value=500.0),
        ],
    )
    assert config.group_index_for("fan_bike") is None


def test_group_index_for_returns_none_for_none_or_unknown():
    config = RaceConfig(
        race_type="mixed",
        groups=[
            _group(["treadmill"], "distance", target_value=800.0),
            _group(["rower"], "distance", target_value=500.0),
        ],
    )
    assert config.group_index_for(None) is None
    assert config.group_index_for("unknown") is None


# ---------------------------------------------------------------------------
# scoped_config
# ---------------------------------------------------------------------------


def test_scoped_config_carries_the_groups_race_type_and_target():
    config = RaceConfig(
        race_type="mixed",
        groups=[
            _group(["treadmill"], "distance", target_value=800.0),
            _group(["rower"], "time", duration_sec=300),
        ],
    )
    scoped_0 = config.scoped_config(0)
    assert scoped_0.race_type == "distance"
    assert scoped_0.target_value == 800.0
    assert scoped_0.competition_mode == "individual"
    assert scoped_0.groups == []

    scoped_1 = config.scoped_config(1)
    assert scoped_1.race_type == "time"
    assert scoped_1.duration_sec == 300
