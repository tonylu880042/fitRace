"""FITRACE_BACKUP_DIR wiring: finishing a race/class backs up the data files;
unset means nothing is created."""

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi import app as hub_app
from hub_server.infrastructure.fastapi.app import app
from hub_server.usecases.race_result_store import RaceResultStore

from tests.integration.test_results_read_failure import _RunningRace

REPO_ROOT = Path(hub_app.__file__).resolve().parents[3]


def _stores(tmp_path, paths):
    hook = hub_app.build_backup_hook(paths)
    race = RaceResultStore(tmp_path / "race_results.jsonl", on_saved=hook)
    cls = RaceResultStore(
        tmp_path / "class_results.jsonl", session_mode="class", on_saved=hook
    )
    return race, cls


def _stop_race_with(monkeypatch, race_store, class_store):
    monkeypatch.setattr(hub_app, "race_manager", _RunningRace())
    monkeypatch.setattr(hub_app, "race_result_store", race_store)
    monkeypatch.setattr(hub_app, "class_result_store", class_store)
    monkeypatch.setattr(hub_app, "lan_signup_url", lambda: None)

    async def ignore_broadcast(payload):
        return None

    monkeypatch.setattr(hub_app.ws_manager, "broadcast", ignore_broadcast)
    return TestClient(app).post("/api/race/stop")


def test_backup_dir_unset_creates_no_backup_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("FITRACE_BACKUP_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    race, cls = _stores(tmp_path, [tmp_path / "race_results.jsonl"])

    response = _stop_race_with(monkeypatch, race, cls)

    assert response.status_code == 200
    assert [p.name for p in tmp_path.iterdir()] == ["race_results.jsonl"]


def test_backup_dir_set_backs_up_results_after_stop(monkeypatch, tmp_path):
    backup_dir = tmp_path / "usb" / "backups"
    backup_dir.mkdir(parents=True)
    monkeypatch.setenv("FITRACE_BACKUP_DIR", str(backup_dir))
    settings = tmp_path / "race_settings.json"
    settings.write_text('{"a": 1}')
    race, cls = _stores(
        tmp_path,
        [tmp_path / "race_results.jsonl", tmp_path / "class_results.jsonl", settings],
    )

    response = _stop_race_with(monkeypatch, race, cls)

    assert response.status_code == 200
    folders = list(backup_dir.iterdir())
    assert len(folders) == 1
    copied = (folders[0] / "race_results.jsonl").read_text()
    assert json.loads(copied.splitlines()[-1])["result_id"] == "1000-2000-distance"
    assert (folders[0] / "race_settings.json").read_text() == '{"a": 1}'
    assert not (folders[0] / "class_results.jsonl").exists()


def test_hook_is_none_when_env_unset_or_empty(monkeypatch):
    monkeypatch.delenv("FITRACE_BACKUP_DIR", raising=False)
    assert hub_app.build_backup_hook([]) is None
    monkeypatch.setenv("FITRACE_BACKUP_DIR", "")
    assert hub_app.build_backup_hook([]) is None


_SCRIPT = """
from hub_server.infrastructure.fastapi import app as a
snap = {"state": "STOPPED", "config": {"race_type": "distance"},
        "start_time_epoch_ms": 1, "end_time_epoch_ms": 2}
assert a.race_result_store.save_finished_snapshot(snap)
cls = dict(snap, session_mode="class", start_time_epoch_ms=3)
assert a.class_result_store.save_finished_snapshot(cls)
"""


def _run_module_wiring(tmp_path, backup_dir):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("FITRACE_") and k != "TESTING"
    }
    env.update(
        {
            "FITRACE_RACE_RESULTS_PATH": str(tmp_path / "data" / "race_results.jsonl"),
            "FITRACE_RACE_SETTINGS_PATH": str(tmp_path / "data" / "race_settings.json"),
            "FITRACE_ROSTER_PATH": str(tmp_path / "data" / "roster.json"),
            "PYTHONPATH": str(REPO_ROOT),
        }
    )
    if backup_dir is not None:
        env["FITRACE_BACKUP_DIR"] = str(backup_dir)
    return subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_module_level_stores_back_up_both_race_and_class_when_env_set(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "roster.json").write_text("{}")

    proc = _run_module_wiring(tmp_path, backup_dir)

    assert proc.returncode == 0, proc.stderr
    folders = sorted(backup_dir.iterdir())
    assert folders, "no backup folder was created"
    # The class save (second) triggers the last backup and must see both files.
    last = folders[-1]
    assert (last / "race_results.jsonl").exists()
    assert (last / "class_results.jsonl").exists()
    assert (last / "roster.json").read_text() == "{}"


def test_module_level_stores_create_nothing_when_env_unset(tmp_path):
    (tmp_path / "data").mkdir()

    proc = _run_module_wiring(tmp_path, None)

    assert proc.returncode == 0, proc.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data"]
    assert not (tmp_path / "backups").exists()
