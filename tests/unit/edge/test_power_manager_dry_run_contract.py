from fitrace_common.power_manager import PowerManager


def test_power_manager_restart_service_skips_command_in_dry_run():
    calls = []
    manager = PowerManager(
        target="edge",
        service_name="x.service",
        dry_run=True,
        command_runner=calls.append,
    )

    result = manager.restart_service()

    assert calls == []
    assert result.dry_run is True
    assert result.executed is False


def test_power_manager_restart_service_runs_command_when_dry_run_disabled():
    calls = []
    manager = PowerManager(
        target="edge",
        service_name="x.service",
        dry_run=False,
        command_runner=calls.append,
    )

    result = manager.restart_service()

    assert calls == [["sudo", "systemctl", "restart", "x.service"]]
    assert result.dry_run is False
    assert result.executed is True
