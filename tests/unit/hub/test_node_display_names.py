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
                "display_name": "Node130+Vmax_1183",
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

    assert enriched["fitrace-edge-01-01"]["node_display_name"] == (
        "Node130+Vmax_1183"
    )
    assert "node_display_name" not in progress["fitrace-edge-01-01"]


def test_race_state_display_names_cover_individual_and_team_rows():
    state = {
        "leaderboard": {
            "fitrace-edge-01-01": {"node_id": "fitrace-edge-01-01"}
        },
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
    assert enriched["team_leaderboard"][0]["members"][0][
        "node_display_name"
    ] == "Node130+Vmax_1183"


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
