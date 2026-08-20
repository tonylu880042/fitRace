"""In a class, the default label for a station is the machine, not a node id.

A class has no ranking and usually nobody self-registers, so the board falls
back to a default participant name. `Athlete fitrace-edge-01-01` tells a coach
nothing; the BLE name the antenna board reports (`Vmax26_35B`) is printed on
the machine itself and is what the coach is looking at.

A registered athlete always wins over the default, and race mode keeps the
labels it has always had.
"""

from hub_server.domain.class_models import ClassPlan
from hub_server.domain.models import RaceConfig
from hub_server.usecases.race_manager import RaceManager


def _plan(*durations):
    return ClassPlan(segments=[{"kind": "work", "duration_sec": d} for d in durations])


def _telemetry(node_id, equipment_id=None, **extra):
    payload = {
        "node_id": node_id,
        "equipment_type": "spin_bike",
        "power_watts": 150,
        "instantaneous_speed_kph": 25.0,
        "distance_m": 100.0,
        "elapsed_time_ms": 10_000,
        "timestamp_epoch_ms": 1_700_000_000_000,
    }
    if equipment_id is not None:
        payload["equipment_id"] = equipment_id
    payload.update(extra)
    return payload


def _running_class(**kwargs):
    manager = RaceManager(**kwargs)
    manager.configure_class(_plan(600))
    manager.start_race()
    return manager


def test_class_station_defaults_to_the_ble_name_from_telemetry():
    manager = RaceManager()
    manager.configure_class(_plan(600))
    manager.assign_station(1, "fitrace-edge-01-01")
    manager.start_race()

    progress = manager.ingest_telemetry(
        _telemetry("fitrace-edge-01-01", equipment_id="Vmax26_35B")
    )

    assert progress["fitrace-edge-01-01"]["athlete_name"] == "Vmax26_35B"


def test_a_registered_athlete_still_wins_over_the_machine_name():
    manager = RaceManager()
    manager.configure_class(_plan(600))
    manager.assign_station(1, "fitrace-edge-01-01")
    manager.register_athlete(1, "王小明")
    manager.start_race()

    progress = manager.ingest_telemetry(
        _telemetry("fitrace-edge-01-01", equipment_id="Vmax26_35B")
    )

    assert progress["fitrace-edge-01-01"]["athlete_name"] == "王小明"


def test_class_falls_back_to_the_node_id_label_before_any_ble_name_is_known():
    """Telemetry without an equipment_id (or before the first sample that
    carries one) must not produce an empty or None name."""
    manager = _running_class()

    progress = manager.ingest_telemetry(_telemetry("fitrace-edge-01-02"))

    assert progress["fitrace-edge-01-02"]["athlete_name"] == (
        "Athlete fitrace-edge-01-02"
    )


def test_race_mode_keeps_its_own_default_labels():
    """The machine-name default is a class concept only -- a race names
    participants by station or athlete, and saved race results depend on it."""
    manager = RaceManager()
    manager.configure(RaceConfig(race_type="distance", target_value=100.0))
    manager.assign_station(1, "fitrace-edge-01-01")
    manager.start_race()

    progress = manager.ingest_telemetry(
        _telemetry("fitrace-edge-01-01", equipment_id="Vmax26_35B")
    )

    assert progress["fitrace-edge-01-01"]["athlete_name"] == "Station 1"
