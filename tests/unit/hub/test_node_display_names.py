from hub_server.usecases.node_display_names import (
    enrich_progress_display_names,
    enrich_race_state_display_names,
    enrich_station_display_names,
)

NODES = [
    {
        "edge_node_id": "fitrace-edge-01",
        "display_name": "Node130",
        "equipment_streams": [
            {
                "node_id": "fitrace-edge-01-01",
                "equipment_id": "Vmax_1183",
                "display_name": "Node130+Vmax_1183",
            }
        ],
    }
]

# A stream that never got a composed display_name (e.g. it predates the
# hub's own display-name pass) but does carry the BLE name the edge always
# publishes as equipment_id. The fallback chain must land on that BLE name,
# not skip straight past it to the internal plumbing node_id.
NODES_NO_DISPLAY_NAME = [
    {
        "edge_node_id": "fitrace-edge-01",
        "equipment_streams": [
            {
                "node_id": "fitrace-edge-01-01",
                "equipment_id": "Vmax53932",
            }
        ],
    }
]


def test_progress_display_names_are_added_without_mutating_race_progress():
    progress = {
        "fitrace-edge-01-01": {
            "node_id": "fitrace-edge-01-01",
            "progress_percent": 42,
        }
    }

    enriched = enrich_progress_display_names(progress, NODES)

    assert enriched["fitrace-edge-01-01"]["node_display_name"] == ("Node130+Vmax_1183")
    assert "node_display_name" not in progress["fitrace-edge-01-01"]


def test_race_state_display_names_cover_individual_and_team_rows():
    state = {
        "leaderboard": {"fitrace-edge-01-01": {"node_id": "fitrace-edge-01-01"}},
        "team_leaderboard": [
            {
                "team_name": "Team A",
                "members": [{"node_id": "fitrace-edge-01-01"}],
            }
        ],
    }

    enriched = enrich_race_state_display_names(state, NODES)

    assert enriched["leaderboard"]["fitrace-edge-01-01"]["node_display_name"] == (
        "Node130+Vmax_1183"
    )
    assert (
        enriched["team_leaderboard"][0]["members"][0]["node_display_name"]
        == "Node130+Vmax_1183"
    )


def test_station_display_names_cover_assigned_and_unassigned_streams():
    stations = {
        "stations": {
            1: {"node_id": "fitrace-edge-01-01"},
        },
        "unassigned_nodes": ["fitrace-edge-01-01"],
    }

    enriched = enrich_station_display_names(stations, NODES)

    assert enriched["stations"][1]["node_display_name"] == "Node130+Vmax_1183"
    assert enriched["unassigned_node_display_names"] == {
        "fitrace-edge-01-01": "Node130+Vmax_1183"
    }


def test_progress_display_name_falls_back_to_equipment_id_before_node_id():
    progress = {
        "fitrace-edge-01-01": {
            "node_id": "fitrace-edge-01-01",
            "progress_percent": 42,
        }
    }

    enriched = enrich_progress_display_names(progress, NODES_NO_DISPLAY_NAME)

    assert enriched["fitrace-edge-01-01"]["node_display_name"] == "Vmax53932"


def test_race_state_display_name_falls_back_to_equipment_id_before_node_id():
    state = {
        "leaderboard": {"fitrace-edge-01-01": {"node_id": "fitrace-edge-01-01"}},
        "team_leaderboard": [],
    }

    enriched = enrich_race_state_display_names(state, NODES_NO_DISPLAY_NAME)

    assert enriched["leaderboard"]["fitrace-edge-01-01"]["node_display_name"] == (
        "Vmax53932"
    )


def test_station_display_name_falls_back_to_equipment_id_before_node_id():
    stations = {
        "stations": {
            1: {"node_id": "fitrace-edge-01-01"},
        },
        "unassigned_nodes": ["fitrace-edge-01-01"],
    }

    enriched = enrich_station_display_names(stations, NODES_NO_DISPLAY_NAME)

    assert enriched["stations"][1]["node_display_name"] == "Vmax53932"
    assert enriched["unassigned_node_display_names"] == {
        "fitrace-edge-01-01": "Vmax53932"
    }


def test_station_payload_exposes_raw_equipment_id_for_assigned_and_unassigned():
    """The station payload must carry the raw equipment_id (BLE name)
    alongside node_display_name so client-side renderers can apply their own
    display_name -> equipment_id -> node_id chain without guessing at data
    that was never sent to them."""
    stations = {
        "stations": {
            1: {"node_id": "fitrace-edge-01-01"},
        },
        "unassigned_nodes": ["fitrace-edge-01-01"],
    }

    enriched = enrich_station_display_names(stations, NODES)

    assert enriched["stations"][1]["equipment_id"] == "Vmax_1183"
    assert enriched["unassigned_node_equipment_ids"] == {
        "fitrace-edge-01-01": "Vmax_1183"
    }
