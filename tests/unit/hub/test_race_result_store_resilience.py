"""Regression coverage for the venue incident where an unwritable results
path turned a routine "stop the session" call into an HTTP 500 (see
DEPLOYMENT.md / FITRACE_CLASS_RESULTS_PATH): RaceResultStore must swallow
OS/IO failures when persisting a finished snapshot, not propagate them."""

import os
from pathlib import Path

import pytest

from hub_server.usecases.race_result_store import RaceResultStore


def _make_readonly_dir(tmp_path):
    """Builds a read-only directory to write into and returns it, plus a
    finalizer the caller must invoke to restore the mode so tmp_path cleanup
    can actually remove the tree afterward."""
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    readonly_dir.chmod(0o500)

    def restore():
        readonly_dir.chmod(0o700)

    return readonly_dir, restore


def _skip_if_root_bypasses_permissions():
    # root ignores directory write permission bits entirely, so a
    # read-only-directory test would silently always pass under it and
    # prove nothing about the guard.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip(
            "running as root: chmod does not block writes, test would be a no-op"
        )


def test_save_finished_snapshot_swallows_unwritable_directory(tmp_path):
    _skip_if_root_bypasses_permissions()
    readonly_dir, restore = _make_readonly_dir(tmp_path)
    try:
        store = RaceResultStore(readonly_dir / "nested" / "race_results.jsonl")
        snapshot = {
            "state": "STOPPED",
            "config": {"race_type": "distance"},
            "start_time_epoch_ms": 1000,
            "end_time_epoch_ms": 2000,
            "leaderboard": {"node-01": {"distance_m": 100}},
        }

        result = store.save_finished_snapshot(snapshot)

        assert result is None
    finally:
        restore()


def test_save_finished_snapshot_still_writes_normally_on_a_writable_path(tmp_path):
    """The resilience guard must not have weakened the success path: the
    file must exist with the exact expected contents, not merely "no
    exception was raised"."""
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    snapshot = {
        "state": "STOPPED",
        "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {"node-01": {"distance_m": 100}},
    }

    result = store.save_finished_snapshot(snapshot)

    assert result is not None
    assert result["result_id"] == "1000-2000-distance"
    results = store.list_results()
    assert len(results) == 1
    assert results[0]["result_id"] == "1000-2000-distance"
    assert results[0]["snapshot"] == snapshot


def test_save_finished_snapshot_read_error_preserves_retry_and_dedup(
    tmp_path, monkeypatch, caplog
):
    """A read failure must leave the result eligible for a later retry."""
    path = tmp_path / "race_results.jsonl"
    snapshot = {
        "state": "STOPPED",
        "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {"node-01": {"distance_m": 100}},
    }
    initial_store = RaceResultStore(path)
    assert initial_store.save_finished_snapshot(snapshot) is not None
    store = RaceResultStore(path)
    original_open = Path.open

    def fail_read(path_obj, *args, **kwargs):
        mode = kwargs.get("mode", args[0] if args else "r")
        if path_obj == path and "r" in mode:
            raise PermissionError("results file cannot be read")
        return original_open(path_obj, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_read)

    with caplog.at_level("WARNING", logger="hub_server.race_result_store"):
        assert store.save_finished_snapshot(snapshot) is None

    assert store._saved_keys == set()
    assert "Failed to inspect existing result records" in caplog.text

    monkeypatch.setattr(Path, "open", original_open)
    assert store.save_finished_snapshot(snapshot) is None

    different_snapshot = {**snapshot, "end_time_epoch_ms": 3000}
    saved = store.save_finished_snapshot(different_snapshot)

    assert saved is not None
    results = store.list_results(limit=None)
    assert len(results) == 2
    assert [result["result_id"] for result in results] == [
        "1000-2000-distance",
        "1000-3000-distance",
    ]
