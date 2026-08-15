"""Tests for RaceResultStore's session_mode parameterisation (commit 2 of
class history), and for GET /api/class/history.

RaceResultStore already persists finished race snapshots to an append-only
jsonl file and filters out training classes. This module covers the new
`session_mode` constructor parameter that lets the SAME class file a second,
independent log for classes instead -- without a second store class, and
without changing the behaviour of any existing race-mode caller (the
parameter defaults to "race").
"""

import json

from hub_server.usecases.race_result_store import RaceResultStore

_CLASS_SNAPSHOT = {
    "state": "STOPPED",
    "session_mode": "class",
    "start_time_epoch_ms": 5000,
    "end_time_epoch_ms": 65000,
    "class_plan": {"segments": [{"kind": "work", "duration_sec": 60}]},
    "leaderboard": {
        "node-01": {
            "athlete_name": "Alice",
            "station_number": 1,
            "distance_m": 1200.0,
            "calories": 80.0,
            "power_watts": 150,
            "max_power_watts": 220,
            "elapsed_time_ms": 58000,
        },
        "node-02": {
            "athlete_name": None,
            "station_number": 2,
            "distance_m": 900.0,
            "calories": 60.0,
            "power_watts": 100,
            "max_power_watts": 180,
            "elapsed_time_ms": 55000,
        },
    },
}

_RACE_SNAPSHOT = {
    "state": "STOPPED",
    "config": {"race_type": "distance"},
    "start_time_epoch_ms": 1000,
    "end_time_epoch_ms": 2000,
    "leaderboard": {"node-01": {"distance_m": 100}},
}


# ---------------------------------------------------------------------------
# 1. Default constructor argument preserves race-store behaviour exactly.
# ---------------------------------------------------------------------------


def test_default_session_mode_is_race(tmp_path):
    store = RaceResultStore(tmp_path / "results.jsonl")
    assert store.save_finished_snapshot(_RACE_SNAPSHOT) is not None
    assert store.save_finished_snapshot(_CLASS_SNAPSHOT) is None


# ---------------------------------------------------------------------------
# 2. A store parameterised for "class" files classes and rejects races.
# ---------------------------------------------------------------------------


def test_class_mode_store_persists_class_snapshot(tmp_path):
    store = RaceResultStore(tmp_path / "class_results.jsonl", session_mode="class")
    record = store.save_finished_snapshot(_CLASS_SNAPSHOT)
    assert record is not None
    results = store.list_results()
    assert len(results) == 1
    assert results[0]["snapshot"]["session_mode"] == "class"


def test_class_mode_store_rejects_race_snapshot(tmp_path):
    store = RaceResultStore(tmp_path / "class_results.jsonl", session_mode="class")
    assert store.save_finished_snapshot(_RACE_SNAPSHOT) is None
    assert store.list_results() == []


def test_class_mode_store_rejects_snapshot_missing_session_mode_key(tmp_path):
    # A snapshot with no session_mode key at all must mean "race" -- absent
    # must never be silently treated as "class" just because this store
    # happens to be the class one.
    store = RaceResultStore(tmp_path / "class_results.jsonl", session_mode="class")
    legacy_race_snapshot = dict(_RACE_SNAPSHOT)
    legacy_race_snapshot.pop("session_mode", None)
    assert store.save_finished_snapshot(legacy_race_snapshot) is None


# ---------------------------------------------------------------------------
# 3. The two modes write to two independent files -- a class run never ends
# up in the race file and vice versa, even when both stores are live at once
# (mirrors how app.py wires race_result_store and class_result_store).
# ---------------------------------------------------------------------------


def test_race_and_class_stores_are_independent_files(tmp_path):
    race_store = RaceResultStore(tmp_path / "race_results.jsonl")
    class_store = RaceResultStore(
        tmp_path / "class_results.jsonl", session_mode="class"
    )

    race_store.save_finished_snapshot(_RACE_SNAPSHOT)
    race_store.save_finished_snapshot(_CLASS_SNAPSHOT)
    class_store.save_finished_snapshot(_RACE_SNAPSHOT)
    class_store.save_finished_snapshot(_CLASS_SNAPSHOT)

    race_records = race_store.list_results()
    class_records = class_store.list_results()

    assert len(race_records) == 1
    assert race_records[0]["snapshot"]["config"]["race_type"] == "distance"

    assert len(class_records) == 1
    assert class_records[0]["snapshot"]["session_mode"] == "class"


# ---------------------------------------------------------------------------
# 4. Anonymous participants (no athlete name, real station) are stored in
# the raw leaderboard exactly like everyone else -- the store itself does no
# filtering, so this pins that save_finished_snapshot never strips them.
# ---------------------------------------------------------------------------


def test_class_snapshot_keeps_anonymous_participant_in_stored_leaderboard(tmp_path):
    store = RaceResultStore(tmp_path / "class_results.jsonl", session_mode="class")
    record = store.save_finished_snapshot(_CLASS_SNAPSHOT)
    assert record is not None
    stored = json.loads(json.dumps(record))  # round-trip like the real jsonl write
    leaderboard = stored["snapshot"]["leaderboard"]
    assert leaderboard["node-02"]["athlete_name"] is None
    assert leaderboard["node-02"]["station_number"] == 2
    assert leaderboard["node-02"]["distance_m"] == 900.0
