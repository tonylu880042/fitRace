from hub_server.usecases.race_result_store import RaceResultStore


def test_race_result_store_persists_finished_snapshot_once(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    snapshot = {
        "state": "STOPPED",
        "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {"node-01": {"distance_m": 100}},
    }

    first = store.save_finished_snapshot(snapshot)
    second = store.save_finished_snapshot(snapshot)

    assert first is not None
    assert second is None
    results = store.list_results()
    assert len(results) == 1
    assert results[0]["result_id"] == "1000-2000-distance"
    assert results[0]["snapshot"]["leaderboard"]["node-01"]["distance_m"] == 100


def test_race_result_store_ignores_non_stopped_snapshot(tmp_path):
    store = RaceResultStore(tmp_path / "race_results.jsonl")

    assert store.save_finished_snapshot({"state": "RUNNING"}) is None
    assert store.list_results() == []
