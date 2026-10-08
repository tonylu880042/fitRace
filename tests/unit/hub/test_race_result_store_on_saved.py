"""RaceResultStore.on_saved fires once per NEW persisted record, and a failing
hook can never break ending a race."""

from pathlib import Path

from hub_server.usecases.race_result_store import RaceResultStore


def _snapshot(**overrides):
    snap = {
        "state": "STOPPED",
        "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {"node-01": {"distance_m": 100}},
    }
    snap.update(overrides)
    return snap


def test_on_saved_called_once_for_a_new_record(tmp_path):
    calls = []
    store = RaceResultStore(tmp_path / "r.jsonl", on_saved=lambda: calls.append(1))

    record = store.save_finished_snapshot(_snapshot())

    assert record is not None
    assert calls == [1]


def test_on_saved_runs_after_the_record_is_on_disk(tmp_path):
    path = tmp_path / "r.jsonl"
    seen = []
    store = RaceResultStore(path, on_saved=lambda: seen.append(path.read_text()))

    store.save_finished_snapshot(_snapshot())

    assert len(seen) == 1
    assert "1000-2000-distance" in seen[0]


def test_on_saved_not_called_for_duplicate_key(tmp_path):
    calls = []
    store = RaceResultStore(tmp_path / "r.jsonl", on_saved=lambda: calls.append(1))
    store.save_finished_snapshot(_snapshot())

    assert store.save_finished_snapshot(_snapshot()) is None

    assert calls == [1]


def test_on_saved_not_called_for_duplicate_key_found_on_disk(tmp_path):
    path = tmp_path / "r.jsonl"
    RaceResultStore(path).save_finished_snapshot(_snapshot())
    calls = []
    fresh = RaceResultStore(path, on_saved=lambda: calls.append(1))

    assert fresh.save_finished_snapshot(_snapshot()) is None

    assert calls == []


def test_on_saved_not_called_when_write_raises_oserror(tmp_path, monkeypatch):
    calls = []
    store = RaceResultStore(tmp_path / "r.jsonl", on_saved=lambda: calls.append(1))

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", boom)

    assert store.save_finished_snapshot(_snapshot()) is None
    assert calls == []


def test_on_saved_not_called_for_non_stopped_snapshot(tmp_path):
    calls = []
    store = RaceResultStore(tmp_path / "r.jsonl", on_saved=lambda: calls.append(1))

    assert store.save_finished_snapshot(_snapshot(state="RUNNING")) is None
    assert calls == []


def test_on_saved_not_called_for_other_session_mode(tmp_path):
    calls = []
    store = RaceResultStore(tmp_path / "r.jsonl", on_saved=lambda: calls.append(1))

    assert store.save_finished_snapshot(_snapshot(session_mode="class")) is None
    assert calls == []


def test_failing_on_saved_still_returns_the_record(tmp_path, caplog):
    def bad_hook():
        raise RuntimeError("backup exploded")

    store = RaceResultStore(tmp_path / "r.jsonl", on_saved=bad_hook)

    with caplog.at_level("WARNING"):
        record = store.save_finished_snapshot(_snapshot())

    assert record is not None
    assert record["result_id"] == "1000-2000-distance"
    assert any("backup exploded" in r.getMessage() for r in caplog.records)
    assert len(store.list_results()) == 1


def test_default_store_has_no_hook_and_still_saves(tmp_path):
    store = RaceResultStore(tmp_path / "r.jsonl")
    assert store.save_finished_snapshot(_snapshot()) is not None
