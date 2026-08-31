"""Regression coverage for the venue incident where an unwritable results
path turned a routine "stop the session" call into an HTTP 500 (see
DEPLOYMENT.md / FITRACE_CLASS_RESULTS_PATH): RaceResultStore must swallow
OS/IO failures when persisting a finished snapshot, not propagate them."""

import os

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
