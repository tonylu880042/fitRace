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


# -- torn / non-object lines in the results jsonl ---------------------------


def _line(result_id):
    import json

    record = {"result_id": result_id, "snapshot": {"state": "STOPPED"}}
    return json.dumps(record, ensure_ascii=False).encode() + b"\n"


# A power cut mid-append can cut a line inside a multibyte character.
_TORN_LINE = '{"result_id":"x","snapshot":{"n":"'.encode() + "王".encode()[:2] + b"\n"


def _torn_file(tmp_path):
    path = tmp_path / "race_results.jsonl"
    path.write_bytes(_line("a") + _TORN_LINE + _line("b"))
    return path


def _non_object_file(tmp_path):
    path = tmp_path / "race_results.jsonl"
    path.write_bytes(_line("a") + b"123\nnull\n[]\n" + _line("b"))
    return path


def _snapshot(end_ms):
    return {
        "state": "STOPPED",
        "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": end_ms,
        "leaderboard": {"node-01": {"distance_m": 100}},
    }


def test_list_results_skips_a_line_torn_inside_a_multibyte_character(tmp_path):
    store = RaceResultStore(_torn_file(tmp_path))

    results = store.list_results(limit=None)

    assert [r["result_id"] for r in results] == ["a", "b"]


def test_key_exists_survives_a_torn_utf8_line(tmp_path):
    store = RaceResultStore(_torn_file(tmp_path))

    assert store._key_exists("b") is True
    assert store._key_exists("missing") is False


def test_save_finished_snapshot_appends_after_a_torn_utf8_line(tmp_path):
    store = RaceResultStore(_torn_file(tmp_path))

    saved = store.save_finished_snapshot(_snapshot(2000))

    assert saved is not None
    assert saved["result_id"] == "1000-2000-distance"
    ids = [r["result_id"] for r in store.list_results(limit=None)]
    assert ids == ["a", "b", "1000-2000-distance"]


def test_archive_counts_valid_records_around_a_torn_utf8_line(tmp_path):
    from datetime import datetime

    store = RaceResultStore(_torn_file(tmp_path))

    outcome = store.archive(datetime(2026, 1, 1, 0, 0, 0))

    assert outcome["cleared_count"] == 2
    assert outcome["backup_path"] is not None


def test_list_results_skips_non_object_json_lines(tmp_path):
    store = RaceResultStore(_non_object_file(tmp_path))

    results = store.list_results(limit=None)

    assert all(isinstance(r, dict) for r in results)
    assert [r["result_id"] for r in results] == ["a", "b"]


def test_key_exists_skips_non_object_json_lines(tmp_path):
    store = RaceResultStore(_non_object_file(tmp_path))

    assert store._key_exists("b") is True
    assert store._key_exists("missing") is False
