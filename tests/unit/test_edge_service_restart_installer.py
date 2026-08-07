import subprocess
from pathlib import Path

INSTALLER = (
    Path(__file__).resolve().parents[2]
    / "deploy_update"
    / "systemd"
    / "install-edge-service-restart.sh"
)


def test_edge_service_restart_installer_is_valid_and_strictly_scoped():
    source = INSTALLER.read_text(encoding="utf-8")

    subprocess.run(["bash", "-n", str(INSTALLER)], check=True)
    assert "FITRACE_EDGE_SERVICE_RESTART_ENABLED=1" in source
    assert "/usr/bin/systemctl restart fitracestudio-edge.service" in source
    assert "visudo -cf" in source
    assert "systemctl *" not in source
    assert "FITRACE_POWER_COMMANDS_ENABLED=1" not in source
    assert "systemctl reboot" not in source
    assert "systemctl poweroff" not in source
