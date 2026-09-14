"""Pure-logic tests for hub_server.usecases.roster: CSV parsing (aliases,
BOM, Chinese headers/values), heat formation (registration order, ignoring
gender, short last heat), absent/requeue transitions, and persistence round
trip via RaceSettingsStore. No FastAPI here -- see test_roster_api.py for
the HTTP surface."""

import pytest

from hub_server.usecases.race_settings_store import RaceSettingsStore
from hub_server.usecases.roster import RosterManager, parse_roster_csv

# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def test_parse_csv_basic_header():
    entries, errors = parse_roster_csv(
        "name,division,team\nAlice,men,Red\nBob,women,Blue\n"
    )
    assert errors == []
    assert [e["name"] for e in entries] == ["Alice", "Bob"]
    assert entries[0]["division"] == "men"
    assert entries[0]["team"] == "Red"
    assert entries[0]["status"] == "pending"
    assert entries[0]["order"] == 0
    assert entries[1]["order"] == 1


def test_parse_csv_strips_leading_bom():
    text = "﻿name,division\nAlice,men\n"
    entries, errors = parse_roster_csv(text)
    assert errors == []
    assert entries[0]["name"] == "Alice"


def test_parse_csv_chinese_headers_and_values():
    text = "姓名,性別,隊名\n小美,女子,紅隊\n小明,男子組,藍隊\n"
    entries, errors = parse_roster_csv(text)
    assert errors == []
    assert entries[0]["name"] == "小美"
    assert entries[0]["division"] == "women"
    assert entries[0]["team"] == "紅隊"
    assert entries[1]["division"] == "men"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("men", "men"),
        ("Male", "men"),
        ("m", "men"),
        ("男", "men"),
        ("男子", "men"),
        ("男子組", "men"),
        ("women", "women"),
        ("Female", "women"),
        ("f", "women"),
        ("女", "women"),
        ("女子", "women"),
        ("女子組", "women"),
        ("", None),
    ],
)
def test_parse_csv_division_aliases(value, expected):
    text = f"name,division\nAlice,{value}\n"
    entries, errors = parse_roster_csv(text)
    assert errors == []
    assert entries[0]["division"] == expected


def test_parse_csv_blank_rows_are_skipped():
    text = "name,division\nAlice,men\n\n,,\nBob,women\n"
    entries, errors = parse_roster_csv(text)
    assert errors == []
    assert [e["name"] for e in entries] == ["Alice", "Bob"]


def test_parse_csv_invalid_division_is_a_row_error_with_line_number():
    text = "name,division\nAlice,men\nBob,unknown\n"
    entries, errors = parse_roster_csv(text)
    assert entries == []
    assert errors == [{"row": 3, "message": "Invalid division: unknown"}]


def test_parse_csv_missing_name_is_a_row_error():
    text = "name,division\n,men\n"
    entries, errors = parse_roster_csv(text)
    assert entries == []
    assert errors == [{"row": 2, "message": "Missing name"}]


def test_parse_csv_name_too_long_is_a_row_error():
    text = "name\n" + ("x" * 81) + "\n"
    entries, errors = parse_roster_csv(text)
    assert entries == []
    assert errors == [{"row": 2, "message": "Name too long (max 80 characters)"}]


def test_parse_csv_missing_name_column_is_an_error():
    text = "division,team\nmen,Red\n"
    entries, errors = parse_roster_csv(text)
    assert entries == []
    assert errors == [{"row": 1, "message": "Missing required column: name"}]


def test_parse_csv_any_row_error_rejects_whole_import_collecting_all_errors():
    text = "name,division\n,men\nBob,bogus\nCarol,women\n"
    entries, errors = parse_roster_csv(text)
    assert entries == []
    assert errors == [
        {"row": 2, "message": "Missing name"},
        {"row": 3, "message": "Invalid division: bogus"},
    ]


# ---------------------------------------------------------------------------
# RosterManager: import, heat formation, absent/requeue, persistence
# ---------------------------------------------------------------------------


def _manager(tmp_path):
    return RosterManager(RaceSettingsStore(tmp_path / "roster.json"))


def test_import_replaces_roster(tmp_path):
    manager = _manager(tmp_path)
    errors = manager.import_csv("name\nAlice\nBob\n")
    assert errors == []
    assert [e["name"] for e in manager.entries()] == ["Alice", "Bob"]

    errors = manager.import_csv("name\nCarol\n")
    assert errors == []
    assert [e["name"] for e in manager.entries()] == ["Carol"]


