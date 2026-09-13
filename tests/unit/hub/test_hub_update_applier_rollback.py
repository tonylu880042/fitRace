import json

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


def test_healthy_update_writes_marker_before_switch_then_clears_it(tmp_path):
    cache_dir = tmp_path / "cache"
    release_root = tmp_path / "releases"
    current_link = tmp_path / "current"
    previous = _install_previous_release(
        release_root, current_link, "0.1.0", "old release\n"
    )
    _stage_release(cache_dir, "0.2.0", "print('new')\n")

    marker_path = tmp_path / "pending-verify.json"
    observed = {}

    def runner(command):
        # The marker must already be on disk -- with real content -- by the
        # time we restart, proving it was written before the symlink switch
        # (not merely recorded as a command that never touched the fs).
        observed["marker_at_restart"] = json.loads(marker_path.read_text())

    result = apply_hub_update(
        cache_dir=cache_dir,
        release_root=release_root,
        current_link=current_link,
        service_name="fitracestudio-hub.service",
        runner=runner,
        health_check=lambda: True,
        pending_verify_marker_path=marker_path,
    )

    assert observed["marker_at_restart"] == {
        "previous": str(previous),
        "applied": "hub-0.2.0",
        "applied_at_epoch_ms": observed["marker_at_restart"]["applied_at_epoch_ms"],
    }
    assert result["state"] == "applied"
    # The real file must be gone -- not just a key in the returned dict.
    assert not marker_path.exists()


def test_unhealthy_update_rollback_clears_pending_verify_marker(tmp_path):
    cache_dir = tmp_path / "cache"
    release_root = tmp_path / "releases"
    current_link = tmp_path / "current"
    _install_previous_release(release_root, current_link, "0.1.0", "old release\n")
    _stage_release(cache_dir, "0.2.0", "print('broken')\n")

    marker_path = tmp_path / "pending-verify.json"
    result = apply_hub_update(
        cache_dir=cache_dir,
        release_root=release_root,
        current_link=current_link,
        service_name="fitracestudio-hub.service",
        runner=lambda command: None,
        health_check=lambda: False,
        health_check_retries=2,
        sleep=lambda seconds: None,
        pending_verify_marker_path=marker_path,
    )

    assert result["state"] == "rolled_back"
    assert not marker_path.exists()


def test_first_install_marker_has_null_previous_and_is_cleared(tmp_path):
    cache_dir = tmp_path / "cache"
    release_root = tmp_path / "releases"
    current_link = tmp_path / "current"
    _stage_release(cache_dir, "0.2.0", "print('new')\n")

    marker_path = tmp_path / "pending-verify.json"
    observed = {}

    def runner(command):
        observed["marker_at_restart"] = json.loads(marker_path.read_text())

    result = apply_hub_update(
        cache_dir=cache_dir,
        release_root=release_root,
        current_link=current_link,
        service_name="fitracestudio-hub.service",
        runner=runner,
        health_check=lambda: True,
        pending_verify_marker_path=marker_path,
    )

    assert observed["marker_at_restart"]["previous"] is None
    assert result["state"] == "applied"
    assert not marker_path.exists()


def test_unhealthy_update_with_no_previous_release_leaves_marker_pending(tmp_path):
    # There is nothing safe to roll back to, so the applier can't reach a
    # verdict -- the marker must stay on disk for the guard to find later.
    cache_dir = tmp_path / "cache"
    release_root = tmp_path / "releases"
    current_link = tmp_path / "current"
    _stage_release(cache_dir, "0.2.0", "print('broken')\n")

    marker_path = tmp_path / "pending-verify.json"
    result = apply_hub_update(
        cache_dir=cache_dir,
        release_root=release_root,
        current_link=current_link,
        service_name="fitracestudio-hub.service",
        runner=lambda command: None,
        health_check=lambda: False,
        health_check_retries=2,
        sleep=lambda seconds: None,
        pending_verify_marker_path=marker_path,
    )

    assert result["state"] == "failed"
    assert marker_path.exists()
    marker = json.loads(marker_path.read_text())
    assert marker["previous"] is None


def test_default_marker_path_is_next_to_current_link(tmp_path):
    cache_dir = tmp_path / "cache"
    release_root = tmp_path / "releases"
    current_link = tmp_path / "opt" / "current"
    current_link.parent.mkdir(parents=True)
    _stage_release(cache_dir, "0.2.0", "print('new')\n")

    default_marker_path = current_link.parent / "pending-verify.json"
    observed = {}

    def runner(command):
        observed["existed_at_restart"] = default_marker_path.exists()

    apply_hub_update(
        cache_dir=cache_dir,
        release_root=release_root,
        current_link=current_link,
        service_name="fitracestudio-hub.service",
        runner=runner,
        health_check=lambda: True,
    )

    assert observed["existed_at_restart"] is True
    assert not default_marker_path.exists()
