"""Test RaceResultStore.archive() method for clearing and archiving race results."""

from datetime import datetime
from pathlib import Path


from hub_server.usecases.race_result_store import RaceResultStore


def test_archive_missing_file_returns_zero_count_no_backup(tmp_path):
    """archive() on missing file: count 0, backup None, no exception."""
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    now = datetime(2026, 9, 14, 15, 30, 0)

    result = store.archive(now)

    assert result["cleared_count"] == 0
    assert result["backup_path"] is None
    assert not (tmp_path / "race_results.jsonl").exists()


def test_archive_empty_file_returns_zero_count_no_backup(tmp_path):
    """archive() on empty file: count 0, backup None, file unchanged."""
    path = tmp_path / "race_results.jsonl"
    path.write_text("")
    store = RaceResultStore(path)
    now = datetime(2026, 9, 14, 15, 30, 0)

    result = store.archive(now)

    assert result["cleared_count"] == 0
    assert result["backup_path"] is None
    # Empty file is left alone (not deleted, not renamed)
    assert path.exists()
    assert path.read_text() == ""


def test_archive_file_with_blank_lines_only_returns_zero_count(tmp_path):
    """archive() with only blank lines: count 0."""
    path = tmp_path / "race_results.jsonl"
    path.write_text("\n\n  \n")
    store = RaceResultStore(path)
    now = datetime(2026, 9, 14, 15, 30, 0)

    result = store.archive(now)

    assert result["cleared_count"] == 0
    assert result["backup_path"] is None


def test_archive_file_with_invalid_json_lines_returns_zero_count(tmp_path):
    """archive() with only invalid JSON lines: count 0."""
    path = tmp_path / "race_results.jsonl"
    path.write_text("not json\n{ bad json\n")
    store = RaceResultStore(path)
    now = datetime(2026, 9, 14, 15, 30, 0)

    result = store.archive(now)

    assert result["cleared_count"] == 0
    assert result["backup_path"] is None


def test_archive_file_with_valid_records_creates_backup(tmp_path):
    """archive() on file with N valid records: count N, backup exists with original bytes."""
    store = RaceResultStore(tmp_path / "race_results.jsonl")

    # Populate with realistic records using save_finished_snapshot
    snapshot1 = {
        "state": "STOPPED",
        "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {"node-01": {"distance_m": 100}},
    }
    snapshot2 = {
        "state": "STOPPED",
        "config": {"race_type": "time"},
        "start_time_epoch_ms": 3000,
        "end_time_epoch_ms": 4000,
        "leaderboard": {"node-01": {"time_sec": 120}},
    }
    store.save_finished_snapshot(snapshot1)
    store.save_finished_snapshot(snapshot2)

    original_content = store._path.read_bytes()
    now = datetime(2026, 9, 14, 15, 30, 0)

    result = store.archive(now)

    assert result["cleared_count"] == 2
    assert result["backup_path"] is not None

    # Verify backup file exists and has original bytes
    backup_path = Path(result["backup_path"])
    assert backup_path.exists()
    assert backup_path.read_bytes() == original_content

    # Verify original path no longer exists
    assert not store._path.exists()


def test_archive_creates_backup_with_correct_timestamp_naming(tmp_path):
    """archive() creates backup with correct timestamp in filename."""
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    snapshot = {
        "state": "STOPPED",
        "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {"node-01": {"distance_m": 100}},
    }
    store.save_finished_snapshot(snapshot)

    now = datetime(2026, 9, 14, 15, 30, 45)

    result = store.archive(now)

    # Backup path should follow format: <original>.bak-YYYYmmddHHMMSS
    expected_backup = tmp_path / "race_results.jsonl.bak-20260914153045"
    assert Path(result["backup_path"]) == expected_backup
    assert expected_backup.exists()


def test_archive_clears_saved_keys_dedup_cache(tmp_path):
    """archive() clears _saved_keys so subsequent saves don't deduplicate against deleted records."""
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    snapshot = {
        "state": "STOPPED",
        "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {"node-01": {"distance_m": 100}},
    }
    result1 = store.save_finished_snapshot(snapshot)
    assert result1 is not None

    # Verify _saved_keys contains the key
    result_key = result1["result_id"]
    assert result_key in store._saved_keys

    now = datetime(2026, 9, 14, 15, 30, 0)
    store.archive(now)

    # After archive, _saved_keys should be empty
    assert len(store._saved_keys) == 0

    # Now save the same snapshot again - it should succeed, not deduplicate
    result2 = store.save_finished_snapshot(snapshot)
    assert result2 is not None
    assert result2["result_id"] == result_key

    # Verify it's now in the new file
    assert store._path.exists()
    results = store.list_results()
    assert len(results) == 1
    assert results[0]["result_id"] == result_key


def test_archive_list_results_is_empty_after_archive(tmp_path):
    """After archive(), list_results() returns []."""
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    snapshot = {
        "state": "STOPPED",
        "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {"node-01": {"distance_m": 100}},
    }
    store.save_finished_snapshot(snapshot)

    assert len(store.list_results()) == 1

    now = datetime(2026, 9, 14, 15, 30, 0)
    store.archive(now)

    assert store.list_results() == []


def test_archive_followed_by_new_save_creates_new_file(tmp_path):
    """After archive(), saving a new snapshot creates a fresh file at original path."""
    store = RaceResultStore(tmp_path / "race_results.jsonl")
    snapshot1 = {
        "state": "STOPPED",
        "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1000,
        "end_time_epoch_ms": 2000,
        "leaderboard": {"node-01": {"distance_m": 100}},
    }
    store.save_finished_snapshot(snapshot1)

    now = datetime(2026, 9, 14, 15, 30, 0)
    archive_result = store.archive(now)

    # Original path should not exist
    assert not store._path.exists()

    # Save a new snapshot
    snapshot2 = {
        "state": "STOPPED",
        "config": {"race_type": "time"},
        "start_time_epoch_ms": 5000,
        "end_time_epoch_ms": 6000,
        "leaderboard": {"node-02": {"time_sec": 60}},
    }
    result = store.save_finished_snapshot(snapshot2)

    # New snapshot should succeed
    assert result is not None

    # New file should exist and contain only the new record
    assert store._path.exists()
    results = store.list_results()
    assert len(results) == 1
    assert results[0]["result_id"] == "5000-6000-time"

    # Backup should still exist
    assert Path(archive_result["backup_path"]).exists()


def test_archive_with_mixed_valid_and_invalid_lines_counts_only_valid(tmp_path):
    """archive() counts only valid JSON lines, ignoring blank/invalid lines."""
    store = RaceResultStore(tmp_path / "race_results.jsonl")

    # Write a mix of valid and invalid lines
    path = tmp_path / "race_results.jsonl"
    path.write_text(
        '{"result_id":"1","saved_epoch_ms":1000,"snapshot":{}}\n'
        "invalid json line\n"
        "\n"
        '{"result_id":"2","saved_epoch_ms":2000,"snapshot":{}}\n'
        "  \n"
        '{"result_id":"3","saved_epoch_ms":3000,"snapshot":{}}\n'
    )

    # Re-init store to point to the manually-written file
    store = RaceResultStore(path)
    now = datetime(2026, 9, 14, 15, 30, 0)

    result = store.archive(now)

    assert result["cleared_count"] == 3
    assert result["backup_path"] is not None
    assert Path(result["backup_path"]).exists()