def test_import_row_error_leaves_existing_roster_intact(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\n")

    errors = manager.import_csv("name,division\nBob,bogus\n")
    assert errors == [{"row": 2, "message": "Invalid division: bogus"}]
    assert [e["name"] for e in manager.entries()] == ["Alice"]


def test_load_next_heat_forms_in_registration_order_ignoring_gender(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name,division\nAlice,women\nBob,men\nCarol,women\nDave,men\n")
    loaded = manager.load_next_heat([1, 2])
    assert [e["name"] for e in loaded] == ["Alice", "Bob"]
    assert {e["station_number"] for e in loaded} == {1, 2}

    statuses = {e["name"]: e["status"] for e in manager.entries()}
    assert statuses["Alice"] == "loaded"
    assert statuses["Bob"] == "loaded"
    assert statuses["Carol"] == "pending"
    assert statuses["Dave"] == "pending"


def test_load_next_heat_moves_previous_loaded_to_done(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\nBob\nCarol\nDave\n")
    manager.load_next_heat([1, 2])
    manager.load_next_heat([1, 2])

    statuses = {e["name"]: e["status"] for e in manager.entries()}
    assert statuses["Alice"] == "done"
    assert statuses["Bob"] == "done"
    assert statuses["Carol"] == "loaded"
    assert statuses["Dave"] == "loaded"


def test_load_next_heat_last_heat_can_be_short(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\nBob\nCarol\n")
    manager.load_next_heat([1, 2])
    loaded = manager.load_next_heat([1, 2])
    assert [e["name"] for e in loaded] == ["Carol"]


def test_load_next_heat_zero_pending_raises(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\n")
    manager.load_next_heat([1])
    with pytest.raises(ValueError, match="roster exhausted"):
        manager.load_next_heat([1])


def test_load_next_heat_zero_pending_leaves_state_unchanged_and_persisted(tmp_path):
    """A raising load_next_heat() must never mutate the loaded->done
    transition in memory without persisting it -- otherwise entries() would
    read "done" while the on-disk file (and a freshly reloaded manager)
    still says "loaded", a silent state divergence."""
    path = tmp_path / "roster.json"
    manager = RosterManager(RaceSettingsStore(path))
    manager.import_csv("name\nAlice\n")
    manager.load_next_heat([1])  # Alice -> loaded
    before = manager.entries()

    with pytest.raises(ValueError, match="roster exhausted"):
        manager.load_next_heat([1])

    assert manager.entries() == before
    assert manager.entries()[0]["status"] == "loaded"

    reloaded = RosterManager(RaceSettingsStore(path))
    assert reloaded.entries() == before


def test_load_next_heat_no_stations_raises(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\n")
    with pytest.raises(ValueError, match="assign stations first"):
        manager.load_next_heat([])


def test_mark_absent_only_from_pending(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\n")
    entry_id = manager.entries()[0]["id"]
    manager.load_next_heat([1])
    with pytest.raises(ValueError):
        manager.mark_absent(entry_id)  # now "loaded", not "pending"


def test_mark_absent_and_requeue_to_end(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\nBob\nCarol\n")
    alice_id = manager.entries()[0]["id"]

    manager.mark_absent(alice_id)
    assert manager.entries()[0]["status"] == "absent"

    manager.requeue(alice_id)
    entries = manager.entries()
    alice = next(e for e in entries if e["id"] == alice_id)
    assert alice["status"] == "pending"
    max_order_of_others = max(e["order"] for e in entries if e["id"] != alice_id)
    assert alice["order"] > max_order_of_others

    # Requeued entry rejoins the back of the queue.
    loaded = manager.load_next_heat([1, 2])
    assert [e["name"] for e in loaded] == ["Bob", "Carol"]


def test_requeue_from_done(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\nBob\nCarol\n")
    alice_id = manager.entries()[0]["id"]
    manager.load_next_heat([1, 2])  # Alice, Bob loaded
    manager.load_next_heat([1])  # Alice, Bob -> done; Carol loaded
    alice = next(e for e in manager.entries() if e["id"] == alice_id)
    assert alice["status"] == "done"

    manager.requeue(alice_id)
    alice = next(e for e in manager.entries() if e["id"] == alice_id)
    assert alice["status"] == "pending"


def test_mark_current_heat_started_sets_flag_on_loaded_entries_only(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\nBob\nCarol\n")
    manager.load_next_heat([1, 2])  # Alice, Bob loaded; Carol pending

    manager.mark_current_heat_started()

    entries = {e["name"]: e for e in manager.entries()}
    assert entries["Alice"]["started"] is True
    assert entries["Bob"]["started"] is True
    assert entries["Carol"].get("started") is not True


def test_mark_current_heat_started_persists(tmp_path):
    path = tmp_path / "roster.json"
    manager = RosterManager(RaceSettingsStore(path))
    manager.import_csv("name\nAlice\n")
    manager.load_next_heat([1])

    manager.mark_current_heat_started()

    reloaded = RosterManager(RaceSettingsStore(path))
    assert reloaded.entries()[0]["started"] is True


def test_mark_current_heat_started_is_a_no_op_when_nothing_loaded(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\n")
    # Nothing loaded yet -- must not raise and must not fabricate an entry.
    manager.mark_current_heat_started()
    assert manager.entries()[0].get("started") is not True


def test_load_next_heat_drops_started_flag_when_moving_loaded_to_done(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\nBob\nCarol\n")
    manager.load_next_heat([1, 2])  # Alice, Bob loaded
    manager.mark_current_heat_started()

    manager.load_next_heat([1])  # Alice, Bob -> done; Carol loaded

    entries = {e["name"]: e for e in manager.entries()}
    assert entries["Alice"]["status"] == "done"
    assert entries["Alice"].get("started") is not True
    assert entries["Bob"]["status"] == "done"
    assert entries["Bob"].get("started") is not True


def test_requeue_clears_started_flag(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\nBob\nCarol\n")
    alice_id = manager.entries()[0]["id"]
    manager.load_next_heat([1, 2])  # Alice, Bob loaded
    manager.mark_current_heat_started()

    # requeue is only valid from absent/done -- move Alice to done first.
    manager.load_next_heat([1])  # Alice, Bob -> done; Carol loaded
    alice = next(e for e in manager.entries() if e["id"] == alice_id)
    assert alice["status"] == "done"

    manager.requeue(alice_id)
    alice = next(e for e in manager.entries() if e["id"] == alice_id)
    assert alice["status"] == "pending"
    assert alice.get("started") is not True


def test_add_walk_in_appends_pending_at_the_end(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\nBob\n")
    manager.add_walk_in("Zoe", "women", "Green")
    entries = manager.entries()
    assert entries[-1]["name"] == "Zoe"
    assert entries[-1]["status"] == "pending"
    assert entries[-1]["division"] == "women"
    assert entries[-1]["team"] == "Green"


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "roster.json"
    manager = _manager(tmp_path)
    manager.import_csv("name,division,team\nAlice,men,Red\n")
    manager.load_next_heat([1])

    reloaded = RosterManager(RaceSettingsStore(path))
    entries = reloaded.entries()
    assert len(entries) == 1
    assert entries[0]["name"] == "Alice"
    assert entries[0]["status"] == "loaded"
    assert entries[0]["station_number"] == 1


def test_summary_current_and_next_heat(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name\nAlice\nBob\nCarol\nDave\n")
    manager.load_next_heat([1, 2])

    summary = manager.summary([1, 2])
    assert summary["heat_size"] == 2
    assert {e["name"] for e in summary["current_heat"]} == {"Alice", "Bob"}
    assert [e["name"] for e in summary["next_heat"]] == ["Carol", "Dave"]


# ---------------------------------------------------------------------------
# RosterManager: relay team heats (load_next_heat_teams)
# ---------------------------------------------------------------------------


def test_load_next_heat_teams_groups_by_team_in_first_pending_order(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv(
        "name,team\n" "Alice,Volt\n" "Bob,Surge\n" "Cara,Volt\n" "Dan,Surge\n"
    )
    loaded = manager.load_next_heat_teams([1, 2], relay_legs=2)
    # Volt's first pending entry (Alice) appears before Surge's (Bob), so
    # Volt is team order 0 -> station 1; Surge is team order 1 -> station 2,
    # even though the entries interleave in the roster.
    assert [team["team"] for team in loaded] == ["Volt", "Surge"]
    assert loaded[0]["station_number"] == 1
    assert loaded[1]["station_number"] == 2
    assert [m["name"] for m in loaded[0]["members"]] == ["Alice", "Cara"]
    assert [m["name"] for m in loaded[1]["members"]] == ["Bob", "Dan"]


def test_load_next_heat_teams_members_keep_roster_order(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name,team\nCara,Volt\nAlice,Volt\n")
    loaded = manager.load_next_heat_teams([1], relay_legs=2)
    assert [m["name"] for m in loaded[0]["members"]] == ["Cara", "Alice"]


def test_load_next_heat_teams_marks_members_loaded_with_team_station(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name,team\nAlice,Volt\nBob,Volt\n")
    manager.load_next_heat_teams([1], relay_legs=2)
    statuses = {
        e["name"]: (e["status"], e["station_number"]) for e in manager.entries()
    }
    assert statuses["Alice"] == ("loaded", 1)
    assert statuses["Bob"] == ("loaded", 1)


def test_load_next_heat_teams_moves_previous_loaded_to_done(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name,team\nAlice,Volt\nBob,Volt\nCara,Surge\nDan,Surge\n")
    manager.load_next_heat_teams([1], relay_legs=2)  # Volt loaded
    manager.load_next_heat_teams([1], relay_legs=2)  # Volt -> done; Surge loaded

    statuses = {e["name"]: e["status"] for e in manager.entries()}
    assert statuses["Alice"] == "done"
    assert statuses["Bob"] == "done"
    assert statuses["Cara"] == "loaded"
    assert statuses["Dan"] == "loaded"


def test_load_next_heat_teams_rejects_teamless_pending_entries_and_changes_nothing(
    tmp_path,
):
    path = tmp_path / "roster.json"
    manager = RosterManager(RaceSettingsStore(path))
    manager.import_csv("name,team\nAlice,Volt\nBob,Volt\nEve,\n")
    before = manager.entries()

    with pytest.raises(ValueError) as exc_info:
        manager.load_next_heat_teams([1], relay_legs=2)
    assert "Eve" in str(exc_info.value)

    assert manager.entries() == before
    reloaded = RosterManager(RaceSettingsStore(path))
    assert reloaded.entries() == before


def test_load_next_heat_teams_rejects_short_team_and_changes_nothing(tmp_path):
    path = tmp_path / "roster.json"
    manager = RosterManager(RaceSettingsStore(path))
    manager.import_csv("name,team\nAlice,Volt\n")
    before = manager.entries()

    with pytest.raises(ValueError) as exc_info:
        manager.load_next_heat_teams([1], relay_legs=2)
    message = str(exc_info.value)
    assert "Volt" in message
    assert "1" in message
    assert "2" in message

    assert manager.entries() == before
    reloaded = RosterManager(RaceSettingsStore(path))
    assert reloaded.entries() == before


def test_load_next_heat_teams_rejects_long_team_and_changes_nothing(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name,team\nAlice,Volt\nBob,Volt\nCara,Volt\n")
    with pytest.raises(ValueError) as exc_info:
        manager.load_next_heat_teams([1], relay_legs=2)
    message = str(exc_info.value)
    assert "Volt" in message
    assert "3" in message
    assert "2" in message


def test_load_next_heat_teams_only_validates_the_next_k_teams(tmp_path):
    """A too-small/too-large team further down the queue than the K teams
    actually being loaded must not block loading -- only the chosen teams'
    sizes are validated."""
    manager = _manager(tmp_path)
    manager.import_csv(
        "name,team\nAlice,Volt\nBob,Volt\nCara,Surge\n"  # Surge only has 1 member
    )
    loaded = manager.load_next_heat_teams([1], relay_legs=2)  # only Volt is chosen
    assert [team["team"] for team in loaded] == ["Volt"]


def test_load_next_heat_teams_exhausted_when_no_pending_teams(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name,team\nAlice,Volt\nBob,Volt\n")
    manager.load_next_heat_teams([1], relay_legs=2)
    with pytest.raises(ValueError, match="roster exhausted"):
        manager.load_next_heat_teams([1], relay_legs=2)


def test_load_next_heat_teams_no_stations_raises(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name,team\nAlice,Volt\nBob,Volt\n")
    with pytest.raises(ValueError, match="assign stations first"):
        manager.load_next_heat_teams([], relay_legs=2)


def test_summary_without_relay_legs_omits_team_keys(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name,team\nAlice,Volt\nBob,Volt\n")
    summary = manager.summary([1])
    assert "current_heat_teams" not in summary
    assert "next_heat_teams" not in summary


def test_summary_with_relay_legs_includes_current_and_next_heat_teams(tmp_path):
    manager = _manager(tmp_path)
    manager.import_csv("name,team\nAlice,Volt\nBob,Volt\nCara,Surge\nDan,Surge\n")
    manager.load_next_heat_teams([1], relay_legs=2)  # Volt loaded on station 1

    summary = manager.summary([1], relay_legs=2)
    assert summary["current_heat_teams"] == [
        {"team": "Volt", "station_number": 1, "members": ["Alice", "Bob"]}
    ]
    assert summary["next_heat_teams"] == [
        {"team": "Surge", "station_number": 1, "members": ["Cara", "Dan"]}
    ]
    assert summary["counts"] == {"pending": 2, "loaded": 2, "done": 0, "absent": 0}
