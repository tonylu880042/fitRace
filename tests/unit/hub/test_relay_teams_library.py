"""RelayTeamLibrary (persistent team name + ordered members) and the pure
plan_assignments validator used by POST /api/relay-teams/assign."""

import pytest

from hub_server.usecases.race_settings_store import RaceSettingsStore
from hub_server.usecases.relay_teams import RelayTeamLibrary, plan_assignments


class MemoryStore:
    def __init__(self, data=None):
        self.data = data
        self.saves = 0

    def load(self):
        return self.data

    def save(self, data):
        self.data = data
        self.saves += 1


def _lib():
    return RelayTeamLibrary(MemoryStore())


def test_add_returns_team_with_id_and_listed_in_creation_order():
    lib = _lib()
    a = lib.add("Volt", ["Alice", "Bob"])
    b = lib.add("Amp", ["Cara"])
    assert a["id"] and a["id"] != b["id"]
    assert a["name"] == "Volt" and a["members"] == ["Alice", "Bob"]
    assert [t["name"] for t in lib.teams()] == ["Volt", "Amp"]


def test_name_is_stripped_and_blank_members_dropped():
    lib = _lib()
    team = lib.add("  Volt  ", ["  Alice ", "", "   ", "Bob"])
    assert team["name"] == "Volt"
    assert team["members"] == ["Alice", "Bob"]


@pytest.mark.parametrize("name", ["", "   ", "x" * 81])
def test_bad_name_length_rejected(name):
    with pytest.raises(ValueError):
        _lib().add(name, ["Alice"])


def test_name_of_80_chars_accepted():
    assert _lib().add("x" * 80, ["Alice"])["name"] == "x" * 80


def test_duplicate_name_rejected_case_insensitively():
    lib = _lib()
    lib.add("Volt", ["Alice"])
    with pytest.raises(ValueError):
        lib.add("vOLT", ["Bob"])
    assert len(lib.teams()) == 1


def test_member_count_and_length_bounds():
    lib = _lib()
    with pytest.raises(ValueError):
        lib.add("A", [])
    with pytest.raises(ValueError):
        lib.add("B", ["  ", ""])
    with pytest.raises(ValueError):
        lib.add("C", [f"m{i}" for i in range(11)])
    with pytest.raises(ValueError):
        lib.add("D", ["x" * 81])
    assert lib.add("E", [f"m{i}" for i in range(10)])["members"][-1] == "m9"
    assert lib.add("F", ["x" * 80])["members"] == ["x" * 80]
    assert [t["name"] for t in lib.teams()] == ["E", "F"]


def test_update_changes_team_and_may_keep_own_name():
    lib = _lib()
    a = lib.add("Volt", ["Alice"])
    updated = lib.update(a["id"], "volt", ["Zed", "Yan"])
    assert updated["id"] == a["id"]
    assert updated["name"] == "volt"
    assert lib.teams()[0]["members"] == ["Zed", "Yan"]


def test_update_cannot_take_another_teams_name():
    lib = _lib()
    lib.add("Volt", ["Alice"])
    b = lib.add("Amp", ["Bob"])
    with pytest.raises(ValueError):
        lib.update(b["id"], "VOLT", ["Bob"])
    assert lib.teams()[1]["name"] == "Amp"


def test_update_and_delete_unknown_id():
    lib = _lib()
    with pytest.raises(ValueError, match="team not found"):
        lib.update("nope", "A", ["x"])
    with pytest.raises(ValueError, match="team not found"):
        lib.delete("nope")


def test_delete_removes_team():
    lib = _lib()
    a = lib.add("Volt", ["Alice"])
    lib.add("Amp", ["Bob"])
    lib.delete(a["id"])
    assert [t["name"] for t in lib.teams()] == ["Amp"]


def test_teams_returns_copies():
    lib = _lib()
    lib.add("Volt", ["Alice"])
    lib.teams()[0]["name"] = "hacked"
    lib.teams()[0]["members"].append("hacked")
    assert lib.teams()[0]["name"] == "Volt"
    assert lib.teams()[0]["members"] == ["Alice"]


def test_persists_via_store_and_reloads(tmp_path):
    path = tmp_path / "relay_teams.json"
    lib = RelayTeamLibrary(RaceSettingsStore(path))
    a = lib.add("Volt", ["Alice", "Bob"])
    again = RelayTeamLibrary(RaceSettingsStore(path))
    assert again.teams() == [a]


def test_store_holding_garbage_loads_as_empty():
    assert RelayTeamLibrary(MemoryStore({"teams": "nope"})).teams() == []
    assert RelayTeamLibrary(MemoryStore(None)).teams() == []


def test_rejected_add_does_not_save():
    store = MemoryStore()
    lib = RelayTeamLibrary(store)
    with pytest.raises(ValueError):
        lib.add("", ["x"])
    assert store.saves == 0


# ---- plan_assignments ----

T1 = {"id": "t1", "name": "Volt", "members": ["a", "b"]}
T2 = {"id": "t2", "name": "Amp", "members": ["c", "d"]}
T3 = {"id": "t3", "name": "Trio", "members": ["e", "f", "g"]}


def test_plan_returns_sorted_station_team_pairs():
    plan = plan_assignments([T1, T2], {3: "t2", 1: "t1"}, [1, 2, 3], 2)
    assert plan == [(1, T1), (3, T2)]


def test_plan_none_and_missing_stations_are_left_empty():
    plan = plan_assignments([T1, T2], {1: "t1", 2: None}, [1, 2, 3], 2)
    assert plan == [(1, T1)]
    assert plan_assignments([T1], {}, [1], 2) == []


def test_plan_station_without_node_rejected():
    with pytest.raises(ValueError):
        plan_assignments([T1], {9: "t1"}, [1, 2], 2)


def test_plan_unknown_team_rejected():
    with pytest.raises(ValueError):
        plan_assignments([T1], {1: "ghost"}, [1], 2)


def test_plan_same_team_on_two_stations_rejected():
    with pytest.raises(ValueError):
        plan_assignments([T1], {1: "t1", 2: "t1"}, [1, 2], 2)


def test_plan_wrong_member_count_rejected_naming_the_team():
    with pytest.raises(ValueError, match="Trio"):
        plan_assignments([T1, T3], {1: "t1", 2: "t3"}, [1, 2], 2)


def test_plan_error_even_when_bad_entry_comes_after_valid_ones():
    with pytest.raises(ValueError):
        plan_assignments([T1, T2], {1: "t1", 2: "t2", 3: "ghost"}, [1, 2, 3], 2)
