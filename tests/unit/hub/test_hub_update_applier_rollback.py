from hub_server.usecases.hub_update_applier import apply_hub_update


def _stage_release(cache_dir, version, marker):
    source = cache_dir / "installed" / f"hub-{version}"
    source.mkdir(parents=True)
    (source / "hub_server").mkdir()
    (source / "hub_server" / "main.py").write_text(marker)
    (cache_dir / "active-hub-version").write_text(version)


def _install_previous_release(release_root, current_link, version, marker):
    """Simulate an already-installed, currently-live release."""
    previous = release_root / f"hub-{version}"
    previous.mkdir(parents=True)
    (previous / "marker.txt").write_text(marker)
    current_link.symlink_to(previous)
    return previous


def test_healthy_update_leaves_new_release_linked_and_returns_existing_keys(tmp_path):
    cache_dir = tmp_path / "cache"
    release_root = tmp_path / "releases"
    current_link = tmp_path / "current"
    _stage_release(cache_dir, "0.2.0", "print('new')\n")

    calls = []
    result = apply_hub_update(
        cache_dir=cache_dir,
        release_root=release_root,
        current_link=current_link,
        service_name="fitracestudio-hub.service",
        runner=lambda command: calls.append(command),
        health_check=lambda: True,
    )

    target = release_root / "hub-0.2.0"
    assert result["state"] == "applied"
    assert result["version"] == "0.2.0"
    assert result["release_path"] == str(target)
    assert result["current_link"] == str(current_link)
    assert result["service_name"] == "fitracestudio-hub.service"
    assert result["service_restart"] == "restarted"
    assert "applied_at_epoch_ms" in result
    assert current_link.resolve() == target
    assert calls == [["systemctl", "restart", "fitracestudio-hub.service"]]


def test_unhealthy_update_rolls_back_symlink_on_disk_and_restarts(tmp_path):
    cache_dir = tmp_path / "cache"
    release_root = tmp_path / "releases"
    current_link = tmp_path / "current"
    previous = _install_previous_release(
        release_root, current_link, "0.1.0", "old release\n"
    )
    _stage_release(cache_dir, "0.2.0", "print('broken')\n")

    calls = []
    result = apply_hub_update(
        cache_dir=cache_dir,
        release_root=release_root,
        current_link=current_link,
        service_name="fitracestudio-hub.service",
        runner=lambda command: calls.append(command),
        health_check=lambda: False,
        health_check_retries=2,
        sleep=lambda seconds: None,
    )

    # The real filesystem symlink must point back at the previous release --
    # not just the recorded systemctl commands (that would pass even if the
    # rollback never touched disk).
    assert current_link.resolve() == previous
    assert result["state"] != "applied"
    assert result["state"] == "rolled_back"
    assert result["rolled_back_to"] == "0.1.0"
    assert calls == [
        ["systemctl", "restart", "fitracestudio-hub.service"],
        ["systemctl", "restart", "fitracestudio-hub.service"],
    ]


def test_unhealthy_update_with_no_previous_release_does_not_report_applied(tmp_path):
    cache_dir = tmp_path / "cache"
    release_root = tmp_path / "releases"
    current_link = tmp_path / "current"
    _stage_release(cache_dir, "0.2.0", "print('broken')\n")

    calls = []
    result = apply_hub_update(
        cache_dir=cache_dir,
        release_root=release_root,
        current_link=current_link,
        service_name="fitracestudio-hub.service",
        runner=lambda command: calls.append(command),
        health_check=lambda: False,
        health_check_retries=2,
        sleep=lambda seconds: None,
    )

    target = release_root / "hub-0.2.0"
    # No previous release existed, so the symlink stays on the (unhealthy)
    # new release -- there is nothing safe to roll back to.
    assert current_link.resolve() == target
    assert result["state"] != "applied"
    assert calls == [["systemctl", "restart", "fitracestudio-hub.service"]]


def test_restart_false_skips_health_check_entirely(tmp_path):
    cache_dir = tmp_path / "cache"
    release_root = tmp_path / "releases"
    current_link = tmp_path / "current"
    _stage_release(cache_dir, "0.2.0", "print('new')\n")

    def explode():
        raise AssertionError("health_check must not be called when restart=False")

    result = apply_hub_update(
        cache_dir=cache_dir,
        release_root=release_root,
        current_link=current_link,
        service_name="fitracestudio-hub.service",
        restart=False,
        runner=lambda command: (_ for _ in ()).throw(
            AssertionError("runner must not be called when restart=False")
        ),
        health_check=explode,
    )

    target = release_root / "hub-0.2.0"
    assert result["state"] == "applied"
    assert result["service_restart"] == "not_run"
    assert current_link.resolve() == target
