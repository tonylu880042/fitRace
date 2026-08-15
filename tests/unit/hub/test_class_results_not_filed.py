from hub_server.usecases.race_result_store import RaceResultStore


def _stopped_snapshot(**overrides):
    snapshot = {
        "state": "STOPPED",
        "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {"node-01": {"distance_m": 100}},
    }
    snapshot.update(overrides)
    return snapshot


def test_class_session_mode_snapshot_is_not_filed_as_a_race_result(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    snapshot = _stopped_snapshot(session_mode="class")

    result = store.save_finished_snapshot(snapshot)

    assert result is None
    assert store.list_results() == []


def test_race_session_mode_snapshot_is_still_filed_as_a_race_result(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    snapshot = _stopped_snapshot(session_mode="race")

    result = store.save_finished_snapshot(snapshot)

    assert result is not None
    results = store.list_results()
    assert len(results) == 1
    assert results[0]["snapshot"]["session_mode"] == "race"


def test_snapshot_with_no_session_mode_key_is_still_filed_as_a_race_result(tmp_path):
    """Backward compatibility: snapshots written before class mode existed
    have no session_mode key at all. Absent must mean "race", not "class" --
    an old-format snapshot must still be saved."""
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    snapshot = _stopped_snapshot()
    assert "session_mode" not in snapshot

    result = store.save_finished_snapshot(snapshot)

    assert result is not None
    results = store.list_results()
    assert len(results) == 1
