import subprocess

from edge_node.infrastructure.network.wifi_status import (
    LinuxWifiStatusReader,
    build_wifi_status,
)


def test_build_wifi_status_classifies_good_rssi():
    status = build_wifi_status(interface="wlan0", rssi_dbm=-61, ssid="fitRace26")

    assert status.connected is True
    assert status.ssid == "fitRace26"
    assert status.rssi_dbm == -61
    assert status.quality_level == "good"
    assert status.quality_percent == 78
    assert "suitable" in status.recommendation


def test_linux_wifi_status_reader_parses_iw_link_output():
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                "Connected to 00:11:22:33:44:55 (on wlan0)\n"
                "\tSSID: fitRace26\n"
                "\tfreq: 2437\n"
                "\tsignal: -72 dBm\n"
            ),
            stderr="",
        )

    status = LinuxWifiStatusReader(command_runner=fake_run).read("wlan0")

    assert status.connected is True
    assert status.ssid == "fitRace26"
    assert status.rssi_dbm == -72
    assert status.quality_level == "fair"


def test_linux_wifi_status_reader_reports_disconnected_iw_link():
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="Not connected.\n",
            stderr="",
        )

    status = LinuxWifiStatusReader(command_runner=fake_run).read("wlan0")

    assert status.connected is False
    assert status.rssi_dbm is None
    assert status.quality_level == "unknown"
