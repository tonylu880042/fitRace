"""GET /api/system/backup.zip: admin-protected in-memory zip of the data files."""

import io
import re
import zipfile

from fastapi.testclient import TestClient

from hub_server.infrastructure.fastapi.app import app

FILENAME_RE = re.compile(r'^attachment; filename="fitrace-backup-\d{8}-\d{6}\.zip"$')


def _point_at(monkeypatch, tmp_path, create):
    paths = {
        "FITRACE_RACE_RESULTS_PATH": tmp_path / "race_results.jsonl",
        "FITRACE_CLASS_RESULTS_PATH": tmp_path / "class_results.jsonl",
        "FITRACE_RACE_SETTINGS_PATH": tmp_path / "race_settings.json",
        "FITRACE_ROSTER_PATH": tmp_path / "roster.json",
    }
    for env, path in paths.items():
        monkeypatch.setenv(env, str(path))
    contents = {}
    for env, path in paths.items():
        if env in create:
            data = f"content of {path.name}\n".encode() + "王".encode()
            path.write_bytes(data)
            contents[path.name] = data
    return contents


def test_backup_zip_requires_admin_token(monkeypatch, tmp_path):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "s3cret")
    _point_at(monkeypatch, tmp_path, create=set())
    client = TestClient(app)

    assert client.get("/api/system/backup.zip").status_code == 401
    wrong = client.get(
        "/api/system/backup.zip", headers={"X-FitRace-Admin-Token": "nope"}
    )
    assert wrong.status_code == 401


def test_backup_zip_returns_all_existing_files_with_exact_bytes(monkeypatch, tmp_path):
    monkeypatch.setenv("FITRACE_ADMIN_TOKEN", "s3cret")
    expected = _point_at(
        monkeypatch,
        tmp_path,
        create={
            "FITRACE_RACE_RESULTS_PATH",
            "FITRACE_CLASS_RESULTS_PATH",
            "FITRACE_RACE_SETTINGS_PATH",
            "FITRACE_ROSTER_PATH",
        },
    )

    response = TestClient(app).get(
        "/api/system/backup.zip", headers={"X-FitRace-Admin-Token": "s3cret"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert FILENAME_RE.match(response.headers["content-disposition"])
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert sorted(zf.namelist()) == sorted(expected)
        for name, data in expected.items():
            assert zf.read(name) == data


def test_backup_zip_contains_only_existing_files(monkeypatch, tmp_path):
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    expected = _point_at(monkeypatch, tmp_path, create={"FITRACE_RACE_RESULTS_PATH"})

    response = TestClient(app).get("/api/system/backup.zip")

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert zf.namelist() == ["race_results.jsonl"]
        assert zf.read("race_results.jsonl") == expected["race_results.jsonl"]


def test_backup_zip_is_a_valid_empty_zip_when_nothing_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    _point_at(monkeypatch, tmp_path, create=set())

    response = TestClient(app).get("/api/system/backup.zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert zf.namelist() == []


def test_backup_zip_works_without_backup_dir_env(monkeypatch, tmp_path):
    monkeypatch.delenv("FITRACE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("FITRACE_BACKUP_DIR", raising=False)
    expected = _point_at(monkeypatch, tmp_path, create={"FITRACE_ROSTER_PATH"})

    response = TestClient(app).get("/api/system/backup.zip")

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert zf.read("roster.json") == expected["roster.json"]
