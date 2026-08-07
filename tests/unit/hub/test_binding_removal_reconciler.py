from hub_server.domain.models import RaceState
from hub_server.usecases.binding_removal_reconciler import (
    stations_to_unassign_for_removed_bindings,
)


def test_removal_event_unassigns_exactly_the_affected_stations():
    stations = {
        1: "fitrace-edge-01-01",
        2: "fitrace-edge-01-02",
        3: "fitrace-edge-02-01",
    }

    result = stations_to_unassign_for_removed_bindings(
        removed_node_ids=["fitrace-edge-01-01", "fitrace-edge-01-02"],
        stations=stations,
        race_state=RaceState.IDLE,
    )

    assert result == [1, 2]
    # Station 3, bound to an unrelated node_id, must be left untouched.
    assert 3 not in result


def test_removal_event_during_running_unassigns_nothing():
    stations = {1: "fitrace-edge-01-01"}

    result = stations_to_unassign_for_removed_bindings(
        removed_node_ids=["fitrace-edge-01-01"],
        stations=stations,
        race_state=RaceState.RUNNING,
    )

    assert result == []


def test_node_id_not_bound_to_any_station_is_a_no_op_not_an_error():
    stations = {1: "fitrace-edge-01-01"}

    result = stations_to_unassign_for_removed_bindings(
        removed_node_ids=["fitrace-edge-99-01"],
        stations=stations,
        race_state=RaceState.READY,
    )

    assert result == []


def test_empty_removed_node_ids_is_a_no_op():
    stations = {1: "fitrace-edge-01-01"}

    result = stations_to_unassign_for_removed_bindings(
        removed_node_ids=[],
        stations=stations,
        race_state=RaceState.IDLE,
    )

    assert result == []


def test_unassigns_in_other_non_running_states_too():
    stations = {1: "fitrace-edge-01-01"}

    for state in (RaceState.IDLE, RaceState.READY, RaceState.STOPPED):
        result = stations_to_unassign_for_removed_bindings(
            removed_node_ids=["fitrace-edge-01-01"],
            stations=stations,
            race_state=state,
        )
        assert result == [1], f"expected unassign in state {state}"
