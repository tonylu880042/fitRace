from pathlib import Path

import pytest

from hub_server.usecases.hub_update_applier import apply_hub_update


def test_apply_hub_update_switches_current_symlink_and_restarts(tmp_path):
    cache_dir = tmp_path / "cache"
    source = cache_dir / "installed" / "hub-0.1.1"
    source.mkdir(parents=True)
    (source / "hub_server").mkdir()
    (source / "hub_server" / "main.py").write_text("print('new')\n")
    (cache_dir / "active-hub-version").write_text("0.1.1")

    calls = []
    result = apply_hub_update(
        cache_dir=cache_dir,
        release_root=tmp_path / "releases",
        current_link=tmp_path / "current",
        service_name="fitracestudio-hub.service",
        runner=lambda command: calls.append(command),
    )

    target = tmp_path / "releases" / "hub-0.1.1"
    assert result["state"] == "applied"
    assert result["version"] == "0.1.1"
    assert result["release_path"] == str(target)
    assert (target / "hub_server" / "main.py").read_text() == "print('new')\n"
    assert (tmp_path / "current").resolve() == target
    assert calls == [["systemctl", "restart", "fitracestudio-hub.service"]]


def test_apply_hub_update_can_skip_restart(tmp_path):
    cache_dir = tmp_path / "cache"
    source = cache_dir / "installed" / "hub-0.1.1"
    source.mkdir(parents=True)
    (cache_dir / "active-hub-version").write_text("0.1.1")

    calls = []
    result = apply_hub_update(
        cache_dir=cache_dir,
        release_root=tmp_path / "releases",
        current_link=tmp_path / "current",
        service_name="fitracestudio-hub.service",
        restart=False,
        runner=lambda command: calls.append(command),
    )

    assert result["state"] == "applied"
    assert result["service_restart"] == "not_run"
    assert calls == []


def test_apply_hub_update_requires_staged_release(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "active-hub-version").write_text("0.1.1")

    with pytest.raises(FileNotFoundError, match="staged hub release"):
        apply_hub_update(
            cache_dir=cache_dir,
            release_root=tmp_path / "releases",
            current_link=tmp_path / "current",
            service_name="fitracestudio-hub.service",
            restart=False,
        )
