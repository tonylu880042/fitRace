import pytest

from fitrace_common.power_manager import PowerActionError, PowerManager


def test_power_manager_defaults_to_dry_run_and_restarts_service_when_idle():
    calls = []
    manager = PowerManager(
        target="hub",
        service_name="fitracestudio-hub.service",
        action_allowed=lambda: True,
        dry_run=True,
        command_runner=calls.append,
    )

    result = manager.restart_service()

    assert result.dry_run is True
    assert result.executed is False
    assert result.command == ["systemctl", "restart", "fitracestudio-hub.service"]
    assert calls == []


def test_power_manager_requires_confirmation_for_shutdown():
    manager = PowerManager(
        target="hub",
        service_name="fitracestudio-hub.service",
        dry_run=True,
    )

    with pytest.raises(PowerActionError, match="SHUTDOWN"):
        manager.shutdown()

    result = manager.shutdown("SHUTDOWN")
    assert result.command == ["systemctl", "poweroff"]


def test_power_manager_blocks_power_actions_when_race_is_not_idle():
    manager = PowerManager(
        target="hub",
        service_name="fitracestudio-hub.service",
        action_allowed=lambda: False,
        blocked_message=lambda: "IDLE",
        dry_run=True,
    )

    with pytest.raises(PowerActionError, match="IDLE"):
        manager.restart_service()

    with pytest.raises(PowerActionError, match="IDLE"):
        manager.reboot("REBOOT")
