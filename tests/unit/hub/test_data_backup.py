from datetime import datetime

from hub_server.usecases.data_backup import backup_files

NOW = datetime(2026, 10, 8, 14, 30, 5)


def test_copies_existing_files_into_timestamped_folder(tmp_path):
    a = tmp_path / "src" / "race_results.jsonl"
    b = tmp_path / "src" / "race_settings.json"
    a.parent.mkdir()
    a.write_bytes(b'{"x":1}\n\xe7\x8e\x8b\n')
    b.write_bytes(b'{"y":2}')
    dest = tmp_path / "backups"
    dest.mkdir()

    result = backup_files([a, b], dest, NOW)

    assert result == dest / "20261008-143005"
    assert (result / "race_results.jsonl").read_bytes() == a.read_bytes()
    assert (result / "race_settings.json").read_bytes() == b.read_bytes()


def test_missing_source_does_not_stop_the_rest(tmp_path):
    present = tmp_path / "present.json"
    present.write_bytes(b"ok")
    missing = tmp_path / "missing.json"
    dest = tmp_path / "backups"
    dest.mkdir()

    result = backup_files([missing, present], dest, NOW)

    assert result is not None
    assert (result / "present.json").read_bytes() == b"ok"
    assert not (result / "missing.json").exists()


def test_second_backup_in_same_second_keeps_newer_contents(tmp_path):
    src = tmp_path / "r.jsonl"
    dest = tmp_path / "backups"
    dest.mkdir()
    src.write_bytes(b"one\n")
    backup_files([src], dest, NOW)
    src.write_bytes(b"one\ntwo\n")

    result = backup_files([src], dest, NOW)

    assert (result / "r.jsonl").read_bytes() == b"one\ntwo\n"


def test_unwritable_destination_returns_none_without_raising(tmp_path):
    src = tmp_path / "r.jsonl"
    src.write_bytes(b"data")
    blocker = tmp_path / "backups"
    blocker.write_bytes(b"i am a file, not a directory")

    assert backup_files([src], blocker, NOW) is None


def test_missing_backup_root_is_not_created(tmp_path):
    src = tmp_path / "r.jsonl"
    src.write_bytes(b"data")
    unmounted = tmp_path / "usb-not-mounted"

    assert backup_files([src], unmounted, NOW) is None

    assert not unmounted.exists()
