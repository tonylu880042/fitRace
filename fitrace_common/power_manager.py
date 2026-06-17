import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable


class PowerActionError(ValueError):
    pass


@dataclass
class PowerActionResult:
    action: str
    target: str
    dry_run: bool
    command: list[str]
    accepted: bool = True
    executed: bool = False
    timestamp_epoch_ms: int = field(
        default_factory=lambda: int(time.time() * 1000)
    )
    message: str = "accepted"


class PowerManager:
    def __init__(
        self,
        *,
        target: str,
        service_name: str,
        action_allowed: Callable[[], bool] | None = None,
        blocked_message: Callable[[], str] | None = None,
        dry_run: bool | None = None,
        command_runner: Callable[[list[str]], None] | None = None,
    ):
        self._target = target
        self._service_name = service_name
        self._action_allowed = action_allowed or (lambda: True)
        self._blocked_message = blocked_message or (
            lambda: f"Power action is not allowed for {target}"
        )
        self._dry_run = (
            os.getenv("FITRACE_POWER_COMMANDS_ENABLED") != "1"
            if dry_run is None
            else dry_run
        )
        self._command_runner = command_runner or self._run_command

    def status(self) -> dict:
        return {
            "dry_run": self._dry_run,
            "power_actions_allowed": self._action_allowed(),
            "requires_confirmation": ["reboot", "shutdown"],
            "service_name": self._service_name,
        }

    def restart_service(self) -> PowerActionResult:
        return self._execute(
            action="restart-service",
            command=["systemctl", "restart", self._service_name],
        )

    def reboot(self, confirmation: str | None = None) -> PowerActionResult:
        self._require_confirmation(confirmation, expected="REBOOT")
        return self._execute(action="reboot", command=["systemctl", "reboot"])

    def shutdown(self, confirmation: str | None = None) -> PowerActionResult:
        self._require_confirmation(confirmation, expected="SHUTDOWN")
        return self._execute(action="shutdown", command=["systemctl", "poweroff"])

    def _execute(self, *, action: str, command: list[str]) -> PowerActionResult:
        if not self._action_allowed():
            raise PowerActionError(self._blocked_message())

        result = PowerActionResult(
            action=action,
            target=self._target,
            dry_run=self._dry_run,
            command=command,
            executed=not self._dry_run,
            message="dry-run accepted" if self._dry_run else "command executed",
        )

        if not self._dry_run:
            self._command_runner(command)

        return result

    @staticmethod
    def _require_confirmation(confirmation: str | None, *, expected: str):
        if confirmation != expected:
            raise PowerActionError(f"Confirmation must be exactly '{expected}'")

    @staticmethod
    def _run_command(command: list[str]):
        subprocess.run(command, check=True, timeout=15)
