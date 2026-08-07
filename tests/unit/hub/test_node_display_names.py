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


def test_station_display_name_falls_back_to_remembered_ble_name_when_edge_offline():
    """Reproduces the reported defect: the edge has dropped out of the live
    registry entirely (nodes=[]), but the station remembers the BLE name
    from earlier telemetry. The operator must see the bare BLE name, not
    the internal node_id, at exactly the moment the machine looks broken."""
    stations = {
        "stations": {
            1: {
                "node_id": "fitrace-edge-01-01",
                "equipment_type": "rowing_machine",
                "athlete_name": "王小明",
            },
        },
        "unassigned_nodes": [],
        "remembered_equipment_ids": {"fitrace-edge-01-01": "Vmax53932"},
    }

    enriched = enrich_station_display_names(stations, nodes=[])

    assert enriched["stations"][1]["node_display_name"] == "Vmax53932"


def test_station_display_name_prefers_live_prefixed_name_over_remembered():
    """When the edge is online, the Node{octet}+{BLE name} form must win,
    even if a (possibly stale) remembered BLE name is also on file."""
    stations = {
        "stations": {1: {"node_id": "fitrace-edge-01-01"}},
        "unassigned_nodes": [],
        "remembered_equipment_ids": {"fitrace-edge-01-01": "StaleName"},
    }

    enriched = enrich_station_display_names(stations, NODES)

    assert enriched["stations"][1]["node_display_name"] == "Node130+Vmax_1183"


def test_station_display_name_falls_back_to_node_id_when_ble_name_never_seen():
    """Last resort: no live catalog entry and nothing remembered either."""
    stations = {
        "stations": {1: {"node_id": "fitrace-edge-01-01"}},
        "unassigned_nodes": [],
        "remembered_equipment_ids": {},
    }

    enriched = enrich_station_display_names(stations, nodes=[])

    assert enriched["stations"][1]["node_display_name"] == "fitrace-edge-01-01"


def test_unassigned_node_no_longer_shows_when_edge_stops_reporting_even_with_remembered_name():
    """Root-cause fix (Task A): unlike an assigned station, an unassigned
    stream carries no venue setup, so once no edge reports a node_id at
    all any more it must disappear from unassigned_nodes -- even if a BLE
    name was remembered from earlier telemetry. This scenario used to
    assert the OPPOSITE (that the phantom entry stays, using the
    remembered name): that older assertion was itself the bug this task
    fixes, so it has been replaced rather than kept alongside a
    contradictory new one."""
    stations = {
        "stations": {},
        "unassigned_nodes": ["fitrace-edge-01-01"],
        "remembered_equipment_ids": {"fitrace-edge-01-01": "Vmax53932"},
    }

    enriched = enrich_station_display_names(stations, nodes=[])

    assert enriched["unassigned_nodes"] == []
    assert enriched["unassigned_node_display_names"] == {}


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


def test_unassigned_stream_no_longer_reported_by_any_edge_is_dropped():
    """Root-cause regression: a node_id that sent telemetry under an old
    pairing scheme is no longer in ANY edge's live equipment_streams. It
    must not linger as a phantom 'unassigned' entry forever."""
    stations = {
        "stations": {},
        "unassigned_nodes": ["fitrace-edge-01-stale"],
    }
    enriched = enrich_station_display_names(stations, NODES)
    assert enriched["unassigned_nodes"] == []
    assert enriched["unassigned_node_display_names"] == {}
    assert enriched["unassigned_node_equipment_ids"] == {}


def test_unassigned_stream_still_reported_stays_listed():
    stations = {
        "stations": {},
        "unassigned_nodes": ["fitrace-edge-01-01"],
    }
    enriched = enrich_station_display_names(stations, NODES)
    assert enriched["unassigned_nodes"] == ["fitrace-edge-01-01"]
    assert enriched["unassigned_node_display_names"] == {
        "fitrace-edge-01-01": "Node130+Vmax_1183"
    }


def test_assigned_station_unaffected_by_unassigned_filter_when_edge_offline():
    """Pin the highest-risk regression: an ASSIGNED station's node must keep
    showing in `stations` even when its edge is not currently reporting
    (nodes=[]). Only the *unassigned* list is filtered by live streams."""
    stations = {
        "stations": {1: {"node_id": "fitrace-edge-01-01"}},
        "unassigned_nodes": [],
    }
    enriched = enrich_station_display_names(stations, nodes=[])
    assert enriched["stations"][1]["node_id"] == "fitrace-edge-01-01"


def test_assigned_station_unaffected_by_unassigned_filter_when_edge_online():
    """Same station-unassignment-must-not-change guarantee, but with the
    edge online: assigned stations are also unaffected in this case."""
    stations = {
        "stations": {1: {"node_id": "fitrace-edge-01-01"}},
        "unassigned_nodes": [],
    }
    enriched = enrich_station_display_names(stations, NODES)
    assert enriched["stations"][1]["node_id"] == "fitrace-edge-01-01"
    assert enriched["stations"][1]["node_display_name"] == "Node130+Vmax_1183"


def test_live_venue_scenario_six_reported_six_stale_all_assigned():
    """Reproduces the exact live-venue report: six streams currently
    reported by the edge, all six bound to stations 1-6; six MAC-derived
    ids from an older pairing scheme that the edge no longer reports at
    all. unassigned_nodes must end up empty, not showing the stale six."""
    reported = [
        ("fitrace-edge-01-01", "Vmax26_9EE"),
        ("fitrace-edge-01-02", "Vmax26_35B"),
        ("fitrace-edge-01-03", "Vmax26_5E5"),
        ("fitrace-edge-01-04", "Vmax_F341"),
        ("fitrace-edge-01-05", "Vmax_2014"),
        ("fitrace-edge-01-06", "Vmax_E551"),
    ]
    nodes = [
        {
            "edge_node_id": "fitrace-edge-01",
            "equipment_streams": [
                {"node_id": nid, "equipment_id": eq} for nid, eq in reported
            ],
        }
    ]
    stale_ids = [
        "fitrace-edge-01-d58c35fa5e56",
        "fitrace-edge-01-dff929d79eed",
        "fitrace-edge-01-f3031f872014",
        "fitrace-edge-01-d634a6df35b0",
        "fitrace-edge-01-d2c16a98f341",
        "fitrace-edge-01-ca1b69abe551",
    ]
    stations = {
        "stations": {i + 1: {"node_id": nid} for i, (nid, _) in enumerate(reported)},
        "unassigned_nodes": stale_ids,
    }
    enriched = enrich_station_display_names(stations, nodes)
    assert enriched["unassigned_nodes"] == []
